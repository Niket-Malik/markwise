from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time
import base64
import io
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated
from collections import defaultdict
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, create_engine, delete as sql_delete, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from pgvector.sqlalchemy import Vector


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parents[1] / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )
    database_url: str = "sqlite:///./markwise.db"
    secret_key: str = "dev-only-change-me"
    access_token_minutes: int = 30
    cors_origins: str = "http://localhost:3000"
    frontend_host: str = ""
    upload_dir: str = "./uploads"
    storage_backend: str = "local"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_region: str = "us-east-1"
    s3_access_key_id: str = ""
    s3_secret_access_key: str = ""
    s3_server_side_encryption: str = "AES256"
    s3_sse_kms_key_id: str = ""
    ai_provider: str = ""
    ai_api_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str = ""
    ai_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""
    ai_timeout_seconds: float = 30
    ocr_language: str = "eng"


settings = Settings()
logger = logging.getLogger(__name__)


def normalize_database_url(url: str) -> str:
    """Use psycopg 3 and require TLS for remote PostgreSQL connections."""
    if url.startswith("postgres://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgres://")
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
    elif url.startswith("postgresql+psycopg://"):
        pass
    else:
        return url
    parsed = urlsplit(url)
    if (parsed.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1", "db"}:
        query = parse_qsl(parsed.query, keep_blank_values=True)
        sslmodes = [value.lower() for key, value in query if key == "sslmode"]
        if not sslmodes or any(mode not in {"require", "verify-ca", "verify-full"} for mode in sslmodes):
            query = [(key, value) for key, value in query if key != "sslmode"]
            query.append(("sslmode", "require"))
            parsed = parsed._replace(query=urlencode(query))
    return urlunsplit(parsed)


database_url = normalize_database_url(settings.database_url)
connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {"prepare_threshold": None}
pool_args = {"pool_pre_ping": True}
if database_url.startswith("postgresql+"):
    pool_args.update(pool_size=5, max_overflow=5, pool_recycle=300)
engine = create_engine(database_url, connect_args=connect_args, **pool_args)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Organization(Base):
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    external_ai_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    upload_retention_days: Mapped[int] = mapped_column(Integer, default=0)
    review_retention_days: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    email: Mapped[str] = mapped_column(String(320), index=True, unique=True)
    name: Mapped[str] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(24), default="creator")
    password_hash: Mapped[str] = mapped_column(String(256))


class Knowledge(Base):
    __tablename__ = "knowledge"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), default="general")
    severity: Mapped[str] = mapped_column(String(20), default="medium")
    source: Mapped[str] = mapped_column(String(240), default="Admin entry")
    passage: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("knowledge_id", "chunk_index", name="uq_knowledge_chunks_source_index"),
        Index(
            "ix_knowledge_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ).ddl_if(dialect="postgresql"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    knowledge_id: Mapped[int] = mapped_column(ForeignKey("knowledge.id", ondelete="CASCADE"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text)
    channel: Mapped[str] = mapped_column(String(60), default="Website")
    audience: Mapped[str] = mapped_column(String(160), default="")
    goal: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[str] = mapped_column(String(24), default="draft")
    ai_status: Mapped[str] = mapped_column(String(32), default="disabled")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    findings: Mapped[list[Finding]] = relationship(back_populates="review", cascade="all, delete-orphan")


class Finding(Base):
    __tablename__ = "findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    excerpt: Mapped[str] = mapped_column(Text)
    explanation: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(20))
    confidence: Mapped[float]
    citation: Mapped[str] = mapped_column(Text, default="")
    suggestion: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(24), default="open")
    review: Mapped[Review] = relationship(back_populates="findings")


class FindingDecision(Base):
    __tablename__ = "finding_decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    finding_id: Mapped[int] = mapped_column(ForeignKey("findings.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decision: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class StoredAsset(Base):
    __tablename__ = "stored_assets"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    original_name: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(160), default="application/octet-stream")
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReviewVersion(Base):
    __tablename__ = "review_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), index=True)
    review_id: Mapped[int] = mapped_column(ForeignKey("reviews.id"), index=True)
    assigned_to: Mapped[int] = mapped_column(ForeignKey("users.id"))
    assigned_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


@asynccontextmanager
async def lifespan(_app):
    if len(settings.secret_key) < 32 or "replace-with" in settings.secret_key or "dev-only" in settings.secret_key:
        raise RuntimeError("Set SECRET_KEY to a unique random value of at least 32 characters before starting Markwise")
    yield


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Small in-process abuse guard; production edge limits must also be enabled."""

    def __init__(self, app):
        super().__init__(app)
        self._events: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in {"/health", "/docs", "/openapi.json"} or path.startswith("/docs/"):
            return await call_next(request)
        host = request.client.host if request.client else "unknown"
        if path in {"/api/auth/login", "/api/auth/register"}:
            group, maximum, window = path.rsplit("/", 1)[-1], 20, 600
        elif path in {"/api/uploads", "/api/visual-reviews", "/api/knowledge/import"}:
            group, maximum, window = "upload", 20, 60
        else:
            group, maximum, window = "api", 300, 60
        now = time.monotonic()
        key = (host, group)
        with self._lock:
            events = [stamp for stamp in self._events[key] if now - stamp < window]
            if len(events) >= maximum:
                retry_after = max(1, int(window - (now - events[0])))
                self._events[key] = events
                return JSONResponse({"detail": "Too many requests. Try again shortly."}, status_code=429, headers={"Retry-After": str(retry_after)})
            events.append(now)
            self._events[key] = events
            if len(self._events) > 10000:
                self._events = defaultdict(list, {key: values for key, values in self._events.items() if values and now - values[-1] < 3600})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["X-Frame-Options"] = "DENY"
        return response


app = FastAPI(title="Markwise API", version="0.1.0", description="Pre-publication brand governance API", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware)


def configured_cors_origins(origins: str, frontend_host: str = "") -> list[str]:
    allowed = [origin.strip().rstrip("/") for origin in origins.split(",") if origin.strip()]
    host = frontend_host.strip().strip("/")
    if host:
        allowed.append(f"https://{host}")
    return list(dict.fromkeys(allowed))


app.add_middleware(CORSMiddleware, allow_origins=configured_cors_origins(settings.cors_origins, settings.frontend_host), allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["Authorization", "Content-Type"])
bearer = HTTPBearer(auto_error=False)


def db_session():
    with SessionLocal() as session:
        yield session


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}:{digest.hex()}"


def check_password(password: str, stored: str) -> bool:
    try:
        salt, digest = stored.split(":", 1)
        return hmac.compare_digest(hash_password(password, bytes.fromhex(salt)).split(":", 1)[1], digest)
    except (ValueError, TypeError):
        return False


def token_for(user: User) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = json.dumps({"sub": user.id, "org": user.organization_id, "role": user.role, "exp": int(time.time()) + settings.access_token_minutes * 60}, separators=(",", ":")).encode()
    body = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    signing_input = f"{header}.{body}"
    sig = base64.urlsafe_b64encode(hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()).decode().rstrip("=")
    return f"{signing_input}.{sig}"


def current_user(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)], db: Session = Depends(db_session)) -> User:
    if not credentials:
        raise HTTPException(401, "Sign in to continue")
    try:
        header, body, sig = credentials.credentials.split(".", 2)
        signing_input = f"{header}.{body}"
        expected = base64.urlsafe_b64encode(hmac.new(settings.secret_key.encode(), signing_input.encode(), hashlib.sha256).digest()).decode().rstrip("=")
        if header != base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=") or not hmac.compare_digest(sig, expected):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
        if data["exp"] < time.time():
            raise ValueError
        user = db.get(User, int(data["sub"]))
        if not user or user.organization_id != data["org"]:
            raise ValueError
        return user
    except (ValueError, KeyError, json.JSONDecodeError):
        raise HTTPException(401, "Session is invalid or expired")


def roles(*allowed: str):
    def check(user: User = Depends(current_user)):
        if user.role not in allowed:
            raise HTTPException(403, "Your role cannot perform this action")
        return user
    return check


class RegisterIn(BaseModel):
    organization: str = Field(min_length=2, max_length=160)
    name: str = Field(min_length=2, max_length=160)
    email: str
    password: str = Field(min_length=12, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


class KnowledgeIn(BaseModel):
    kind: str = Field(pattern="^(rule|fact|glossary)$")
    title: str = Field(min_length=2, max_length=240)
    body: str = Field(min_length=3, max_length=12000)
    category: str = "general"
    severity: str = "medium"
    source: str = "Admin entry"
    passage: str = ""


class ReviewIn(BaseModel):
    title: str = Field(min_length=2, max_length=240)
    content: str = Field(min_length=3, max_length=50000)
    channel: str = "Website"
    audience: str = ""
    goal: str = ""


class ReviewUpdateIn(BaseModel):
    title: str | None = Field(default=None, min_length=2, max_length=240)
    content: str | None = Field(default=None, min_length=3, max_length=50000)
    channel: str | None = Field(default=None, max_length=60)
    audience: str | None = Field(default=None, max_length=160)
    goal: str | None = Field(default=None, max_length=160)


class TeamMemberIn(BaseModel):
    email: str
    name: str = Field(min_length=2, max_length=160)
    role: str = Field(pattern="^(creator|manager|reviewer|admin)$")
    password: str = Field(min_length=12, max_length=200)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class AssignmentIn(BaseModel):
    user_id: int


class PrivacySettingsIn(BaseModel):
    external_ai_enabled: bool


class RetentionSettingsIn(BaseModel):
    upload_retention_days: int = Field(ge=0, le=3650)
    review_retention_days: int = Field(default=0, ge=0, le=3650)


def audit(db: Session, user: User, action: str, entity: str, entity_id: int):
    db.add(AuditEvent(organization_id=user.organization_id, user_id=user.id, action=action, entity_type=entity, entity_id=entity_id))


def remove_review_records(db: Session, review: Review):
    finding_ids = select(Finding.id).where(Finding.review_id == review.id)
    db.execute(sql_delete(FindingDecision).where(FindingDecision.organization_id == review.organization_id, FindingDecision.finding_id.in_(finding_ids)))
    db.execute(sql_delete(Comment).where(Comment.organization_id == review.organization_id, Comment.review_id == review.id))
    db.execute(sql_delete(ReviewAssignment).where(ReviewAssignment.organization_id == review.organization_id, ReviewAssignment.review_id == review.id))
    db.execute(sql_delete(ReviewVersion).where(ReviewVersion.organization_id == review.organization_id, ReviewVersion.review_id == review.id))
    db.execute(sql_delete(Finding).where(Finding.review_id == review.id))
    db.delete(review)


@app.get("/health")
def health(db: Session = Depends(db_session)):
    db.execute(select(1))
    return {"status": "ok", "database": "connected", "ai_configured": bool(settings.ai_provider and settings.ai_api_key)}


@app.post("/api/auth/register")
def register(data: RegisterIn, db: Session = Depends(db_session)):
    email = data.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "That email is already registered")
    org = Organization(name=data.organization.strip())
    db.add(org); db.flush()
    user = User(organization_id=org.id, email=email, name=data.name.strip(), role="admin", password_hash=hash_password(data.password))
    db.add(user); db.commit(); db.refresh(user)
    return {"access_token": token_for(user), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "organization": org.name}}


@app.post("/api/auth/login")
def login(data: LoginIn, db: Session = Depends(db_session)):
    user = db.scalar(select(User).where(User.email == data.email.strip().lower()))
    if not user or not check_password(data.password, user.password_hash):
        raise HTTPException(401, "Email or password is incorrect")
    org = db.get(Organization, user.organization_id)
    return {"access_token": token_for(user), "token_type": "bearer", "user": {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "organization": org.name}}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(db_session)):
    org = db.get(Organization, user.organization_id)
    return {"id": user.id, "name": user.name, "email": user.email, "role": user.role, "organization": org.name}


@app.get("/api/team")
def list_team(user: User = Depends(current_user), db: Session = Depends(db_session)):
    members = db.scalars(select(User).where(User.organization_id == user.organization_id).order_by(User.name)).all()
    return [{"id": m.id, "name": m.name, "email": m.email, "role": m.role} for m in members]


@app.post("/api/team")
def create_team_member(data: TeamMemberIn, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    email = data.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "That email is already registered")
    member = User(organization_id=user.organization_id, email=email, name=data.name.strip(), role=data.role, password_hash=hash_password(data.password))
    db.add(member); db.flush(); audit(db, user, "team.member_created", "user", member.id); db.commit(); db.refresh(member)
    return {"id": member.id, "name": member.name, "email": member.email, "role": member.role}


@app.get("/api/settings/privacy")
def get_privacy_settings(user: User = Depends(current_user), db: Session = Depends(db_session)):
    organization = db.get(Organization, user.organization_id)
    provider_configured = settings.ai_provider.strip().lower() in {"openai", "openai_compatible"} and bool(settings.ai_api_key)
    semantic_chunks = db.scalar(select(func.count(KnowledgeChunk.id)).where(
        KnowledgeChunk.organization_id == user.organization_id, KnowledgeChunk.embedding.is_not(None)
    )) or 0
    return {
        "external_ai_enabled": organization.external_ai_enabled,
        "provider": settings.ai_provider or None,
        "model": settings.ai_model or None,
        "vision_model": settings.vision_model or settings.ai_model or None,
        "ai_configured": bool(provider_configured and settings.ai_model),
        "vision_configured": bool(provider_configured and (settings.vision_model or settings.ai_model)),
        "embeddings_configured": bool(provider_configured and settings.embedding_model),
        "semantic_chunks": semantic_chunks,
    }


@app.put("/api/settings/privacy")
def update_privacy_settings(data: PrivacySettingsIn, background_tasks: BackgroundTasks, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    organization = db.get(Organization, user.organization_id)
    organization.external_ai_enabled = data.external_ai_enabled
    audit(db, user, "privacy.external_ai_toggled", "organization", organization.id)
    db.commit()
    if not data.external_ai_enabled:
        chunks = db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.organization_id == user.organization_id, KnowledgeChunk.embedding.is_not(None))).all()
        for chunk in chunks:
            chunk.embedding = None
        db.commit()
    elif settings.embedding_model and settings.ai_api_key and settings.ai_provider.strip().lower() in {"openai", "openai_compatible"}:
        background_tasks.add_task(backfill_knowledge_embeddings, user.organization_id)
    result = get_privacy_settings(user, db)
    result["embedding_backfill_queued"] = bool(data.external_ai_enabled and settings.embedding_model and settings.ai_api_key and settings.ai_provider.strip().lower() in {"openai", "openai_compatible"})
    return result


def backfill_knowledge_embeddings(organization_id: int):
    """Fill vectors for sources approved before the workspace opted into embeddings."""
    from .ai import AIProviderError, provider_from_config
    try:
        provider = provider_from_config()
        with SessionLocal() as db:
            organization = db.get(Organization, organization_id)
            if not organization or not organization.external_ai_enabled or not settings.embedding_model:
                return
            chunks = db.scalars(select(KnowledgeChunk).where(
                KnowledgeChunk.organization_id == organization_id,
                KnowledgeChunk.embedding.is_(None),
            ).order_by(KnowledgeChunk.id)).all()
            for offset in range(0, len(chunks), 64):
                if not organization.external_ai_enabled:
                    break
                batch = chunks[offset:offset + 64]
                try:
                    vectors = provider.embed_many([chunk.text for chunk in batch])
                except AIProviderError as exc:
                    logger.warning("Semantic embedding backfill paused (%s)", type(exc).__name__)
                    break
                for chunk, vector in zip(batch, vectors):
                    chunk.embedding = vector
                db.commit()
    except Exception as exc:
        logger.warning("Semantic embedding backfill did not complete (%s)", type(exc).__name__)


@app.get("/api/settings/retention")
def get_retention_settings(user: User = Depends(current_user), db: Session = Depends(db_session)):
    organization = db.get(Organization, user.organization_id)
    return {"upload_retention_days": organization.upload_retention_days, "review_retention_days": organization.review_retention_days}


@app.put("/api/settings/retention")
def set_retention_settings(data: RetentionSettingsIn, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    organization = db.get(Organization, user.organization_id)
    organization.upload_retention_days = data.upload_retention_days
    organization.review_retention_days = data.review_retention_days
    audit(db, user, "retention.updated", "organization", organization.id)
    db.commit()
    return {"upload_retention_days": organization.upload_retention_days, "review_retention_days": organization.review_retention_days}


@app.get("/api/knowledge")
def list_knowledge(user: User = Depends(current_user), db: Session = Depends(db_session)):
    rows = db.scalars(select(Knowledge).where(Knowledge.organization_id == user.organization_id).order_by(Knowledge.created_at.desc())).all()
    return [serialize_knowledge(k) for k in rows]


def serialize_knowledge(k: Knowledge):
    return {"id": k.id, "kind": k.kind, "title": k.title, "body": k.body, "category": k.category, "severity": k.severity, "source": k.source, "passage": k.passage, "status": k.status, "version": k.version}


def chunk_source(text: str, size: int = 900, overlap: int = 100) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    if not normalized:
        return []
    chunks = []
    start = 0
    while start < len(normalized):
        end = min(start + size, len(normalized))
        if end < len(normalized):
            boundary = normalized.rfind(" ", start + size // 2, end)
            if boundary > start:
                end = boundary
        chunks.append(normalized[start:end].strip())
        if end >= len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


@app.post("/api/knowledge")
def create_knowledge(data: KnowledgeIn, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    row = Knowledge(organization_id=user.organization_id, **data.model_dump(), status="pending")
    db.add(row); db.flush(); audit(db, user, "knowledge.created", "knowledge", row.id); db.commit(); db.refresh(row)
    return serialize_knowledge(row)


@app.post("/api/knowledge/import")
async def import_guideline(file: UploadFile = File(...), user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".docx"}:
        raise HTTPException(415, "Guideline imports support PDF and DOCX")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Maximum guideline size is 15 MB")
    try:
        from .document_processing import guideline_candidates, validate_upload
        validate_upload(data, suffix)
        candidates = guideline_candidates(data, Path(file.filename or "guideline").name, suffix, settings.ocr_language)
    except Exception as exc:
        logger.info("Guideline import failed (%s)", type(exc).__name__)
        raise HTTPException(422, "Could not extract guideline text. Check the file and OCR configuration.") from exc
    if not candidates:
        raise HTTPException(422, "No usable guideline passages were extracted")
    asset_id = secrets.token_hex(16)
    from .storage import put_object
    try:
        storage_key = put_object(user.organization_id, asset_id, file.filename or "guideline", data)
    except Exception as exc:
        logger.warning("Guideline archive storage failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Configured upload storage is unavailable") from exc
    asset = StoredAsset(organization_id=user.organization_id, original_name=(file.filename or "guideline")[:255], content_type=file.content_type or "application/octet-stream", storage_key=storage_key, size_bytes=len(data))
    db.add(asset); db.flush()
    rows = [Knowledge(organization_id=user.organization_id, **candidate, status="pending") for candidate in candidates]
    db.add_all(rows); db.flush()
    audit(db, user, "asset.uploaded", "asset", asset.id)
    audit(db, user, "knowledge.guideline_imported", "knowledge", rows[0].id)
    db.commit()
    return {"candidate_count": len(rows), "status": "pending_admin_confirmation", "asset_id": asset.id, "items": [serialize_knowledge(row) for row in rows]}


@app.post("/api/knowledge/{item_id}/approve")
def approve_knowledge(item_id: int, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    row = db.get(Knowledge, item_id)
    if not row or row.organization_id != user.organization_id:
        raise HTTPException(404, "Knowledge item not found")
    if row.status == "active":
        return serialize_knowledge(row)
    organization = db.get(Organization, user.organization_id)
    source_text = "\n".join([row.title, row.body, row.passage])
    provider = None
    if organization.external_ai_enabled and settings.embedding_model:
        try:
            from .ai import provider_from_config
            provider = provider_from_config()
        except Exception as exc:
            logger.info("Source embedding unavailable during confirmation (%s)", type(exc).__name__)
    chunks = chunk_source(source_text)
    embeddings = [None] * len(chunks)
    if provider and chunks:
        try:
            embeddings = provider.embed_many(chunks)
        except Exception as exc:
            logger.warning("Knowledge source remains lexical-only (%s)", type(exc).__name__)
    for index, chunk in enumerate(chunks):
        embedding = embeddings[index]
        db.add(KnowledgeChunk(organization_id=user.organization_id, knowledge_id=row.id, chunk_index=index, text=chunk, embedding=embedding))
    row.status = "active"; audit(db, user, "knowledge.approved", "knowledge", row.id); db.commit()
    return serialize_knowledge(row)


def analyse(content: str, knowledge: list[Knowledge]) -> list[dict]:
    findings = []
    active = [k for k in knowledge if k.status == "active"]
    for rule in active:
        if rule.kind != "rule":
            continue
        # Rules are authored and approved by an administrator; treat the body as a literal restricted term.
        needle = rule.body.strip()
        if len(needle) >= 2:
            for match in re.finditer(re.escape(needle), content, re.IGNORECASE):
                findings.append({"kind": "restricted_term", "excerpt": match.group(0), "explanation": rule.title + ": " + rule.body, "severity": rule.severity, "confidence": 1.0, "citation": f"{rule.source} — {rule.passage or rule.title} (v{rule.version})", "suggestion": "Review this wording against the approved guidance."})
    for claim in re.finditer(r"\b\d+(?:\.\d+)?\s*%", content):
        start = max(content.rfind(mark, 0, claim.start()) for mark in (".", "!", "?", "\n")) + 1
        ends = [content.find(mark, claim.end()) for mark in (".", "!", "?", "\n")]
        end = min((pos for pos in ends if pos >= 0), default=len(content))
        excerpt = content[start:end].strip()
        fact = next((f for f in active if f.kind == "fact" and any(t in excerpt.lower() for t in re.findall(r"[a-z]{4,}", f.title.lower()))), None)
        claimed_percent = re.search(r"(\d+(?:\.\d+)?)\s*%", excerpt)
        approved_percents = re.findall(r"(\d+(?:\.\d+)?)\s*%", fact.body + " " + fact.passage) if fact else []
        if fact:
            if claimed_percent and claimed_percent.group(1) in approved_percents:
                continue
            findings.append({"kind": "claim", "excerpt": excerpt, "explanation": "Compare this quantitative claim with the approved fact before publication.", "severity": "high", "confidence": 0.82, "citation": f"{fact.source} — {fact.passage or fact.title}: {fact.body} (v{fact.version})", "suggestion": fact.body})
        else:
            findings.append({"kind": "cannot_verify", "excerpt": excerpt, "explanation": "No matching approved fact was found. Marking this claim as CANNOT VERIFY instead of guessing.", "severity": "high", "confidence": 0.94, "citation": "No matching active approved fact in this workspace.", "suggestion": "Confirm this figure with an approved source before publication."})
    return findings


def validate_ai_findings(content: str, candidates, retrieved: list[Knowledge], existing: list[dict], image_dimensions: tuple[int, int] | None = None) -> list[dict]:
    """Accept only exact draft spans and citations from the retrieved, active workspace set."""
    allowed = {source.id: source for source in retrieved if source.status == "active"}
    existing_pairs = {(item["kind"], item["excerpt"]) for item in existing}
    findings = []
    for candidate in candidates:
        excerpt = candidate.excerpt
        if excerpt not in content:
            region = re.fullmatch(r"Image region x=(\d+) y=(\d+) width=(\d+) height=(\d+)", excerpt)
            if not image_dimensions or not region:
                continue
            x, y, width, height = map(int, region.groups())
            if width < 1 or height < 1 or x + width > image_dimensions[0] or y + height > image_dimensions[1]:
                continue
        source_ids = set(candidate.cited_source_ids)
        sources = [allowed[source_id] for source_id in source_ids if source_id in allowed]
        has_unretrieved_ids = bool(source_ids - set(allowed))
        if candidate.kind == "claim" and (candidate.cannot_verify or has_unretrieved_ids or not sources or not any(source.kind == "fact" for source in sources)):
            key = ("cannot_verify", excerpt)
            if key not in existing_pairs:
                findings.append({
                    "kind": "cannot_verify", "excerpt": excerpt,
                    "explanation": "No matching approved fact could be validated for this claim. Marked CANNOT VERIFY instead of guessing.",
                    "severity": "high", "confidence": candidate.confidence,
                    "citation": "No matching active approved fact was validated in the retrieved workspace sources.",
                    "suggestion": "Confirm this claim with an approved source before publication.",
                })
                existing_pairs.add(key)
            continue
        if candidate.cannot_verify or has_unretrieved_ids or not sources:
            continue
        if candidate.kind == "claim" and any(source.kind != "fact" for source in sources):
            continue
        finding_kind = "claim" if candidate.kind == "claim" else f"ai_{candidate.kind}"
        key = (finding_kind, excerpt)
        citations = []
        for source in sources:
            passage = source.passage.strip() or source.body.strip()
            citations.append(f"{source.kind.upper()} #{source.id} — {source.source}, {source.title}, v{source.version}: ‘{passage}’")
        suggestion = ""
        rewrite = candidate.suggested_rewrite
        if rewrite and candidate.intent_preserved:
            proposed_numbers = set(re.findall(r"\d+(?:\.\d+)?\s*%?", rewrite))
            supported_numbers = set(re.findall(r"\d+(?:\.\d+)?\s*%?", excerpt))
            for source in sources:
                supported_numbers.update(re.findall(r"\d+(?:\.\d+)?\s*%?", source.body + " " + source.passage))
            if proposed_numbers <= supported_numbers and len(rewrite) <= max(240, len(excerpt) * 4):
                suggestion = rewrite
        if key in existing_pairs:
            prior = next((item for item in existing if item["kind"] == finding_kind and item["excerpt"] == excerpt), None)
            if prior and suggestion:
                prior["suggestion"] = suggestion
            continue
        findings.append({
            "kind": finding_kind,
            "excerpt": excerpt,
            "explanation": candidate.explanation,
            "severity": candidate.severity,
            "confidence": candidate.confidence,
            "citation": "\n".join(citations),
            "suggestion": suggestion,
        })
        existing_pairs.add(key)
    return findings


def analyse_review_content(db: Session, user: User, content: str, channel: str, audience: str, goal: str):
    knowledge = db.scalars(select(Knowledge).where(Knowledge.organization_id == user.organization_id)).all()
    results = analyse(content, knowledge)
    organization = db.get(Organization, user.organization_id)
    ai_status = "disabled"
    if organization.external_ai_enabled:
        from .ai import AIProviderError, provider_from_config
        from .retrieval import retrieve_approved
        try:
            provider = provider_from_config()
            query_embedding = None
            if settings.embedding_model:
                try:
                    query_embedding = provider.embed(content)
                except AIProviderError as exc:
                    logger.info("Semantic query embedding unavailable; using lexical retrieval (%s)", type(exc).__name__)
            retrieved = retrieve_approved(db, user.organization_id, content, query_embedding)
            analysis = provider.analyse(content, {"channel": channel, "audience": audience, "goal": goal}, retrieved)
            results.extend(validate_ai_findings(content, analysis.findings, retrieved, results))
            ai_status = "complete" if retrieved else "no_sources"
        except AIProviderError as exc:
            ai_status = "not_configured" if "Configure AI_PROVIDER" in str(exc) or "not configured" in str(exc) else "error"
            logger.warning("AI analysis did not complete (%s)", type(exc).__name__)
        except Exception as exc:
            ai_status = "error"
            logger.exception("Unexpected AI analysis failure (%s)", type(exc).__name__)
    return results, ai_status


@app.post("/api/reviews")
def create_review(data: ReviewIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    results, ai_status = analyse_review_content(db, user, data.content, data.channel, data.audience, data.goal)
    review = Review(organization_id=user.organization_id, creator_id=user.id, ai_status=ai_status, **data.model_dump())
    db.add(review); db.flush()
    db.add(ReviewVersion(organization_id=user.organization_id, review_id=review.id, user_id=user.id, content=review.content))
    for finding in results:
        db.add(Finding(review_id=review.id, **finding))
    review.status = "needs_review" if any(f["severity"] in {"high", "critical"} for f in results) else "checked"
    audit(db, user, "review.analysed", "review", review.id); db.commit(); db.refresh(review)
    return serialize_review(review)


def serialize_review(r: Review):
    return {"id": r.id, "title": r.title, "content": r.content, "channel": r.channel, "audience": r.audience, "goal": r.goal, "status": r.status, "ai_status": r.ai_status, "assigned_to": r.assigned_to, "created_at": r.created_at.isoformat(), "findings": [{"id": f.id, "kind": f.kind, "excerpt": f.excerpt, "explanation": f.explanation, "severity": f.severity, "confidence": f.confidence, "citation": f.citation, "suggestion": f.suggestion, "status": f.status} for f in r.findings if f.status != "stale"]}


@app.get("/api/reviews")
def list_reviews(user: User = Depends(current_user), db: Session = Depends(db_session)):
    rows = db.scalars(select(Review).where(Review.organization_id == user.organization_id).order_by(Review.created_at.desc())).all()
    return [serialize_review(r) for r in rows]


@app.post("/api/reviews/{review_id}/submit")
def submit_review(review_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    row = db.get(Review, review_id)
    if not row or row.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    if row.creator_id != user.id and user.role not in {"admin", "manager"}:
        raise HTTPException(403, "Only the review creator or a workspace manager can submit this review")
    if row.status in {"approved", "rejected"}:
        raise HTTPException(409, "A decided review cannot be resubmitted")
    row.status = "submitted"; audit(db, user, "review.submitted", "review", row.id); db.commit()
    return serialize_review(row)


@app.post("/api/reviews/{review_id}/assign")
def assign_review(review_id: int, data: AssignmentIn, user: User = Depends(roles("admin", "manager")), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    assignee = db.get(User, data.user_id)
    if not review or review.organization_id != user.organization_id or not assignee or assignee.organization_id != user.organization_id:
        raise HTTPException(404, "Review or team member not found")
    if assignee.role not in {"reviewer", "admin"}:
        raise HTTPException(422, "Assign content reviews to an authorised reviewer or admin")
    if review.status in {"approved", "rejected"}:
        raise HTTPException(409, "A decided review cannot be reassigned")
    review.assigned_to = assignee.id
    assignment = ReviewAssignment(organization_id=user.organization_id, review_id=review.id, assigned_to=assignee.id, assigned_by=user.id)
    db.add(assignment); audit(db, user, "review.assigned", "review", review.id); db.commit()
    return {"review_id": review.id, "assigned_to": assignee.id, "role": assignee.role}


@app.post("/api/reviews/{review_id}/decision")
def review_decision(review_id: int, data: dict, user: User = Depends(roles("admin", "manager", "reviewer")), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    if review.status != "submitted":
        raise HTTPException(409, "Only submitted reviews can receive an approval decision")
    decision = data.get("decision")
    if decision not in {"approved", "rejected"}:
        raise HTTPException(422, "Decision must be approved or rejected")
    high_risk = db.scalar(select(Finding.id).where(Finding.review_id == review.id, Finding.status != "stale", Finding.severity.in_(["high", "critical"])).limit(1)) is not None
    if high_risk and user.role not in {"admin", "reviewer"}:
        raise HTTPException(403, "High-risk content requires an authorised reviewer or admin")
    if user.role == "reviewer" and review.assigned_to != user.id:
        raise HTTPException(403, "A reviewer may decide only reviews assigned to them")
    review.status = decision; audit(db, user, f"review.{decision}", "review", review.id); db.commit()
    return serialize_review(review)


@app.get("/api/reviews/{review_id}/comments")
def list_comments(review_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    comments = db.scalars(select(Comment).where(Comment.organization_id == user.organization_id, Comment.review_id == review_id).order_by(Comment.created_at)).all()
    return [{"id": c.id, "author": db.get(User, c.user_id).name, "body": c.body, "created_at": c.created_at.isoformat()} for c in comments]


@app.post("/api/reviews/{review_id}/comments")
def add_comment(review_id: int, data: CommentIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    comment = Comment(organization_id=user.organization_id, review_id=review.id, user_id=user.id, body=data.body.strip())
    db.add(comment); db.flush(); audit(db, user, "review.comment_added", "review", review.id); db.commit(); db.refresh(comment)
    return {"id": comment.id, "author": user.name, "body": comment.body, "created_at": comment.created_at.isoformat()}


@app.get("/api/reviews/{review_id}/versions")
def list_versions(review_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    versions = db.scalars(select(ReviewVersion).where(ReviewVersion.organization_id == user.organization_id, ReviewVersion.review_id == review_id).order_by(ReviewVersion.created_at, ReviewVersion.id)).all()
    return [{"id": v.id, "author": db.get(User, v.user_id).name, "content": v.content, "created_at": v.created_at.isoformat()} for v in versions]


@app.patch("/api/reviews/{review_id}")
def update_review(review_id: int, data: ReviewUpdateIn, user: User = Depends(current_user), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    if review.status in {"approved", "rejected"}:
        raise HTTPException(409, "A decided review cannot be edited; create a new review version instead")
    if review.creator_id != user.id and user.role not in {"admin", "manager"}:
        raise HTTPException(403, "Only the review creator or a workspace manager can edit this review")
    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return serialize_review(review)
    content_changed = "content" in updates and updates["content"] != review.content
    for key, value in updates.items():
        setattr(review, key, value.strip())
    if content_changed:
        db.add(ReviewVersion(organization_id=user.organization_id, review_id=review.id, user_id=user.id, content=review.content))
        # Preserve prior findings and their reasons for audit; mark them stale and re-check the new version.
        for finding in list(review.findings):
            if finding.status != "stale":
                finding.status = "stale"
        finding_data_list, review.ai_status = analyse_review_content(
            db, user, review.content, review.channel, review.audience, review.goal
        )
        for finding_data in finding_data_list:
            db.add(Finding(review_id=review.id, **finding_data))
        review.status = "needs_review" if any(f["severity"] in {"high", "critical"} for f in finding_data_list) else "checked"
    audit(db, user, "review.edited", "review", review.id)
    db.commit()
    db.refresh(review)
    return serialize_review(review)


@app.delete("/api/reviews/{review_id}")
def delete_review(review_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    review = db.get(Review, review_id)
    if not review or review.organization_id != user.organization_id:
        raise HTTPException(404, "Review not found")
    if review.creator_id != user.id and user.role not in {"admin", "manager"}:
        raise HTTPException(403, "Only the review creator or a workspace manager can delete this review")
    # Retain a minimal audit event, then remove review content, comments, decisions and snapshots.
    audit(db, user, "review.deleted", "review", review.id)
    remove_review_records(db, review); db.commit()
    return {"id": review_id, "deleted": True}


@app.get("/api/audit-events")
def list_audit_events(user: User = Depends(roles("admin", "manager")), db: Session = Depends(db_session), limit: int = 100):
    limit = max(1, min(limit, 500))
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.organization_id == user.organization_id
    ).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [{"id": event.id, "user_id": event.user_id, "action": event.action,
             "entity_type": event.entity_type, "entity_id": event.entity_id,
             "created_at": event.created_at.isoformat()} for event in events]


@app.post("/api/findings/{finding_id}/decision")
def finding_decision(finding_id: int, data: dict, user: User = Depends(current_user), db: Session = Depends(db_session)):
    finding = db.get(Finding, finding_id)
    if not finding or finding.review.organization_id != user.organization_id:
        raise HTTPException(404, "Finding not found")
    if finding.status == "stale":
        raise HTTPException(409, "This finding belongs to an earlier draft version")
    review = finding.review
    if review.status in {"approved", "rejected"}:
        raise HTTPException(409, "A decided review cannot receive further finding decisions")
    is_owner_or_manager = review.creator_id == user.id or user.role in {"admin", "manager"}
    is_assigned_reviewer = user.role == "reviewer" and review.assigned_to == user.id
    if not (is_owner_or_manager or is_assigned_reviewer):
        raise HTTPException(403, "Only the review creator, a manager, or its assigned reviewer can decide findings")
    decision = data.get("decision")
    if decision not in {"accepted", "edited", "dismissed"}:
        raise HTTPException(422, "Unsupported decision")
    reason = data.get("reason", "")
    if decision in {"edited", "dismissed", "rejected"} and (not isinstance(reason, str) or len(reason.strip()) < 3):
        raise HTTPException(422, "Add a short reason for editing, dismissing, or rejecting this finding")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise HTTPException(422, "Decision reason is limited to 2,000 characters")
    finding.status = decision
    if decision == "edited" and isinstance(data.get("suggestion"), str):
        finding.suggestion = data["suggestion"][:12000]
    if decision == "accepted":
        if review.channel == "Image" or finding.kind == "cannot_verify" or not finding.kind.startswith("ai_") and finding.kind != "claim":
            raise HTTPException(422, "This finding is guidance only and has no applicable text rewrite")
        if not finding.suggestion.strip() or finding.excerpt not in review.content:
            raise HTTPException(422, "There is no validated rewrite span to apply in this draft")
        if review.content.count(finding.excerpt) != 1:
            raise HTTPException(409, "This wording appears more than once; edit the draft and review it again to avoid changing the wrong span")
        span_end = review.content.index(finding.excerpt) + len(finding.excerpt)
        rewrite = finding.suggestion.strip()
        if review.content[span_end:span_end + 1] in {".", "!", "?"} and rewrite[-1:] in {".", "!", "?"}:
            rewrite = rewrite[:-1]
        updated_content = review.content.replace(finding.excerpt, rewrite, 1)
        if updated_content == review.content:
            raise HTTPException(422, "The suggested wording does not change this draft")
        review.content = updated_content
        db.add(ReviewVersion(organization_id=user.organization_id, review_id=review.id, user_id=user.id, content=updated_content))
        for previous in review.findings:
            if previous.status != "stale":
                previous.status = "stale"
        new_findings, review.ai_status = analyse_review_content(db, user, review.content, review.channel, review.audience, review.goal)
        for finding_data in new_findings:
            db.add(Finding(review_id=review.id, **finding_data))
        review.status = "needs_review" if any(item["severity"] in {"high", "critical"} for item in new_findings) else "checked"
    db.add(FindingDecision(organization_id=user.organization_id, finding_id=finding.id, user_id=user.id, decision=decision, reason=reason.strip()))
    audit(db, user, f"finding.{decision}", "finding", finding.id); db.commit()
    return {"id": finding.id, "status": decision, "suggestion": finding.suggestion, "reason": reason.strip(), "content_updated": decision == "accepted"}


@app.get("/api/findings/{finding_id}/decisions")
def finding_decisions(finding_id: int, user: User = Depends(current_user), db: Session = Depends(db_session)):
    finding = db.get(Finding, finding_id)
    if not finding or finding.review.organization_id != user.organization_id:
        raise HTTPException(404, "Finding not found")
    rows = db.scalars(select(FindingDecision).where(
        FindingDecision.organization_id == user.organization_id,
        FindingDecision.finding_id == finding_id,
    ).order_by(FindingDecision.created_at, FindingDecision.id)).all()
    return [{"decision": row.decision, "reason": row.reason, "author": db.get(User, row.user_id).name, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/api/uploads")
async def upload(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(db_session)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".docx"}:
        raise HTTPException(415, "Supported uploads: PDF, DOCX, PNG, JPG, WEBP")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Maximum upload size is 15 MB")
    try:
        from .document_processing import validate_upload
        validate_upload(data, suffix)
    except Exception as exc:
        logger.info("Upload validation failed (%s)", type(exc).__name__)
        raise HTTPException(422, "File content does not match its declared type or exceeds safe processing limits") from exc
    asset_id = secrets.token_hex(16)
    from .storage import put_object
    try:
        storage_key = put_object(user.organization_id, asset_id, file.filename or "upload", data)
    except Exception as exc:
        logger.warning("Upload storage failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Configured upload storage is unavailable") from exc
    asset = StoredAsset(organization_id=user.organization_id, original_name=(file.filename or "upload")[:255], content_type=file.content_type or "application/octet-stream", storage_key=storage_key, size_bytes=len(data))
    db.add(asset); db.flush(); audit(db, user, "asset.uploaded", "asset", asset.id); db.commit()
    extracted = ""
    ocr_pages: list[int] = []
    note = None
    try:
        from .document_processing import extract_upload
        extracted, ocr_pages = extract_upload(data, suffix, settings.ocr_language)
    except Exception as exc:
        extracted = ""
        note = str(exc) if exc.__class__.__name__ == "OCRUnavailable" else "Text extraction was not available for this file."
    return {"id": asset.id, "filename": file.filename, "stored": True, "extracted_text": extracted[:50000], "ocr_pages": ocr_pages, "note": note}


@app.post("/api/visual-reviews")
async def visual_review(
    file: UploadFile = File(...),
    role: str = Form("informative"),
    alt_text: str = Form(""),
    reference_logo: UploadFile | None = File(default=None),
    user: User = Depends(current_user),
    db: Session = Depends(db_session),
):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".webp"} or role not in {"informative", "decorative", "logo", "brand_graphic"}:
        raise HTTPException(415, "Visual review supports PNG/JPG/WEBP assets and informative, decorative, logo, or brand_graphic roles")
    if len(alt_text) > 2000:
        raise HTTPException(422, "Alt text is limited to 2,000 characters")
    data = await file.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Maximum upload size is 15 MB")
    try:
        from .document_processing import validate_upload
        validate_upload(data, suffix)
    except Exception as exc:
        logger.info("Visual upload validation failed (%s)", type(exc).__name__)
        raise HTTPException(422, "File content does not match its declared image type or exceeds safe processing limits") from exc
    reference = await reference_logo.read(5 * 1024 * 1024 + 1) if reference_logo else None
    if reference and len(reference) > 5 * 1024 * 1024:
        raise HTTPException(413, "Reference logo is limited to 5 MB")
    reference_mime = "image/png"
    if reference:
        try:
            from .document_processing import validate_upload
            reference_suffix = Path(reference_logo.filename or "").suffix.lower()
            validate_upload(reference, reference_suffix)
            from PIL import Image
            with Image.open(io.BytesIO(reference)) as reference_image:
                reference_mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}[reference_image.format]
            if role != "logo":
                raise ValueError("A reference logo is only valid for logo comparison")
        except (ImportError, OSError, KeyError, ValueError) as exc:
            raise HTTPException(422, "Reference logo must be a valid PNG, JPEG, or WEBP image") from exc
    try:
        from .visual_analysis import analyse_visual
        deterministic, visual_note, dimensions = analyse_visual(data, role, alt_text, db.scalars(select(Knowledge).where(Knowledge.organization_id == user.organization_id, Knowledge.status == "active")).all(), reference)
        extraction_note = None
        from .document_processing import extract_upload
        try:
            extracted, _ = extract_upload(data, suffix, settings.ocr_language)
        except Exception as exc:
            from .document_processing import OCRUnavailable
            if not isinstance(exc, OCRUnavailable):
                raise
            extracted = ""
            extraction_note = str(exc)
    except Exception as exc:
        logger.info("Visual analysis input failed (%s)", type(exc).__name__)
        raise HTTPException(422, "Could not decode or inspect this image. Check the image and OCR configuration.") from exc
    content = extracted[:50000] or alt_text.strip() or f"Visual asset review: {(file.filename or 'image')[:180]}"
    knowledge = db.scalars(select(Knowledge).where(Knowledge.organization_id == user.organization_id)).all()
    query = f"{role} logo palette colour contrast accessibility alt text {content}"
    from .retrieval import retrieve_approved
    retrieved = retrieve_approved(db, user.organization_id, query)
    ai_status = "disabled"
    ai_findings = []
    organization = db.get(Organization, user.organization_id)
    if organization.external_ai_enabled:
        if not settings.vision_model and not settings.ai_model:
            ai_status = "not_configured"
        elif not retrieved:
            ai_status = "no_sources"
        else:
            try:
                from .ai import AIProviderError, provider_from_config
                provider = provider_from_config()
                mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}[suffix]
                candidates = provider.analyse_image(data, mime, {"role": role, "alt_text": alt_text.strip()}, retrieved, reference, reference_mime)
                ai_findings = validate_ai_findings(content, candidates.findings, retrieved, deterministic, dimensions)
                ai_status = "complete"
            except Exception as exc:
                ai_status = "error"
                logger.warning("Vision model analysis failed (%s)", type(exc).__name__)
    asset_id = secrets.token_hex(16)
    from .storage import put_object
    try:
        storage_key = put_object(user.organization_id, asset_id, file.filename or "visual-asset", data)
    except Exception as exc:
        logger.warning("Visual upload storage failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Configured upload storage is unavailable") from exc
    asset = StoredAsset(organization_id=user.organization_id, original_name=(file.filename or "image")[:255], content_type=file.content_type or "application/octet-stream", storage_key=storage_key, size_bytes=len(data))
    db.add(asset); db.flush()
    review = Review(organization_id=user.organization_id, creator_id=user.id, title=f"Visual review: {(file.filename or 'image')[:200]}", content=content, channel="Image", audience=role, goal=alt_text.strip(), ai_status=ai_status, status="checked")
    db.add(review); db.flush()
    db.add(ReviewVersion(organization_id=user.organization_id, review_id=review.id, user_id=user.id, content=content))
    for finding in deterministic + ai_findings:
        db.add(Finding(review_id=review.id, **finding))
    if any(finding["severity"] in {"high", "critical"} for finding in deterministic + ai_findings):
        review.status = "needs_review"
    audit(db, user, "review.visual_analysed", "review", review.id)
    audit(db, user, "asset.uploaded", "asset", asset.id)
    db.commit(); db.refresh(review)
    response = serialize_review(review)
    response["asset_id"] = asset.id
    response["analysis_note"] = " ".join(note for note in (extraction_note, visual_note) if note) or None
    return response


@app.get("/api/uploads")
def list_uploads(user: User = Depends(current_user), db: Session = Depends(db_session)):
    if user.role != "admin":
        return []
    rows = db.scalars(select(StoredAsset).where(
        StoredAsset.organization_id == user.organization_id
    ).order_by(StoredAsset.created_at.desc(), StoredAsset.id.desc()).limit(500)).all()
    return [{"id": row.id, "filename": row.original_name, "content_type": row.content_type,
             "size_bytes": row.size_bytes, "created_at": row.created_at.isoformat()} for row in rows]


@app.delete("/api/uploads/{asset_id}")
def delete_upload(asset_id: int, user: User = Depends(roles("admin")), db: Session = Depends(db_session)):
    asset = db.get(StoredAsset, asset_id)
    if not asset or asset.organization_id != user.organization_id:
        raise HTTPException(404, "Upload not found")
    from .storage import delete_object
    try:
        delete_object(asset.storage_key)
    except Exception as exc:
        logger.warning("Upload deletion failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Configured upload storage is unavailable; metadata was retained") from exc
    db.delete(asset); audit(db, user, "asset.deleted", "asset", asset_id); db.commit()
    return {"id": asset_id, "deleted": True}


@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(db_session)):
    reviews = db.scalars(select(Review).where(Review.organization_id == user.organization_id)).all()
    knowledge = db.scalars(select(Knowledge).where(Knowledge.organization_id == user.organization_id)).all()
    all_findings = [f for r in reviews for f in r.findings if f.status != "stale"]
    counts = {}
    decisions = {"accepted": 0, "edited": 0, "dismissed": 0, "open": sum(f.status == "open" for f in all_findings)}
    contested = {}
    for f in all_findings:
        counts[f.kind] = counts.get(f.kind, 0) + 1
    decision_rows = db.scalars(select(FindingDecision).where(FindingDecision.organization_id == user.organization_id)).all()
    for row in decision_rows:
        if row.decision in {"accepted", "edited", "dismissed"}:
            decisions[row.decision] += 1
        if row.decision in {"dismissed", "edited"}:
            finding = db.get(Finding, row.finding_id)
            if finding:
                for kind, source_id in re.findall(r"\b(RULE|FACT) #(\d+)\b", finding.citation):
                    if kind == "RULE":
                        contested[int(source_id)] = contested.get(int(source_id), 0) + 1
    gaps = {}
    for finding in all_findings:
        if finding.kind == "cannot_verify":
            gaps[finding.kind] = gaps.get(finding.kind, 0) + 1
    guidance_gaps = [{
        "type": "unsupported_claims",
        "count": count,
        "message": "Claims could not be matched to an approved fact. Add or confirm a source before relying on similar claims.",
    } for _, count in sorted(gaps.items(), key=lambda item: -item[1])]
    source_by_id = {source.id: source for source in knowledge}
    contested_rules = [{"id": source_id, "title": source_by_id[source_id].title, "dismissals_or_overrides": count}
                       for source_id, count in sorted(contested.items(), key=lambda item: -item[1]) if source_id in source_by_id]
    return {"reviews": len(reviews), "needs_review": sum(r.status in {"needs_review", "submitted"} for r in reviews), "active_knowledge": sum(k.status == "active" for k in knowledge), "pending_knowledge": sum(k.status == "pending" for k in knowledge), "findings": len(all_findings), "finding_decisions": decisions, "dismissed_findings": decisions["dismissed"], "recurring_issues": sorted([{"type": k, "count": v} for k, v in counts.items()], key=lambda x: -x["count"]), "contested_rules": contested_rules, "guideline_gaps": guidance_gaps}
