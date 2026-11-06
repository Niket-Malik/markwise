# Markwise

**Pre-publication brand governance that explains, cites, and suggests while leaving the decision to people.**

Markwise helps teams catch brand and factual issues before publication. Brand rules and approved facts often live in documents that are easy to overlook, while many people and tools produce content quickly. Markwise checks drafts against a workspace's confirmed guidance, explains findings with source references, and offers guarded suggestions. People make every final decision; Markwise does not publish content.

## Features implemented

- Workspace registration, login, team roles, review assignments, comments, decisions, review versions, and audit events.
- Admin-confirmed knowledge entries from manually entered rules/facts and imported PDF or DOCX guidelines. Imported passages stay pending until an admin confirms them.
- Deterministic restricted-term and percentage-claim checks, plus AI-assisted tone, claim, and image analysis when configured and enabled for that workspace.
- PostgreSQL/pgvector embeddings and workspace-scoped retrieval over approved sources. Findings are validated against those sources; unsupported claims can return `CANNOT VERIFY` rather than an invented fact or citation.
- Guarded, intent-aware suggestions. A suggestion can be edited, accepted, or dismissed with a reason; applying it creates a review version. The original draft is not silently replaced.
- PDF text extraction, scanned PDF/image OCR (Tesseract; English is installed in the Docker image), and DOCX guideline parsing.
- Image review for palette checks, OCR-derived contrast estimates, alt-text checks, and optional vision analysis. A reference logo can be supplied for comparison. Visual measurements are heuristics and are not a formal accessibility certification or pixel-perfect logo verification.
- Local or S3-compatible upload storage, upload inventory/deletion for admins, per-workspace review and upload retention settings, and an automatic retention job.
- Workspace-scoped dashboard data for recurring finding types, dismissed findings, contested cited rules, and unsupported-claim guidance gaps.
- Responsive Next.js interface, keyboard focus styles, labelled forms, navigation landmarks, and a skip link.

The Round 1 proposal also describes broader future workflows such as slide-deck review, integrations, SSO, and automatic routing to named reviewers. Those are not represented here as implemented features. See [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for the current verified scope.

## Architecture and technology

| Layer | Implementation |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, CSS |
| API | Python 3.12, FastAPI, Pydantic Settings |
| Persistence | SQLAlchemy 2, Alembic, PostgreSQL, pgvector; SQLite is used for local exploration and automated backend tests |
| Documents and images | pypdf, python-docx, pypdfium2, Tesseract OCR, Pillow |
| AI | Gemini Interactions API for primary text/vision analysis; OpenAI-compatible API for secondary text/vision analysis and embeddings |
| File storage | Local filesystem or S3-compatible storage (including Supabase Storage S3) |
| Runtime | Docker Compose for local services; Render Blueprint for prepared deployment |

The API owns authentication, authorization, workspace scoping, review workflows, retrieval, provider orchestration, and storage access. The browser calls the API with a bearer token. Database migrations are in `backend/migrations/versions`; the API runs them at Compose startup and Render runs them in its pre-deploy phase.

## Project structure

```text
markwise_app/
├── backend/
│   ├── app/
│   │   ├── main.py                 # API, models, auth, review and dashboard routes
│   │   ├── ai.py                   # Gemini/OpenAI adapters, retries and failover
│   │   ├── retrieval.py            # Approved-source retrieval
│   │   ├── document_processing.py  # PDF, OCR and DOCX parsing
│   │   ├── visual_analysis.py      # Local image and accessibility heuristics
│   │   ├── storage.py              # Local/S3 object storage
│   │   ├── retention.py            # Scheduled purge entry point
│   │   └── storage_check.py        # Live S3 upload/download/privacy probe
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/                # Alembic schema migrations
│   ├── tests/
│   │   ├── test_api.py              # API, workflow, storage and security checks
│   │   └── test_ai_failover.py      # Mocked provider failover tests
│   ├── .env.example               # Backend-only configuration template
│   ├── .dockerignore
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── app/                        # Next.js page, layout and styles
│   ├── tests/ui-smoke.test.mjs     # Built-UI smoke/accessibility checks
│   ├── Dockerfile
│   ├── .dockerignore
│   ├── package.json
│   └── package-lock.json
├── .env.example                   # Docker Compose template
├── .github/workflows/ci.yml       # GitHub Actions checks
├── .gitignore
├── docker-compose.yml             # pgvector, API, web and retention worker
├── render.yaml                    # Render API, web and scheduled worker blueprint
├── DEPLOYMENT.md
├── IMPLEMENTATION_CHECKLIST.md
├── PROJECT_STATUS.md
├── TODO.md
└── README.md
```

Local `.env` files, uploaded content, local databases, dependency directories, and generated build/test output are intentionally omitted from this tree and excluded by `.gitignore`. The original proposal PDF was available for this audit but is not checked into the project.

## Prerequisites

- Docker Desktop or Docker Engine with the Compose plugin for the all-in-one local setup.
- Node.js 22 and npm for frontend development outside Docker.
- Python 3.12 for backend development outside Docker.
- Tesseract OCR for local scanned-document/image OCR. The backend Docker image installs Tesseract with English data.
- Optional service accounts and credentials for Neon PostgreSQL, Supabase Storage S3, Gemini, and OpenAI. Live AI analysis requires working provider accounts, enabled models, and available quota.

## Local development with Docker Compose

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
```

Edit the root `.env`: set a unique random `SECRET_KEY` (at least 32 characters) and `POSTGRES_PASSWORD`. Keep `DATABASE_URL` blank so Compose uses its local PostgreSQL/pgvector service. AI and S3 settings can remain unconfigured for local deterministic review and local file storage; set provider credentials only if you want external AI and S3-backed files. Never commit `.env`.

Start the stack:

```powershell
docker compose config
docker compose up --build
```

Open the frontend at [http://localhost:3000](http://localhost:3000), the API health endpoint at [http://localhost:8000/health](http://localhost:8000/health), and the FastAPI docs at [http://localhost:8000/docs](http://localhost:8000/docs). Register to create a workspace; that account is its first admin. Compose waits for PostgreSQL health, applies Alembic migrations before starting the API, then starts the frontend and a separate periodic retention worker.

The root `.env` configures Compose. For a backend or Alembic process run directly on the host, settings are read from `backend/.env`; see [Configuration](#configuration) below.

## Run backend and frontend without Docker

Create and activate a Python virtual environment from the repository root, then install backend dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
```

Set `DATABASE_URL` and a unique `SECRET_KEY` of at least 32 characters in `backend/.env`. For SQLite exploration, a local value is `sqlite:///./markwise.db`; for Neon use the PostgreSQL setup below. Start the API from the repository root:

```powershell
python -m uvicorn app.main:app --reload --app-dir backend --port 8000
```

For the frontend, in another terminal:

```powershell
cd frontend
npm ci
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev
```

Then open [http://localhost:3000](http://localhost:3000). Keep the API origin in `NEXT_PUBLIC_API_URL` aligned with the backend `CORS_ORIGINS` allowlist.

## Configuration and secrets

- Root `.env.example` is the Docker Compose template; copy it to the ignored root `.env` for Compose.
- `backend/.env.example` is the template for direct backend/Alembic use; copy it to the ignored `backend/.env`.
- Both templates use placeholders. Enter actual secrets only in the ignored environment file or a deployment provider's protected secret settings. Do not put secrets in the frontend environment, source, issue tracker, or chat.
- `SECRET_KEY` must be a unique random value of at least 32 characters. `CORS_ORIGINS` is a comma-separated list of exact allowed origins. `NEXT_PUBLIC_API_URL` is public and is compiled into the frontend build.
- Other settings include token expiry, upload directory, OCR language, AI timeout/retry/circuit values, storage backend, and retention interval. See the two example files for exact variable names and defaults.

## Neon PostgreSQL and migrations

Neon is compatible with the SQLAlchemy/psycopg 3 configuration. Put the Neon pooled URL in `backend/.env` as `DATABASE_URL`, retaining TLS, for example:

```dotenv
DATABASE_URL=postgresql://USER:PASSWORD@HOST/DATABASE?sslmode=require
```

This is a placeholder format, not a real connection string. The application and Alembic normalize PostgreSQL URLs to the `postgresql+psycopg` driver, use psycopg 3's non-prepared mode, and require TLS for remote PostgreSQL hosts. Keep the existing `vector(1536)` embedding column paired with OpenAI `text-embedding-3-small`; changing the embedding model/dimension requires a coordinated re-embedding and migration plan.

With the Python dependencies installed and `backend/.env` configured, run from the repository root:

```powershell
python -m alembic -c backend/alembic.ini upgrade head
python -m alembic -c backend/alembic.ini current
python -m alembic -c backend/alembic.ini check
```

Migrations create the application schema and pgvector extension. Back up a database before schema changes. Do not point test commands at a production database.

## Supabase Storage (S3-compatible)

The storage adapter supports local files or S3-compatible storage. For the private Supabase bucket, set these values in `backend/.env` for a direct backend process, or root `.env` for Compose:

```dotenv
STORAGE_BACKEND=s3
S3_ENDPOINT_URL=https://<project-ref>.storage.supabase.co/storage/v1/s3
S3_BUCKET=markwise-assets
S3_REGION=<region-from-supabase-s3-settings>
S3_ACCESS_KEY_ID=<server-side-access-key-id>
S3_SECRET_ACCESS_KEY=<server-side-secret-access-key>
S3_SERVER_SIDE_ENCRYPTION=
```

Copy the endpoint and region from Supabase Project Settings → Storage → S3. Supabase S3 uses HTTPS, Signature V4, and path-style requests. Its S3 API rejects the SSE request header, so leave `S3_SERVER_SIDE_ENCRYPTION` empty. Keep the bucket private and credentials server-side: generated Supabase S3 credentials bypass bucket RLS. The application currently has no presigned-URL generator or user download route.

To test configured storage, run `python -m backend.app.storage_check` from the repository root. This makes a live upload, authenticated server-side download, anonymous-access denial check, and cleanup of a temporary probe object. It requires valid S3 settings and changes the bucket only by creating and deleting that probe object.

## Gemini, OpenAI, retrieval, and failover

The configured order is:

| Use | Provider/model order |
| --- | --- |
| Text and vision primary | Gemini `gemini-3.8-flash` → `gemini-3.7-flash` |
| Text and vision secondary | OpenAI `gpt-6-luna` → `gpt-5.6-luna` |
| Embeddings | OpenAI `text-embedding-3-small`, 1536 dimensions |

Configure `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, the four `GEMINI_*_MODEL*` settings, `AI_FALLBACK_PROVIDER=openai_compatible`, `AI_FALLBACK_API_BASE_URL`, `AI_FALLBACK_API_KEY`, the four `AI_FALLBACK_*_MODEL*` settings, `EMBEDDING_PROVIDER=openai_compatible`, and `EMBEDDING_MODEL=text-embedding-3-small`. The example files contain the exact keys and model defaults. A workspace admin must enable external AI in that workspace's privacy settings before content is sent to a provider. Gemini requests use the Interactions API with structured JSON output and `store=false`; the OpenAI-compatible adapter enforces a structured output schema.

The review flow retrieves approved rules/facts from the same workspace, supplies that evidence to the model, validates returned citations against approved sources, and abstains with `CANNOT VERIFY` when the evidence does not support a claim. Provider/model fallback preserves that request context. Vision requests only use configured vision-capable models; the system does not silently substitute a text-only model.

Temporary model-specific failures (such as timeouts, connection failures, and retryable 5xx responses) use bounded retries with exponential backoff, jitter, and `Retry-After`, then can try that provider's alternate model. A provider-level 429/quota response skips its alternate model and moves to the other provider because quota is commonly shared. Provider health uses an in-process circuit breaker and cooldown; a half-open request probes recovery so traffic can return to Gemini after it becomes healthy. Invalid credentials, malformed requests, and unsupported models are not retried indefinitely. If all providers fail, analysis reports a retryable failure and the submitted review is retained. Provider status is exposed to workspace admins without returning API keys or content.

Live AI provider availability and quota depend on the configured API accounts, project limits, model access, and service health. Mocked tests do not prove live provider availability. Do not assume the optional external AI path will be available during a demo without checking account quota and configuration beforehand.

## API endpoints and authentication

The API docs at `/docs` expose the OpenAPI schema. Main application routes are:

| Area | Routes |
| --- | --- |
| Health | `GET /health` |
| Authentication | `POST /api/auth/register`, `POST /api/auth/login`, `GET /api/auth/me` |
| Team | `GET /api/team`, `POST /api/team` |
| Privacy and provider settings | `GET/PUT /api/settings/privacy`, `GET /api/settings/providers` |
| Retention | `GET/PUT /api/settings/retention` |
| Knowledge | `GET/POST /api/knowledge`, `POST /api/knowledge/import`, `POST /api/knowledge/{item_id}/approve` |
| Reviews | `POST/GET /api/reviews`, `POST /api/reviews/{review_id}/submit`, `/assign`, `/decision`, `GET/POST /api/reviews/{review_id}/comments`, `GET /api/reviews/{review_id}/versions`, `PATCH/DELETE /api/reviews/{review_id}` |
| Audit and finding decisions | `GET /api/audit-events`, `POST /api/findings/{finding_id}/decision`, `GET /api/findings/{finding_id}/decisions` |
| Assets and visual reviews | `POST /api/uploads`, `GET/DELETE /api/uploads` (delete includes an asset ID), `POST /api/visual-reviews` |
| Dashboard | `GET /api/dashboard` |

Register with a workspace name, name, email, and password of at least 12 characters. Registration creates the workspace's first admin account. Login returns a bearer token; send it as `Authorization: Bearer <access_token>` to protected routes. Tokens expire according to `ACCESS_TOKEN_MINUTES` (30 by default). Admins manage workspace knowledge, members, retention, uploads, and provider status; other actions enforce the role rules in the API. The current frontend keeps the token in memory, so reloading the page signs the user out.

## Tests and build

Backend tests use SQLite and mocked AI/S3 adapters. From the repository root, install dev dependencies and explicitly select a test-only database and secret (these PowerShell values exist only in the current terminal):

```powershell
python -m pip install -r backend/requirements-dev.txt
$env:DATABASE_URL = "sqlite://"
$env:SECRET_KEY = "local-test-secret-change-this-value-32-chars"
python -m pytest backend/tests -q
```

The backend tests cover API/workflow behavior, workspace isolation, authorization, knowledge confirmation, citation checks and abstention, history/audit, OCR/DOCX, image review, mocked provider failover, mocked S3 behavior, retention, file validation, rate limiting, and 1536-dimensional embedding validation. They do not contact live AI providers, Neon, or Supabase.

Build and run the frontend smoke/accessibility checks from the frontend directory:

```powershell
cd frontend
npm ci
npm run build
npm test
```

The Node tests inspect the built production markup and stylesheet for labelled auth controls, a responsive viewport, keyboard focus styling, a skip link, and navigation semantics. They are not browser end-to-end or screen-reader certification tests. The UI tests require the production build to have completed first.

## Render deployment

`render.yaml` prepares a Render Blueprint with a Docker FastAPI web service, Docker Next.js web service, and daily retention cron job. It is a deployment configuration, not evidence of a live deployment. No public frontend/backend URL is currently verified.

1. Connect the intended repository to Render and create the Blueprint from `render.yaml`.
2. Set protected service values for Neon `DATABASE_URL` (with `sslmode=require` and `vector` enabled), Supabase S3 endpoint/bucket/region/access keys, `GEMINI_API_KEY`, and `AI_FALLBACK_API_KEY`. Render generates `SECRET_KEY`; do not put secrets in the repository or frontend settings.
3. Confirm the backend `CORS_ORIGINS` exactly matches the actual HTTPS frontend origin. The Blueprint currently contains an example Render hostname; update it if Render assigns a different host. Verify the frontend's `NEXT_PUBLIC_API_HOST`/API URL points to the actual backend host.
4. Deploy and inspect the build, Alembic pre-deploy migration, API `/health`, frontend HTTPS response, and cron logs. Verify the retention worker receives the required database and S3 settings.
5. Use a dedicated test workspace to verify registration/login, role restrictions and workspace boundaries, private upload, admin-confirmed guideline import, AI consent, real analysis/citations, review decisions/history, dashboard data, retention deletion, and CORS. Run edge-level shared rate limiting, monitoring, backups, and HTTPS checks before inviting users.

See [DEPLOYMENT.md](DEPLOYMENT.md) for detailed external-service setup and release checks. Render authorization, production service startup, live AI workflow, scheduled purge, and public URLs must be verified in the target accounts; do not treat the Blueprint hostname as a live URL.

## Security, known limitations, and demo

- Passwords are hashed with PBKDF2-HMAC-SHA256 and per-account random salts. Bearer tokens expire, routes check workspace ownership and roles, and the backend adds basic response security headers.
- Rate limits are in-process, so multiple production replicas need a shared ingress/edge rate limiter. Token storage is in browser memory. Email invitations, account recovery, and CSRF-safe cookie/refresh-token flows are not implemented.
- External AI is workspace opt-in, but opted-in draft/image content and retrieved approved sources are sent to configured providers. Configure provider privacy and data-retention settings before use. AI findings still require human review.
- OCR contrast and image/logo checks are heuristic. Markwise does not guarantee full WCAG conformance, exact logo identity, or automated publication approval.
- Supabase server-side S3 credentials bypass bucket RLS. Keep the bucket private and keys server-only. Review retention values before enabling automatic purge; `0` means no automatic expiry.
- A complete production security review, browser end-to-end workflow, shared rate-limit service, monitoring/alerting, backup-restore drill, Docker Compose runtime, and Render deployment remain unverified. Check [PROJECT_STATUS.md](PROJECT_STATUS.md) and [TODO.md](TODO.md) before relying on an item as complete.

**Screenshots/demo:** No screenshots are currently checked into the repository, and no public demo URL has been verified. Run the local application using the steps above; use `/docs` to inspect the API.
