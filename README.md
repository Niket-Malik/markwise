# Markwise

**Pre-publication brand governance that explains, cites and suggests, and leaves the decision to people.**

Markwise checks draft copy against a workspace's confirmed rules and facts. It shows the cited source, severity, confidence, and a suggested next step. It does not edit or publish content automatically.

## Current implementation

- React/Next.js workspace UI with sign-in/registration, review submission, review history, knowledge confirmation, insights, and upload controls.
- FastAPI API with organisation-scoped users, password hashing, signed bearer sessions, role checks, persisted rules/facts/glossary entries, reviews, findings, and audit events.
- Admin-confirmed knowledge: new entries remain pending until an admin confirms them.
- API endpoints persist team members, reviewer assignments, review decisions, comments, review content versions, and audit events.
- Deterministic literal restricted-term checks and percentage-claim checks. Unsupported percentage claims are explicitly marked `CANNOT VERIFY` when no active matching fact exists.
- Gemini-first text and vision analysis (`gemini-3.8-flash` → `gemini-3.7-flash`) with OpenAI fallback (`gpt-6-luna` → `gpt-5.6-luna`), strict structured JSON, bounded retry/circuit recovery, workspace AI opt-in, approved-source RAG, citation checks, and explicit abstention. HTTP 429/quota switches providers without trying the same provider's alternate model. OpenAI `text-embedding-3-small` remains the dedicated 1536-dimensional embedding provider; live provider calls require credentials.
- PDF text extraction, scanned PDF/image OCR (system Tesseract required), DOCX guideline parsing, and admin-pending imported passages.
- Image reviews with a configured vision-capable model, optional supplied logo reference, approved-palette sampling, OCR-based contrast estimates, and alt-text checks. Visual findings cite retrieved rules or the applicable W3C criterion; low-confidence measurements remain review suggestions.
- Tracked uploads saved through an organization-scoped local or S3-compatible storage adapter, with an admin upload inventory/deletion screen and scheduled retention purge.
- Review and upload retention settings, scheduled expiry, review deletion, reasoned finding overrides, applyable suggestions saved as new review versions, and per-process rate limiting.
- PostgreSQL/pgvector development database container, Docker Compose, and example environment configuration.

This is an initial foundation, not the complete proposal acceptance. Check [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for the verified scope and outstanding work. Do not use it for production decisions until the remaining security and workflow work has been completed and reviewed.

## Local development

Requirements: Docker Desktop (or Docker Engine with Compose), Node.js 22+, and a modern browser.

1. Copy `.env.example` to `.env`, replace `SECRET_KEY` (at least 32 random characters) and `POSTGRES_PASSWORD`, and keep `DATABASE_URL` blank to let Compose build the matching database URL. For AI, set `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, Gemini text/vision models, OpenAI fallback credentials/models, and `EMBEDDING_MODEL=text-embedding-3-small` as shown in the example. Do not commit `.env`.
2. Start services from the repository root:

   ```powershell
   Copy-Item .env.example .env
   docker compose up --build
   ```

3. Open [http://localhost:3000](http://localhost:3000). Create a workspace; the first account becomes its admin.
4. API documentation is at [http://localhost:8000/docs](http://localhost:8000/docs), and health is at [http://localhost:8000/health](http://localhost:8000/health).

The backend applies the checked-in Alembic migration before it starts in Docker. For a local backend process, run `alembic -c backend/alembic.ini upgrade head` from the repository root after installing `backend/requirements.txt`. Review and back up the database before applying future schema migrations.

## Run without Docker

Backend: create a Python 3.12 virtual environment, install `backend/requirements.txt`, set `DATABASE_URL` (SQLite works for local exploration) and a unique `SECRET_KEY` of at least 32 characters, then run `uvicorn app.main:app --reload --app-dir backend` from the repository root. Frontend: `cd frontend`, install dependencies with `npm install`, then use `npm run dev`. Set `NEXT_PUBLIC_API_URL` to the API origin.

## Checks

Install `backend/requirements-dev.txt`, set a test-only `SECRET_KEY` of at least 32 characters, then run `pytest backend/tests -q` from the repository root. The backend integration suite covers workspace isolation, authorization, knowledge confirmation, citations and abstention, review history/audit, OCR/DOCX, image analysis, mocked provider and S3 adapters, retention, upload validation, and rate limiting. Build and smoke-test the frontend with `cd frontend; npm run build; npm test`. Check schema drift with `alembic -c backend/alembic.ini check` after setting `DATABASE_URL` to the database to inspect. Backend tests use SQLite and mocks; frontend checks validate the generated production login markup, focus styling, and responsive breakpoint. These checks do not verify live services or interactive browser workflows.

## Configuration

See `.env.example`. PostgreSQL accepts `postgresql://` or `postgres://` URLs (including Neon URLs) and normalizes them to psycopg 3. For local backend and Alembic commands, copy `backend/.env.example` to `backend/.env` and enter the Neon pooled `DATABASE_URL` there; remote PostgreSQL connections require TLS. This file is ignored by Git. The root `.env` remains the Docker Compose configuration. `CORS_ORIGINS` is a comma-separated allowlist. Uploaded file limits are 15 MB (reference logos 5 MB). Never commit `.env` or production secrets.

The Gemini adapter uses the current Interactions API with structured JSON output and `store=false`; OpenAI is the compatible secondary for text and image analysis. Configure the Gemini and OpenAI variables listed in `backend/.env.example`, then enable external AI in each workspace's privacy settings. Provider findings retain approved-source checks, citations, unsupported-claim `CANNOT VERIFY`, and guarded suggestions. Internal provider retries do not replay database writes; if both providers fail, the review is saved with a retryable AI status. OpenAI `text-embedding-3-small` stays at 1536 dimensions for the existing pgvector column; do not mix embedding models without reindexing all source chunks. Provider health/circuit state is in-process and shown in settings; live provider behavior requires testing with credentials. OCR uses Tesseract; Docker installs English Tesseract. DOCX imports become pending candidates and need admin confirmation. For Supabase Storage, configure `STORAGE_BACKEND=s3`, `S3_ENDPOINT_URL`, `S3_BUCKET=markwise-assets`, `S3_REGION`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` in the ignored `backend/.env`; leave `S3_SERVER_SIDE_ENCRYPTION` empty because the Supabase S3 endpoint rejects SSE headers. Use the endpoint and region from Supabase S3 settings. The adapter uses Signature V4 and path-style requests; generated Supabase S3 credentials are server-only and bypass bucket RLS, so keep them off the frontend and keep the bucket private. Run `python -m backend.app.storage_check` from the repository root to upload, download, check anonymous public access, and delete a temporary object. No application signed URL or download route currently exists. AWS S3 and Cloudflare R2 endpoints remain supported by the generic adapter. Admins can configure retention (0 disables expiry); Compose and Render schedule purge jobs.

## Deployment

No public URL has been deployed or verified. A production deployment needs managed PostgreSQL with pgvector, Supabase Storage S3, TLS, managed secrets, a configured AI/OCR provider, shared edge rate limiting, monitoring, backups, and correct frontend/backend origins. Alembic migrations are used for schema management. Docker Compose startup and live production configuration have not been verified. A Render Blueprint is prepared in [render.yaml](render.yaml) for frontend, backend, and retention scheduling; [DEPLOYMENT.md](DEPLOYMENT.md) describes the Neon pgvector and Supabase Storage setup and required secrets. There is no public deployment yet: a GitHub origin exists but its visibility and ownership are unverified; Render authorization and Docker are unavailable here.

## Security notes

- Passwords use PBKDF2-HMAC-SHA256 with a per-account random salt.
- Signed bearer tokens expire; the browser currently keeps its token in memory, so a page reload signs the user out.
- Routes scope records to the authenticated organisation, enforce role checks for privileged actions, and return basic response security headers. Login and upload endpoints have an in-process rate limit; public deployments must also configure a shared ingress rate limit across replicas.
- This is not a completed security review. Add CSRF-safe cookie sessions or refresh-token handling and an account invitation/recovery workflow. Verify Supabase bucket privacy, storage credentials, HTTPS, shared rate limiting, and logs before deployment.
