# Markwise

**Pre-publication brand governance that explains, cites and suggests, and leaves the decision to people.**

Markwise checks draft copy against a workspace's confirmed rules and facts. It shows the cited source, severity, confidence, and a suggested next step. It does not edit or publish content automatically.

## Current implementation

- React/Next.js workspace UI with sign-in/registration, review submission, review history, knowledge confirmation, insights, and upload controls.
- FastAPI API with organisation-scoped users, password hashing, signed bearer sessions, role checks, persisted rules/facts/glossary entries, reviews, findings, and audit events.
- Admin-confirmed knowledge: new entries remain pending until an admin confirms them.
- API endpoints persist team members, reviewer assignments, review decisions, comments, review content versions, and audit events.
- Deterministic literal restricted-term checks and percentage-claim checks. Unsupported percentage claims are explicitly marked `CANNOT VERIFY` when no active matching fact exists.
- OpenAI-compatible provider integration, workspace AI opt-in, approved-source RAG, citation checks, and an explicit abstention path. Live provider calls require credentials and are not enabled by default.
- PDF text extraction, scanned PDF/image OCR (system Tesseract required), DOCX guideline parsing, and admin-pending imported passages.
- Image reviews with a configured vision-capable model, optional supplied logo reference, approved-palette sampling, OCR-based contrast estimates, and alt-text checks. Visual findings cite retrieved rules or the applicable W3C criterion; low-confidence measurements remain review suggestions.
- Tracked uploads saved through an organization-scoped local or S3-compatible storage adapter, with an admin upload inventory/deletion screen and scheduled retention purge.
- Review and upload retention settings, scheduled expiry, review deletion, reasoned finding overrides, applyable suggestions saved as new review versions, and per-process rate limiting.
- PostgreSQL/pgvector development database container, Docker Compose, and example environment configuration.

This is an initial foundation, not the complete proposal acceptance. Check [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for the verified scope and outstanding work. Do not use it for production decisions until the remaining security and workflow work has been completed and reviewed.

## Local development

Requirements: Docker Desktop (or Docker Engine with Compose), Node.js 22+, and a modern browser.

1. Copy `.env.example` to `.env`, replace `SECRET_KEY` (at least 32 random characters) and `POSTGRES_PASSWORD`, and keep `DATABASE_URL` blank to let Compose build the matching database URL. For live AI checks, set `AI_PROVIDER=openai_compatible`, `AI_API_KEY`, `AI_MODEL`, a vision-capable `VISION_MODEL`, and an embeddings model that returns 1536-dimensional vectors. Do not commit `.env`.
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

AI is wired through an OpenAI-compatible API adapter. Configure `AI_PROVIDER=openai_compatible`, `AI_API_BASE_URL`, `AI_API_KEY`, `AI_MODEL`, a vision-capable `VISION_MODEL`, `EMBEDDING_MODEL`, and `AI_TIMEOUT_SECONDS`, then enable external AI in each workspace's privacy settings. Text review also checks claims when no source matches and marks unsupported claims `CANNOT VERIFY`; no unsupported source citations are fabricated. The workspace setting applies to text, image, and embedding calls; image reviews disclose when the image and supplied reference logo are sent. Embeddings must return 1536 dimensions for the configured pgvector column. Enabling embeddings backfills approved source chunks; disabling external AI clears stored vectors. Provider and vector database behavior still needs live verification. OCR uses Tesseract; Docker installs English Tesseract. DOCX imports become pending candidates and need admin confirmation. For object storage, set `STORAGE_BACKEND=s3`, `S3_BUCKET`, credentials, region, optional endpoint URL, and encryption configuration. AWS S3 supports AES256 or KMS; Cloudflare R2 uses managed encryption at rest and the adapter omits unsupported SSE headers. Admins can configure upload and review retention (0 disables expiry); Compose runs a separate purge worker, and Render schedules `python -m app.retention` daily. Live S3/purge execution has not been verified.

## Deployment

No public URL has been deployed or verified. A production deployment needs managed PostgreSQL with pgvector, S3-compatible storage, TLS, managed secrets, a configured AI/OCR provider, shared edge rate limiting, monitoring, backups, and correct frontend/backend origins. Alembic migrations are used for schema management. Docker Compose startup and live production configuration have not been verified. A Render Blueprint is prepared in [render.yaml](render.yaml) for frontend, backend, and retention scheduling; [DEPLOYMENT.md](DEPLOYMENT.md) describes the Neon pgvector and Cloudflare R2 setup and required secrets. There is no public deployment yet: this workspace has no Git remote or hosting credentials, and Docker is unavailable here.

## Security notes

- Passwords use PBKDF2-HMAC-SHA256 with a per-account random salt.
- Signed bearer tokens expire; the browser currently keeps its token in memory, so a page reload signs the user out.
- Routes scope records to the authenticated organisation, enforce role checks for privileged actions, and return basic response security headers. Login and upload endpoints have an in-process rate limit; public deployments must also configure a shared ingress rate limit across replicas.
- This is not a completed security review. Add CSRF-safe cookie sessions or refresh-token handling and an account invitation/recovery workflow. Verify S3 access policies, encryption, HTTPS, shared rate limiting, and logs before deployment.
