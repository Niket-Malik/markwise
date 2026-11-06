import os
import json
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

os.environ.setdefault("DATABASE_URL", "sqlite://")

import pytest
from fastapi.testclient import TestClient
from docx import Document
from pypdf import PdfWriter
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.main import Base, RateLimitMiddleware, app, configured_cors_origins, db_session, normalize_database_url


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session():
        with factory() as session:
            yield session

    app.dependency_overrides[db_session] = override_session
    with TestClient(app) as test_client:
        test_client.session_factory = factory
        middleware = app.middleware_stack
        while middleware is not None:
            if isinstance(middleware, RateLimitMiddleware):
                middleware._events.clear()
            middleware = getattr(middleware, "app", None)
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)
    engine.dispose()


def register(client, org, email):
    response = client.post("/api/auth/register", json={"organization": org, "name": "Test Admin", "email": email, "password": "correct-horse-battery"})
    assert response.status_code == 200
    return response.json()["access_token"]


def install_fake_s3(monkeypatch, fake_client, calls):
    import sys
    import types

    class FakeConfig:
        def __init__(self, signature_version, s3):
            self.signature_version = signature_version
            self.s3 = s3

    botocore = types.ModuleType("botocore")
    botocore.__path__ = []
    config_module = types.ModuleType("botocore.config")
    config_module.Config = FakeConfig
    botocore.config = config_module
    monkeypatch.setitem(sys.modules, "botocore", botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", config_module)
    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(
        client=lambda *args, **kwargs: (calls.append(("client", kwargs)) or fake_client)
    ))


def test_admin_confirmation_and_abstaining_claim(client):
    token = register(client, "Acme", "admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.put("/api/settings/retention", headers=headers, json={"upload_retention_days": 30}).json()["upload_retention_days"] == 30
    created = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Avoid guarantee", "body": "guaranteed", "source": "Voice guide", "passage": "Do not promise guaranteed outcomes."})
    assert created.status_code == 200
    assert created.json()["status"] == "pending"

    draft = client.post("/api/reviews", headers=headers, json={"title": "Launch copy", "content": "We cut onboarding by 60%, guaranteed."})
    assert draft.status_code == 200
    assert len(draft.json()["findings"]) == 1
    assert draft.json()["findings"][0]["kind"] == "cannot_verify"
    finding_id = draft.json()["findings"][0]["id"]
    assert client.post(f"/api/findings/{finding_id}/decision", headers=headers, json={"decision": "edited", "suggestion": "Confirmed wording after review."}).status_code == 422
    edit = client.post(f"/api/findings/{finding_id}/decision", headers=headers, json={"decision": "edited", "suggestion": "Confirmed wording after review.", "reason": "Reviewed the current customer study."})
    assert edit.json()["suggestion"] == "Confirmed wording after review."
    decision_history = client.get(f"/api/findings/{finding_id}/decisions", headers=headers).json()
    assert decision_history[0]["reason"] == "Reviewed the current customer study."
    assert client.post(f"/api/findings/{finding_id}/decision", headers=headers, json={"decision": "accepted"}).status_code == 422
    changed_draft = client.patch(f"/api/reviews/{draft.json()['id']}", headers=headers, json={"content": "We cut onboarding by 50%."})
    assert changed_draft.status_code == 200
    assert len(changed_draft.json()["findings"]) == 1
    assert client.get(f"/api/findings/{finding_id}/decisions", headers=headers).json()[0]["reason"] == "Reviewed the current customer study."
    assert client.post(f"/api/findings/{finding_id}/decision", headers=headers, json={"decision": "accepted"}).status_code == 409

    approved = client.post(f"/api/knowledge/{created.json()['id']}/approve", headers=headers)
    assert approved.json()["status"] == "active"
    reviewed = client.post("/api/reviews", headers=headers, json={"title": "Launch copy 2", "content": "We cut onboarding by 60%, guaranteed."})
    kinds = {finding["kind"] for finding in reviewed.json()["findings"]}
    assert {"restricted_term", "cannot_verify"}.issubset(kinds)
    assert all(finding["citation"] for finding in reviewed.json()["findings"])

    fact = client.post("/api/knowledge", headers=headers, json={"kind": "fact", "title": "Onboarding time reduction", "body": "Approved study: onboarding time reduced by up to 40%.", "source": "2025 customer study", "passage": "Reduces onboarding time by up to 40%."})
    assert fact.json()["status"] == "pending"
    client.post(f"/api/knowledge/{fact.json()['id']}/approve", headers=headers)
    accurate = client.post("/api/reviews", headers=headers, json={"title": "Accurate claim", "content": "We cut onboarding by up to 40%."})
    assert not any(f["kind"] in {"claim", "cannot_verify"} for f in accurate.json()["findings"])
    compared = client.post("/api/reviews", headers=headers, json={"title": "Fact check", "content": "We cut onboarding by 60%."})
    claim = next(f for f in compared.json()["findings"] if f["kind"] == "claim")
    assert "2025 customer study" in claim["citation"]
    applied = client.post(f"/api/findings/{claim['id']}/decision", headers=headers, json={"decision": "accepted"})
    assert applied.status_code == 200 and applied.json()["content_updated"] is True
    updated_review = next(item for item in client.get("/api/reviews", headers=headers).json() if item["id"] == compared.json()["id"])
    assert updated_review["content"] == "Approved study: onboarding time reduced by up to 40%."
    assert len(client.get(f"/api/reviews/{compared.json()['id']}/versions", headers=headers).json()) == 2
    repeated = client.post("/api/reviews", headers=headers, json={"title": "Repeated wording", "content": "We cut onboarding by 60%. We cut onboarding by 60%."}).json()
    repeated_claim = next(item for item in repeated["findings"] if item["kind"] == "claim")
    assert client.post(f"/api/findings/{repeated_claim['id']}/decision", headers=headers, json={"decision": "accepted"}).status_code == 409
    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert dashboard["guideline_gaps"][0]["type"] == "unsupported_claims"
    assert dashboard["guideline_gaps"][0]["count"] >= 1


def test_organisation_isolation(client):
    owner = register(client, "Acme", "owner@acme.test")
    outsider = register(client, "Other Co", "owner@other.test")
    own_headers = {"Authorization": f"Bearer {owner}"}
    other_headers = {"Authorization": f"Bearer {outsider}"}
    review = client.post("/api/reviews", headers=own_headers, json={"title": "Private draft", "content": "An unverified 40% improvement."})
    assert review.status_code == 200
    assert client.get("/api/reviews", headers=other_headers).json() == []
    hidden = client.post(f"/api/reviews/{review.json()['id']}/submit", headers=other_headers)
    assert hidden.status_code == 404


def test_cors_allows_only_configured_frontend_origins():
    origins = configured_cors_origins("https://localhost:3000/, https://preview.example", "markwise-hackathon-web.onrender.com")
    assert origins == ["https://localhost:3000", "https://preview.example", "https://markwise-hackathon-web.onrender.com"]


def test_neon_database_urls_use_psycopg3_for_runtime_and_migrations():
    assert normalize_database_url("postgresql://user:pass@host/db") == "postgresql+psycopg://user:pass@host/db?sslmode=require"
    assert normalize_database_url("postgres://user:pass@host/db?sslmode=disable") == "postgresql+psycopg://user:pass@host/db?sslmode=require"
    assert normalize_database_url("postgresql://user:pass@localhost/db") == "postgresql+psycopg://user:pass@localhost/db"
    assert normalize_database_url("postgresql://user:pass@host/db?sslmode=verify-full") == "postgresql+psycopg://user:pass@host/db?sslmode=verify-full"


def test_finding_decisions_and_submission_require_review_authority(client):
    owner_token = register(client, "Workflow Co", "owner@workflow.test")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    creator = client.post("/api/team", headers=owner_headers, json={
        "name": "Other Creator", "email": "creator@workflow.test", "role": "creator", "password": "creator-secure-password"
    }).json()
    reviewer = client.post("/api/team", headers=owner_headers, json={
        "name": "Assigned Reviewer", "email": "reviewer@workflow.test", "role": "reviewer", "password": "reviewer-secure-password"
    }).json()
    creator_token = client.post("/api/auth/login", json={"email": creator["email"], "password": "creator-secure-password"}).json()["access_token"]
    reviewer_token = client.post("/api/auth/login", json={"email": reviewer["email"], "password": "reviewer-secure-password"}).json()["access_token"]
    creator_headers = {"Authorization": f"Bearer {creator_token}"}
    reviewer_headers = {"Authorization": f"Bearer {reviewer_token}"}
    review = client.post("/api/reviews", headers=owner_headers, json={"title": "Restricted draft", "content": "We guarantee 90% results."}).json()
    finding_id = review["findings"][0]["id"]

    assert client.post(f"/api/findings/{finding_id}/decision", headers=creator_headers, json={"decision": "accepted"}).status_code == 403
    assert client.post(f"/api/reviews/{review['id']}/submit", headers=creator_headers).status_code == 403
    assert client.post(f"/api/findings/{finding_id}/decision", headers=reviewer_headers, json={"decision": "accepted"}).status_code == 403

    assert client.post(f"/api/reviews/{review['id']}/assign", headers=owner_headers, json={"user_id": reviewer["id"]}).status_code == 200
    assert client.post(f"/api/reviews/{review['id']}/submit", headers=owner_headers).status_code == 200
    accepted = client.post(f"/api/findings/{finding_id}/decision", headers=reviewer_headers, json={"decision": "dismissed", "reason": "I reviewed this flagged claim."})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "dismissed"
def test_comments_assignment_and_high_risk_review(client):
    admin = register(client, "Acme", "admin@workflow.test")
    admin_headers = {"Authorization": f"Bearer {admin}"}
    created_review = client.post("/api/reviews", headers=admin_headers, json={"title": "Claim review", "content": "We cut costs by 60%."})
    review_id = created_review.json()["id"]
    reviewer = client.post("/api/team", headers=admin_headers, json={"email": "reviewer@workflow.test", "name": "Authorised Reviewer", "role": "reviewer", "password": "another-correct-password"})
    assert reviewer.status_code == 200
    assert client.post(f"/api/reviews/{review_id}/assign", headers=admin_headers, json={"user_id": reviewer.json()["id"]}).status_code == 200
    assert client.get("/api/reviews", headers=admin_headers).json()[0]["assigned_to"] == reviewer.json()["id"]
    comment = client.post(f"/api/reviews/{review_id}/comments", headers=admin_headers, json={"body": "Please confirm this number."})
    assert comment.status_code == 200
    assert client.get(f"/api/reviews/{review_id}/comments", headers=admin_headers).json()[0]["body"] == "Please confirm this number."
    assert len(client.get(f"/api/reviews/{review_id}/versions", headers=admin_headers).json()) == 1
    changed = client.patch(f"/api/reviews/{review_id}", headers=admin_headers, json={"content": "We reduced costs by 60 percent."})
    assert changed.status_code == 200
    versions = client.get(f"/api/reviews/{review_id}/versions", headers=admin_headers).json()
    assert len(versions) == 2
    assert versions[-1]["content"] == "We reduced costs by 60 percent."
    assert client.get("/api/audit-events", headers=admin_headers).status_code == 200
    assert client.get("/api/audit-events", headers={"Authorization": f"Bearer {register(client, 'Other', 'audit-outsider@test.test')}"}).json() == []

    login = client.post("/api/auth/login", json={"email": "reviewer@workflow.test", "password": "another-correct-password"})
    reviewer_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    unassigned_review = client.post("/api/reviews", headers=admin_headers, json={"title": "Unassigned review", "content": "Neutral campaign copy."}).json()
    client.post(f"/api/reviews/{unassigned_review['id']}/submit", headers=admin_headers)
    assert client.post(f"/api/reviews/{unassigned_review['id']}/decision", headers=reviewer_headers, json={"decision": "approved"}).status_code == 403
    assert client.post(f"/api/reviews/{review_id}/submit", headers=admin_headers).status_code == 200
    decision = client.post(f"/api/reviews/{review_id}/decision", headers=reviewer_headers, json={"decision": "approved"})
    assert decision.status_code == 200
    assert decision.json()["status"] == "approved"
    assert client.delete(f"/api/reviews/{review_id}", headers=admin_headers).json()["deleted"] is True
    assert client.get(f"/api/reviews/{review_id}/versions", headers=admin_headers).status_code == 404


def test_external_analysis_requires_workspace_opt_in_and_validates_citations(client, monkeypatch):
    import backend.app.main as main

    token = register(client, "Acme", "ai-admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    fact = client.post("/api/knowledge", headers=headers, json={"kind": "fact", "title": "Onboarding time", "body": "Approved: up to 40% less onboarding time.", "source": "Customer study", "passage": "Onboarding time fell by up to 40%."}).json()
    client.post(f"/api/knowledge/{fact['id']}/approve", headers=headers)
    rule = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Avoid guarantees", "body": "guaranteed", "source": "Voice guide", "passage": "Do not promise guaranteed outcomes."}).json()
    client.post(f"/api/knowledge/{rule['id']}/approve", headers=headers)

    for name, value in (("ai_provider", "openai_compatible"), ("ai_api_key", "test-key-only"), ("ai_model", "test-model"), ("embedding_model", "")):
        monkeypatch.setattr(main.settings, name, value)
    first = {"kind": "claim", "excerpt": "We cut onboarding by 60%, guaranteed", "explanation": "The cited study supports at most 40%.", "severity": "high", "confidence": 0.94, "cited_source_ids": [fact["id"]], "cannot_verify": False, "suggested_rewrite": "Customers reduced onboarding time by up to 40%.", "intent_preserved": True}
    legal = {"kind": "legal", "excerpt": "guaranteed", "explanation": "The approved rule prohibits guarantees.", "severity": "high", "confidence": 0.9, "cited_source_ids": [rule["id"]], "cannot_verify": False, "suggested_rewrite": None, "intent_preserved": False}
    second = {**first, "cited_source_ids": [99999], "suggested_rewrite": "Invented 99% result."}
    responses = iter([[first, legal], [second]])

    def fake_post(url, headers, json: dict, timeout):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": __import__("json").dumps({"findings": next(responses)})}}]}
        return response

    monkeypatch.setattr("backend.app.ai.httpx.post", fake_post)
    review_without_consent = client.post("/api/reviews", headers=headers, json={"title": "No consent", "content": "We cut onboarding by 60%."}).json()
    assert review_without_consent["ai_status"] == "disabled"

    consent = client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    assert consent.json()["external_ai_enabled"] is True
    verified = client.post("/api/reviews", headers=headers, json={"title": "Grounded", "content": "We cut onboarding by 60%, guaranteed."}).json()
    assert verified["ai_status"] == "complete"
    claim = next(f for f in verified["findings"] if f["kind"] == "claim")
    assert "Customer study" in claim["citation"]
    assert claim["suggestion"] == "Customers reduced onboarding time by up to 40%."
    rule_finding = next(f for f in verified["findings"] if f["kind"] == "ai_legal")
    dismissed = client.post(f"/api/findings/{rule_finding['id']}/decision", headers=headers, json={"decision": "dismissed", "reason": "This phrase is required in the approved legal line."})
    assert dismissed.status_code == 200
    insights = client.get("/api/dashboard", headers=headers).json()
    assert insights["dismissed_findings"] == 1
    assert insights["contested_rules"][0]["title"] == "Avoid guarantees"

    invalid = client.post("/api/reviews", headers=headers, json={"title": "Invalid citation", "content": "We cut onboarding by 60%, guaranteed."}).json()
    assert any(f["kind"] == "cannot_verify" and "99999" not in f["citation"] for f in invalid["findings"])


def test_ai_failure_runs_cited_knowledge_fallback_and_never_reports_clean(client, monkeypatch):
    import backend.app.main as main
    import backend.app.ai as ai

    token = register(client, "Fallback Co", "fallback-admin@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    rule = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "No guarantees", "body": "guaranteed", "source": "Approved Voice Guide", "passage": "Never promise guaranteed outcomes."}).json()
    client.post(f"/api/knowledge/{rule['id']}/approve", headers=headers)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")

    def unavailable():
        raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)

    monkeypatch.setattr(ai, "provider_from_config", unavailable)
    response = client.post("/api/reviews", headers=headers, json={"title": "Fallback draft", "content": "Our guaranteed product is ready."})
    assert response.status_code == 200
    review = response.json()
    assert review["status"] == "analysis_incomplete"
    assert review["ai_status"] == "error"
    assert review["analysis_mode"] == "knowledge_base_fallback"
    assert review["analysis_state"] == "failed"
    assert review["clean_review"] is False
    assert "Knowledge Base Fallback Review" in review["analysis_note"]
    assert "AI Analysis Unavailable" in review["ai_message"]
    finding = next(item for item in review["findings"] if item["kind"] == "restricted_term")
    assert "Approved Voice Guide" in finding["citation"]

    dashboard = client.get("/api/dashboard", headers=headers).json()
    assert dashboard["analysis_incomplete"] == 1
    assert dashboard["analysis_failed"] == 1
    assert dashboard["needs_review"] == 1


def test_successful_zero_finding_ai_analysis_is_the_only_clean_result(client, monkeypatch):
    import backend.app.main as main
    import backend.app.ai as ai

    token = register(client, "Clean Co", "clean-admin@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    source = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Product wording", "body": "Use plain language.", "source": "Style guide", "passage": "Describe product features clearly."}).json()
    client.post(f"/api/knowledge/{source['id']}/approve", headers=headers)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")

    class EmptyProvider:
        def analyse(self, content, context, sources):
            assert any(item.id == source["id"] for item in sources)
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "provider_from_config", lambda: EmptyProvider())
    response = client.post("/api/reviews", headers=headers, json={"title": "Clean draft", "content": "Our product supports thoughtful teams."})
    assert response.status_code == 200
    result = response.json()
    assert result["ai_status"] == "complete"
    assert result["analysis_state"] == "completed"
    assert result["clean_review"] is True
    assert result["findings"] == []
    assert result["status"] == "checked"


def test_empty_knowledge_fallback_explicitly_cannot_verify(client, monkeypatch):
    import backend.app.main as main
    import backend.app.ai as ai

    token = register(client, "No Evidence Co", "no-evidence@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")

    def unavailable():
        raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)

    monkeypatch.setattr(ai, "provider_from_config", unavailable)
    result = client.post("/api/reviews", headers=headers, json={"title": "Unverified draft", "content": "A welcoming brand for every team."}).json()
    assert result["ai_status"] == "error"
    assert result["findings"] == []
    assert result["clean_review"] is False
    assert "CANNOT VERIFY" in result["analysis_note"]
    assert "not a complete AI-powered review" in result["analysis_note"]


def test_fallback_marks_nonpercentage_factual_claim_cannot_verify(client):
    token = register(client, "Claim Check Co", "claim-check@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    result = client.post("/api/reviews", headers=headers, json={
        "title": "Unsupported claim", "content": "Our platform doubles customer retention for every retailer."
    }).json()
    finding = next(item for item in result["findings"] if item["kind"] == "cannot_verify")
    assert finding["excerpt"] == "Our platform doubles customer retention for every retailer."
    assert "CANNOT VERIFY" in finding["explanation"]
    assert finding["citation"] == "No matching active approved fact in this workspace."
    assert result["clean_review"] is False


def test_fallback_verifies_exact_approved_claim_but_abstains_on_other_claim(client):
    token = register(client, "Mixed Claims Co", "mixed-claims@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    fact = client.post("/api/knowledge", headers=headers, json={
        "kind": "fact", "title": "Sales for local shops", "body": "Our product doubles sales for local shops.",
        "source": "Approved customer study", "passage": "Our product doubles sales for local shops."
    }).json()
    client.post(f"/api/knowledge/{fact['id']}/approve", headers=headers)
    rule = client.post("/api/knowledge", headers=headers, json={
        "kind": "rule", "title": "No guarantees", "body": "guaranteed",
        "source": "Approved voice guide", "passage": "Do not promise guaranteed outcomes."
    }).json()
    client.post(f"/api/knowledge/{rule['id']}/approve", headers=headers)
    result = client.post("/api/reviews", headers=headers, json={
        "title": "Two factual claims",
        "content": "Our product doubles sales for local shops. Our product doubles retention at large retailers. The service is guaranteed."
    }).json()
    cannot_verify = [item for item in result["findings"] if item["kind"] == "cannot_verify"]
    assert any(item["kind"] == "restricted_term" for item in result["findings"])
    assert len(cannot_verify) == 1
    assert cannot_verify[0]["excerpt"] == "Our product doubles retention at large retailers."
    assert f"FACT #{fact['id']}" in cannot_verify[0]["citation"]
    assert "does not substantiate this claim" in cannot_verify[0]["citation"]
    assert result["clean_review"] is False


def test_fallback_never_retrieves_another_workspace_fact(client):
    first_token = register(client, "Private Evidence Co", "private-evidence@example.test")
    first_headers = {"Authorization": f"Bearer {first_token}"}
    fact = client.post("/api/knowledge", headers=first_headers, json={
        "kind": "fact", "title": "Private sales result", "body": "Our platform doubles sales for private clients.",
        "source": "Private workspace study", "passage": "Private workspace evidence."
    }).json()
    client.post(f"/api/knowledge/{fact['id']}/approve", headers=first_headers)

    second_token = register(client, "Separate Workspace Co", "separate-workspace@example.test")
    second_headers = {"Authorization": f"Bearer {second_token}"}
    result = client.post("/api/reviews", headers=second_headers, json={
        "title": "Isolated claim", "content": "Our platform doubles sales for private clients."
    }).json()
    finding = next(item for item in result["findings"] if item["kind"] == "cannot_verify")
    assert "Private workspace study" not in finding["citation"]
    assert finding["citation"] == "No matching active approved fact in this workspace."


def _create_test_asset(client, headers, monkeypatch, tmp_path, main, filename="reference.png"):
    import io
    from PIL import Image

    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr(main.settings, "storage_backend", "local")
    image = Image.new("RGB", (32, 24), "#345678")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    return client.post("/api/uploads", headers=headers, files={"file": (filename, buffer.getvalue(), "image/png")}).json()


def test_admin_deleting_review_asset_detaches_link_and_preserves_review_data(client, monkeypatch, tmp_path):
    import backend.app.main as main

    token = register(client, "Asset Delete Co", "asset-delete@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    reference_asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main, "logo-reference.png")
    review = client.post("/api/reviews", headers=headers, json={
        "title": "Keep this review", "content": "Our sales rose by 17%."
    }).json()
    assert review["findings"]
    client.post(f"/api/reviews/{review['id']}/comments", headers=headers, json={"body": "Keep this discussion."})
    with client.session_factory() as db:
        row = db.get(main.Review, review["id"])
        row.source_asset_id = asset["id"]
        row.reference_asset_id = reference_asset["id"]
        asset_row = db.get(main.StoredAsset, asset["id"])
        storage_path = asset_row.storage_key[6:]
        reference_path = db.get(main.StoredAsset, reference_asset["id"]).storage_key[6:]
        db.commit()

    removed = client.delete(f"/api/uploads/{asset['id']}", headers=headers)
    assert removed.status_code == 200
    assert removed.json()["deleted"] is True
    with client.session_factory() as db:
        kept_review = db.get(main.Review, review["id"])
        assert kept_review.source_asset_id is None
        assert kept_review.reference_asset_id == reference_asset["id"]
        assert db.get(main.StoredAsset, asset["id"]) is None
        assert db.get(main.StoredAsset, reference_asset["id"]) is not None
        assert [finding.id for finding in kept_review.findings] == [finding["id"] for finding in review["findings"]]
        assert db.query(main.AuditEvent).filter_by(entity_type="asset", entity_id=asset["id"], action="asset.deleted").count() == 1
    serialized = client.get("/api/reviews", headers=headers).json()[0]
    assert serialized["id"] == review["id"]
    assert serialized["content"] == review["content"]
    assert [item["id"] for item in serialized["findings"]] == [item["id"] for item in review["findings"]]
    assert client.get(f"/api/reviews/{review['id']}/comments", headers=headers).json()[0]["body"] == "Keep this discussion."
    assert not __import__("pathlib").Path(storage_path).exists()
    assert __import__("pathlib").Path(reference_path).exists()
    assert client.delete(f"/api/uploads/{reference_asset['id']}", headers=headers).status_code == 200
    with client.session_factory() as db:
        assert db.get(main.Review, review["id"]).reference_asset_id is None


def test_admin_deleting_unreferenced_asset_succeeds(client, monkeypatch, tmp_path):
    import backend.app.main as main

    token = register(client, "Unreferenced Asset Co", "unreferenced-asset@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    assert client.delete(f"/api/uploads/{asset['id']}", headers=headers).json()["deleted"] is True
    with client.session_factory() as db:
        assert db.get(main.StoredAsset, asset["id"]) is None


def test_admin_can_remove_metadata_for_already_missing_object(client, monkeypatch, tmp_path):
    import backend.app.main as main
    from pathlib import Path

    token = register(client, "Missing Object Co", "missing-object@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    with client.session_factory() as db:
        path = Path(db.get(main.StoredAsset, asset["id"]).storage_key[6:])
    path.unlink()
    response = client.delete(f"/api/uploads/{asset['id']}", headers=headers)
    assert response.status_code == 200
    with client.session_factory() as db:
        assert db.get(main.StoredAsset, asset["id"]) is None


def test_storage_delete_failure_preserves_asset_and_review_references(client, monkeypatch, tmp_path):
    import backend.app.main as main
    import backend.app.storage as storage

    token = register(client, "Storage Failure Co", "storage-failure@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    review = client.post("/api/reviews", headers=headers, json={"title": "Retain link", "content": "A saved review."}).json()
    with client.session_factory() as db:
        row = db.get(main.Review, review["id"])
        row.source_asset_id = asset["id"]
        storage_path = db.get(main.StoredAsset, asset["id"]).storage_key[6:]
        db.commit()

    def fail_delete(*args, **kwargs):
        raise RuntimeError("simulated storage outage")

    monkeypatch.setattr(storage, "delete_object", fail_delete)
    response = client.delete(f"/api/uploads/{asset['id']}", headers=headers)
    assert response.status_code == 503
    with client.session_factory() as db:
        assert db.get(main.StoredAsset, asset["id"]) is not None
        assert db.get(main.Review, review["id"]).source_asset_id == asset["id"]
        assert db.query(main.AuditEvent).filter_by(entity_type="asset", entity_id=asset["id"], action="asset.deleted").count() == 0
    from pathlib import Path
    assert Path(storage_path).exists()


def test_database_failure_after_storage_delete_restores_asset_and_references(client, monkeypatch, tmp_path):
    import backend.app.main as main
    import backend.app.storage as storage
    from pathlib import Path

    token = register(client, "Database Failure Co", "database-failure@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    review = client.post("/api/reviews", headers=headers, json={"title": "Keep reference", "content": "A saved review."}).json()
    with client.session_factory() as db:
        db.get(main.Review, review["id"]).source_asset_id = asset["id"]
        storage_path = Path(db.get(main.StoredAsset, asset["id"]).storage_key[6:])
        db.commit()

    original_audit = main.audit

    def fail_delete_audit(db, user, action, entity, entity_id):
        if action == "asset.deleted":
            raise RuntimeError("simulated database transaction failure")
        return original_audit(db, user, action, entity, entity_id)

    monkeypatch.setattr(main, "audit", fail_delete_audit)
    response = client.delete(f"/api/uploads/{asset['id']}", headers=headers)
    assert response.status_code == 503
    with client.session_factory() as db:
        assert db.get(main.StoredAsset, asset["id"]) is not None
        assert db.get(main.Review, review["id"]).source_asset_id == asset["id"]
    assert storage_path.exists()


def test_workspace_admin_cannot_delete_another_workspaces_asset(client, monkeypatch, tmp_path):
    import backend.app.main as main

    owner_token = register(client, "Asset Owner Co", "asset-owner@example.test")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, owner_headers, monkeypatch, tmp_path, main)
    outsider_token = register(client, "Outsider Co", "asset-outsider@example.test")
    outsider_headers = {"Authorization": f"Bearer {outsider_token}"}
    assert client.delete(f"/api/uploads/{asset['id']}", headers=outsider_headers).status_code == 404
    with client.session_factory() as db:
        assert db.get(main.StoredAsset, asset["id"]) is not None


def test_retention_detaches_expired_visual_asset_without_deleting_review(client, monkeypatch, tmp_path):
    import backend.app.main as main
    import backend.app.retention as retention

    token = register(client, "Visual Retention Co", "visual-retention@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/api/settings/retention", headers=headers, json={"upload_retention_days": 1, "review_retention_days": 0})
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "")
    asset = _create_test_asset(client, headers, monkeypatch, tmp_path, main)
    review = client.post("/api/reviews", headers=headers, json={"title": "Persistent review", "content": "Our sales rose by 17%."}).json()
    with client.session_factory() as db:
        db.get(main.Review, review["id"]).source_asset_id = asset["id"]
        db.get(main.StoredAsset, asset["id"]).created_at = datetime.now(timezone.utc) - timedelta(days=2)
        db.commit()
    monkeypatch.setattr(retention, "SessionLocal", client.session_factory)
    assert retention.purge_expired_uploads() == 1
    with client.session_factory() as db:
        kept_review = db.get(main.Review, review["id"])
        assert kept_review is not None
        assert kept_review.source_asset_id is None
        assert len([finding for finding in kept_review.findings if finding.status != "stale"]) == len(review["findings"])


def test_manual_retry_recovers_ai_without_duplicate_review_or_findings(client, monkeypatch):
    import backend.app.main as main
    import backend.app.ai as ai

    token = register(client, "Retry Co", "retry-admin@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    rule = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "No guarantees", "body": "guaranteed", "source": "Voice Guide", "passage": "Do not guarantee outcomes."}).json()
    client.post(f"/api/knowledge/{rule['id']}/approve", headers=headers)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")

    def unavailable():
        raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)

    monkeypatch.setattr(ai, "provider_from_config", unavailable)
    failed = client.post("/api/reviews", headers=headers, json={"title": "Retry draft", "content": "Our guaranteed product is ready."}).json()
    old_ids = {item["id"] for item in failed["findings"]}
    assert failed["ai_status"] == "error"

    class RecoveredProvider:
        def analyse(self, content, context, sources):
            assert any(source.id == rule["id"] for source in sources)
            return ai.AnalysisResponse(findings=[ai.CandidateFinding(
                kind="legal", excerpt="guaranteed", explanation="The approved guide prohibits guarantees.",
                severity="high", confidence=0.9, cited_source_ids=[rule["id"]],
            )])

    monkeypatch.setattr(ai, "provider_from_config", lambda: RecoveredProvider())
    retried = client.post(f"/api/reviews/{failed['id']}/retry-ai", headers=headers)
    assert retried.status_code == 200
    result = retried.json()
    assert result["ai_status"] == "complete"
    assert result["analysis_state"] == "completed"
    assert result["clean_review"] is False
    assert len(client.get("/api/reviews", headers=headers).json()) == 1
    assert len(result["findings"]) == len({(item["kind"], item["excerpt"]) for item in result["findings"]})
    assert old_ids.intersection({item["id"] for item in result["findings"]})
    assert any(item["kind"] == "ai_legal" and "Voice Guide" in item["citation"] for item in result["findings"])
    assert client.post(f"/api/reviews/{failed['id']}/retry-ai", headers=headers).status_code == 409


def test_failed_manual_retry_preserves_fallback_and_reports_cannot_verify(client, monkeypatch):
    import backend.app.main as main
    import backend.app.ai as ai

    token = register(client, "Abstain Co", "abstain-admin@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")

    def unavailable():
        raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)

    monkeypatch.setattr(ai, "provider_from_config", unavailable)
    created = client.post("/api/reviews", headers=headers, json={"title": "Unsupported number", "content": "We increase sales by 85%."}).json()
    assert created["ai_status"] == "error"
    assert any(item["kind"] == "cannot_verify" for item in created["findings"])
    before = {(item["kind"], item["excerpt"], item["id"]) for item in created["findings"]}
    retried = client.post(f"/api/reviews/{created['id']}/retry-ai", headers=headers)
    assert retried.status_code == 200
    after = {(item["kind"], item["excerpt"], item["id"]) for item in retried.json()["findings"]}
    assert retried.json()["ai_status"] == "error"
    assert retried.json()["clean_review"] is False
    assert before == after


def test_ai_abstains_on_unsourced_nonpercentage_claim(client, monkeypatch):
    import backend.app.main as main

    token = register(client, "No Facts Co", "claims@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "ai_provider", "unsupported")
    monkeypatch.setattr(main.settings, "ai_api_key", "test-key-only")
    monkeypatch.setattr(main.settings, "ai_model", "test-model")
    assert client.get("/api/settings/privacy", headers=headers).json()["ai_configured"] is False
    for name, value in (("ai_provider", "openai_compatible"), ("ai_api_key", "test-key-only"), ("ai_model", "test-model"), ("embedding_model", "")):
        monkeypatch.setattr(main.settings, name, value)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    candidate = {"kind": "claim", "excerpt": "Our service doubles your sales", "explanation": "The draft makes a factual performance claim.", "severity": "high", "confidence": 0.88, "cited_source_ids": [], "cannot_verify": True, "suggested_rewrite": None, "intent_preserved": False}

    def fake_post(url, headers, json: dict, timeout):
        assert json["model"] == "test-model"
        payload = json_module.loads(json["messages"][1]["content"])
        assert payload["approved_sources"] == []
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": json_module.dumps({"findings": [candidate]})}}]}
        return response

    import json as json_module
    monkeypatch.setattr("backend.app.ai.httpx.post", fake_post)
    review = client.post("/api/reviews", headers=headers, json={"title": "Unsourced claim", "content": "Our service doubles your sales."}).json()
    assert review["ai_status"] == "no_sources"
    finding = next(item for item in review["findings"] if item["kind"] == "cannot_verify")
    assert finding["excerpt"] == "Our service doubles your sales"
    assert finding["citation"] == "No matching active approved fact was validated in the retrieved workspace sources."


def test_docx_guidelines_are_imported_as_pending_passages(client):
    token = register(client, "Acme", "docx-admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    document = Document()
    document.add_heading("Legal language", level=1)
    document.add_paragraph("Do not promise guaranteed outcomes to customers.")
    buffer = __import__("io").BytesIO()
    document.save(buffer)
    response = client.post("/api/knowledge/import", headers=headers, files={"file": ("brand-guide.docx", buffer.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert response.status_code == 200
    imported = response.json()
    assert imported["candidate_count"] == 1
    assert imported["status"] == "pending_admin_confirmation"
    assert imported["items"][0]["status"] == "pending"
    assert "section Legal language" in imported["items"][0]["source"]
    assert "guaranteed outcomes" in imported["items"][0]["passage"]
    assert client.delete(f"/api/uploads/{imported['asset_id']}", headers=headers).json()["deleted"] is True


def test_image_ocr_and_scanned_pdf_ocr(client, monkeypatch, tmp_path):
    import backend.app.main as main
    from PIL import Image, ImageDraw
    import io

    token = register(client, "Acme", "ocr-admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "Approved OCR sample")

    image = Image.new("RGB", (600, 160), "white")
    ImageDraw.Draw(image).text((20, 40), "Approved OCR sample", fill="black")
    image_bytes = io.BytesIO(); image.save(image_bytes, format="PNG")
    image_result = client.post("/api/uploads", headers=headers, files={"file": ("sample.png", image_bytes.getvalue(), "image/png")})
    assert image_result.status_code == 200
    assert image_result.json()["extracted_text"] == "Approved OCR sample"
    assert image_result.json()["note"] is None
    assert client.get("/api/uploads", headers=headers).json()[0]["filename"] == "sample.png"
    member = client.post("/api/team", headers=headers, json={"name": "Content Creator", "email": "creator@ocr.test", "role": "creator", "password": "creator-secure-password"}).json()
    member_token = client.post("/api/auth/login", json={"email": member["email"], "password": "creator-secure-password"}).json()["access_token"]
    assert client.get("/api/uploads", headers={"Authorization": f"Bearer {member_token}"}).json() == []
    assert client.delete(f"/api/uploads/{image_result.json()['id']}", headers=headers).json()["deleted"] is True

    writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
    pdf_bytes = io.BytesIO(); writer.write(pdf_bytes)
    pdf_result = client.post("/api/uploads", headers=headers, files={"file": ("scanned.pdf", pdf_bytes.getvalue(), "application/pdf")})
    assert pdf_result.status_code == 200
    assert "Approved OCR sample" in pdf_result.json()["extracted_text"]
    assert pdf_result.json()["ocr_pages"] == [1]


def test_scheduled_retention_removes_uploads_and_review_data(client, monkeypatch, tmp_path):
    import backend.app.main as main
    import backend.app.retention as retention
    import io
    from PIL import Image, ImageDraw

    token = register(client, "Retention Co", "retention-admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "Brand image")
    assert client.put("/api/settings/retention", headers=headers, json={"upload_retention_days": 1, "review_retention_days": 1}).status_code == 200
    review = client.post("/api/reviews", headers=headers, json={"title": "Temporary draft", "content": "Temporary copy for deletion."}).json()
    image = Image.new("RGB", (200, 100), "white")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    asset = client.post("/api/uploads", headers=headers, files={"file": ("temporary.png", buffer.getvalue(), "image/png")}).json()
    old = datetime.now(timezone.utc) - timedelta(days=2)
    with client.session_factory() as db:
        db.get(main.Review, review["id"]).created_at = old
        db.get(main.StoredAsset, asset["id"]).created_at = old
        db.commit()
    monkeypatch.setattr(retention, "SessionLocal", client.session_factory)
    assert retention.purge_expired_uploads() == 2
    assert client.get("/api/reviews", headers=headers).json() == []


def test_upload_rejects_mismatched_file_signature_before_storage(client, monkeypatch, tmp_path):
    import backend.app.main as main

    token = register(client, "Upload Co", "upload-admin@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    response = client.post("/api/uploads", headers=headers, files={"file": ("not-really.png", b"plain text pretending to be an image", "image/png")})
    assert response.status_code == 422
    assert list(tmp_path.rglob("*")) == []
    assert not list(tmp_path.rglob("*.png"))


def test_s3_storage_adapter_uses_opaque_org_scoped_keys(monkeypatch):
    from types import SimpleNamespace
    from backend.app.main import Settings
    from backend.app.storage import delete_object, put_object

    calls = []
    fake_client = SimpleNamespace(
        put_object=lambda **kwargs: calls.append(("put", kwargs)),
        delete_object=lambda **kwargs: calls.append(("delete", kwargs)),
    )
    install_fake_s3(monkeypatch, fake_client, calls)
    config = Settings(storage_backend="s3", s3_endpoint_url="", s3_bucket="markwise-test", s3_region="us-east-1", s3_access_key_id="test-key", s3_secret_access_key="test-secret", s3_server_side_encryption="AES256")
    key = put_object(42, "random-object", "../brand logo.png", b"image-bytes", config)
    assert key == "s3:42/random-object-brand_logo.png"
    delete_object(key, config)
    put_args = next(args for action, args in calls if action == "put")
    delete_args = next(args for action, args in calls if action == "delete")
    assert put_args["Bucket"] == "markwise-test"
    assert put_args["Key"].startswith("42/")
    assert put_args["ServerSideEncryption"] == "AES256"
    assert delete_args["Key"] == put_args["Key"]
    kms = Settings(storage_backend="s3", s3_endpoint_url="", s3_bucket="markwise-test", s3_region="us-east-1", s3_access_key_id="test-key", s3_secret_access_key="test-secret", s3_server_side_encryption="aws:kms", s3_sse_kms_key_id="test-kms")
    put_object(42, "kms-object", "guide.pdf", b"pdf", kms)
    assert calls[-1][1]["ServerSideEncryption"] == "aws:kms"
    assert calls[-1][1]["SSEKMSKeyId"] == "test-kms"


def test_r2_storage_uses_managed_encryption_without_unsupported_sse_headers(monkeypatch):
    from types import SimpleNamespace
    from backend.app.main import Settings
    from backend.app.storage import put_object

    calls = []
    fake_client = SimpleNamespace(put_object=lambda **kwargs: calls.append(("put", kwargs)))
    install_fake_s3(monkeypatch, fake_client, calls)
    config = Settings(storage_backend="s3", s3_endpoint_url="https://acct.r2.cloudflarestorage.com", s3_bucket="markwise", s3_region="auto", s3_access_key_id="r2-key", s3_secret_access_key="r2-secret")
    put_object(9, "object", "guide.pdf", b"pdf", config)
    put_args = next(args for action, args in calls if action == "put")
    assert put_args["Bucket"] == "markwise"
    assert "ServerSideEncryption" not in put_args


def test_supabase_s3_uses_sigv4_path_style_and_private_server_side_roundtrip(monkeypatch):
    from types import SimpleNamespace
    from backend.app.main import Settings
    from backend.app.storage import delete_object, get_object, put_object

    calls = []
    payload = b"private Supabase storage probe"

    class Body:
        def read(self):
            return payload
        def close(self):
            calls.append(("body_closed", {}))

    fake_client = SimpleNamespace(
        put_object=lambda **kwargs: calls.append(("put", kwargs)),
        get_object=lambda **kwargs: (calls.append(("get", kwargs)) or {"Body": Body()}),
        delete_object=lambda **kwargs: calls.append(("delete", kwargs)),
    )
    install_fake_s3(monkeypatch, fake_client, calls)
    config = Settings(
        storage_backend="s3",
        s3_endpoint_url="https://project-ref.storage.supabase.co/storage/v1/s3",
        s3_bucket="markwise-assets", s3_region="ap-southeast-1",
        s3_access_key_id="test-key", s3_secret_access_key="test-secret",
        s3_server_side_encryption="AES256",
    )
    key = put_object(9, "probe", "guide.pdf", payload, config)
    assert get_object(key, config) == payload
    delete_object(key, config)
    client_config = next(args[1]["config"] for args in calls if args[0] == "client")
    assert client_config.signature_version == "s3v4"
    assert client_config.s3["addressing_style"] == "path"
    put_args = next(args[1] for args in calls if args[0] == "put")
    assert put_args["Bucket"] == "markwise-assets"
    assert put_args["Key"] == "9/probe-guide.pdf"
    assert "ServerSideEncryption" not in put_args
    assert next(args[1] for args in calls if args[0] == "get")["Key"] == put_args["Key"]
    assert any(action == "body_closed" for action, _ in calls)


def test_embedding_adapter_checks_batch_size_and_vector_dimensions(monkeypatch):
    from backend.app.ai import AIProviderError, OpenAICompatibleProvider
    from backend.app.main import Settings

    vectors = [[0.125] * 1536, [0.25] * 1536]
    def fake_post(url, headers, json: dict, timeout):
        response = Mock(); response.raise_for_status.return_value = None
        response.json.return_value = {"data": [{"index": 1, "embedding": vectors[1]}, {"index": 0, "embedding": vectors[0]}]}
        return response
    monkeypatch.setattr("backend.app.ai.httpx.post", fake_post)
    provider = OpenAICompatibleProvider(Settings(ai_api_key="test", embedding_model="embed-test"))
    result = provider.embed_many(["first", "second"])
    assert result == vectors
    vectors[1].append(0.5)
    with pytest.raises(AIProviderError):
        provider.embed_many(["first", "second"])


def test_visual_review_uses_approved_sources_and_validated_vision_output(client, monkeypatch, tmp_path):
    import backend.app.main as main
    import io
    from PIL import Image, ImageDraw

    token = register(client, "Visual Co", "visual-admin@acme.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "Contrast sample")
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []})
    image = Image.new("RGB", (320, 160), "white")
    ImageDraw.Draw(image).rectangle((20, 20, 300, 140), fill="#ff0000")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")

    source = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Infographic contrast", "body": "Maintain high contrast in infographic text.", "source": "Design guide", "passage": "Ensure text is readable on its background."}).json()
    client.post(f"/api/knowledge/{source['id']}/approve", headers=headers)
    no_alt = client.post("/api/visual-reviews", headers=headers, data={"role": "informative", "alt_text": ""}, files={"file": ("graphic.png", buffer.getvalue(), "image/png")}).json()
    alt_finding = next(f for f in no_alt["findings"] if f["kind"] == "accessibility")
    assert "SC 1.1.1" in alt_finding["citation"]

    palette_rule = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Brand palette", "body": "Use the approved blue #0000ff for brand graphics.", "source": "Visual guide", "passage": "Approved palette: #0000ff."}).json()
    client.post(f"/api/knowledge/{palette_rule['id']}/approve", headers=headers)
    palette_check = client.post("/api/visual-reviews", headers=headers, data={"role": "brand_graphic", "alt_text": ""}, files={"file": ("graphic.png", buffer.getvalue(), "image/png")}).json()
    colour_finding = next(f for f in palette_check["findings"] if f["kind"] == "colour")
    assert f"RULE #{palette_rule['id']}" in colour_finding["citation"]

    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {"text": ["Low contrast"], "conf": ["90"], "left": [20], "top": [40], "width": [120], "height": [30]})
    low_contrast = Image.new("RGB", (320, 160), "white")
    ImageDraw.Draw(low_contrast).text((20, 40), "Low contrast", fill="#999999")
    low_buffer = io.BytesIO(); low_contrast.save(low_buffer, format="PNG")
    contrast_check = client.post("/api/visual-reviews", headers=headers, data={"role": "informative", "alt_text": "Low contrast example"}, files={"file": ("low-contrast.png", low_buffer.getvalue(), "image/png")}).json()
    contrast_finding = next(f for f in contrast_check["findings"] if f["kind"] == "contrast")
    assert "below 4.5:1" in contrast_finding["explanation"]
    assert "SC 1.4.3" in contrast_finding["citation"]

    reference = Image.new("RGB", (320, 160), "white")
    reference_draw = ImageDraw.Draw(reference)
    for x in range(0, 320, 30):
        reference_draw.line((x, 0, x + 100, 160), fill="black", width=10)
    reference_buffer = io.BytesIO(); reference.save(reference_buffer, format="PNG")
    logo_check = client.post("/api/visual-reviews", headers=headers, data={"role": "logo", "alt_text": ""}, files={"file": ("logo.png", buffer.getvalue(), "image/png"), "reference_logo": ("reference.png", reference_buffer.getvalue(), "image/png")}).json()
    logo_finding = next(f for f in logo_check["findings"] if f["kind"] == "logo")
    assert "User-supplied reference logo" in logo_finding["citation"]

    for name, value in (("ai_provider", "openai_compatible"), ("ai_api_key", "test-key-only"), ("ai_model", "test-model"), ("vision_model", "vision-test")):
        monkeypatch.setattr(main.settings, name, value)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    candidate = {"kind": "contrast", "excerpt": "Image region x=10 y=10 width=80 height=40", "explanation": "The cited guide requires readable contrast.", "severity": "medium", "confidence": 0.8, "cited_source_ids": [source["id"]], "cannot_verify": False, "suggested_rewrite": None, "intent_preserved": False}

    def fake_post(url, headers, json: dict, timeout):
        assert json["model"] == "vision-test"
        assert json["messages"][1]["content"][1]["type"] == "image_url"
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"choices": [{"message": {"content": __import__("json").dumps({"findings": [candidate]})}}]}
        return response

    monkeypatch.setattr("backend.app.ai.httpx.post", fake_post)
    result = client.post("/api/visual-reviews", headers=headers, data={"role": "informative", "alt_text": "A red rectangle"}, files={"file": ("graphic.png", buffer.getvalue(), "image/png")})
    assert result.status_code == 200
    assert result.json()["ai_status"] == "complete"
    finding = next(f for f in result.json()["findings"] if f["kind"] == "ai_contrast")
    assert "Infographic contrast" in finding["citation"]


def test_manual_image_retry_reuses_private_asset_without_duplicate_review(client, monkeypatch, tmp_path):
    import io
    import backend.app.main as main
    import backend.app.ai as ai
    from PIL import Image

    token = register(client, "Visual Retry Co", "visual-retry@example.test")
    headers = {"Authorization": f"Bearer {token}"}
    monkeypatch.setattr(main.settings, "upload_dir", str(tmp_path))
    monkeypatch.setattr("pytesseract.image_to_string", lambda *args, **kwargs: "A product illustration")
    monkeypatch.setattr("pytesseract.image_to_data", lambda *args, **kwargs: {"text": [], "conf": [], "left": [], "top": [], "width": [], "height": []})
    image = Image.new("RGB", (64, 48), "#224466")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    source = client.post("/api/knowledge", headers=headers, json={"kind": "rule", "title": "Visual clarity", "body": "Keep product graphics clear.", "source": "Design guide", "passage": "Use clear, legible imagery."}).json()
    client.post(f"/api/knowledge/{source['id']}/approve", headers=headers)
    client.put("/api/settings/privacy", headers=headers, json={"external_ai_enabled": True})
    monkeypatch.setattr(main.settings, "embedding_model", "")
    monkeypatch.setattr(main.settings, "vision_model", "vision-test")

    def unavailable():
        raise ai.ProviderRequestError("gemini", "temporarily unavailable", True)

    monkeypatch.setattr(ai, "provider_from_config", unavailable)
    created = client.post("/api/visual-reviews", headers=headers,
                          data={"role": "informative", "alt_text": "A product illustration"},
                          files={"file": ("product.png", buffer.getvalue(), "image/png")}).json()
    assert created["ai_status"] == "error"
    assert created["analysis_mode"] == "knowledge_base_fallback"

    class RecoveredVisionProvider:
        def analyse_image(self, data, mime, context, sources, reference_data=None, reference_mime="image/png"):
            assert data.startswith(b"\x89PNG")
            assert mime == "image/png"
            assert any(item.id == source["id"] for item in sources)
            return ai.AnalysisResponse(findings=[])

    monkeypatch.setattr(ai, "provider_from_config", lambda: RecoveredVisionProvider())
    retried = client.post(f"/api/reviews/{created['id']}/retry-ai", headers=headers)
    assert retried.status_code == 200
    assert retried.json()["ai_status"] == "complete"
    assert retried.json()["clean_review"] is True
    assert len(client.get("/api/reviews", headers=headers).json()) == 1


def test_provider_status_is_admin_only_and_never_returns_credentials(client, monkeypatch):
    import backend.app.main as main
    admin_token = register(client, "Provider Status Co", "provider-admin@example.test")
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    monkeypatch.setattr(main.settings, "ai_provider", "gemini")
    monkeypatch.setattr(main.settings, "gemini_api_key", "DO_NOT_RETURN_GEMINI_KEY")
    monkeypatch.setattr(main.settings, "ai_fallback_api_key", "DO_NOT_RETURN_OPENAI_KEY")
    response = client.get("/api/settings/providers", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["primary"] == "gemini"
    assert "DO_NOT_RETURN_GEMINI_KEY" not in response.text
    assert "DO_NOT_RETURN_OPENAI_KEY" not in response.text
    member = client.post("/api/team", headers=admin_headers, json={
        "name": "Reviewer", "email": "reviewer@example.test", "password": "long-test-password-123",
        "role": "reviewer",
    })
    assert member.status_code == 200
    member_login = client.post("/api/auth/login", json={"email": "reviewer@example.test", "password": "long-test-password-123"})
    assert member_login.status_code == 200
    member_headers = {"Authorization": f"Bearer {member_login.json()['access_token']}"}
    assert client.get("/api/settings/providers", headers=member_headers).status_code == 403


def test_z_auth_rate_limit(client):
    responses = [client.post("/api/auth/login", json={"email": "nobody@example.test", "password": "wrong-password"}) for _ in range(21)]
    assert all(response.status_code == 401 for response in responses[:20])
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers["Retry-After"]) > 0
