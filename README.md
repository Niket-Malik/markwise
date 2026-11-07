# Markwise

**Pre-publication brand governance that explains, cites, and suggests while leaving decisions to people.**

Markwise helps teams check draft text and images against confirmed brand guidance and approved facts before publication. It surfaces evidence-backed findings, abstains with `CANNOT VERIFY` when support is missing, and lets people decide how to handle findings. Markwise does not publish content.

## Features

- Workspace registration and bearer-token login; workspace-scoped accounts, reviews, knowledge, dashboard data, and audit events.
- Admin-confirmed knowledge entries from manual rules/facts or PDF/DOCX guideline imports. Imported passages remain pending until an Admin confirms each passage.
- PDF text extraction, scanned PDF/image OCR using Tesseract, and DOCX guideline parsing. Imported originals are retained as private tracked uploads when storage is configured.
- AI-assisted text and visual review, approved-source retrieval, citation validation, `CANNOT VERIFY` outcomes, and guarded contextual rewrite suggestions.
- Deterministic fallback checks against approved knowledge when external AI is unavailable. This is labelled as a Knowledge Base fallback and is not represented as a completed AI review.
- Per-finding Apply, Edit suggestion, and Dismiss-with-reason actions. Decisions, content versions, and finding history are persisted; one finding action does not regenerate sibling findings or trigger a complete AI re-analysis.
- Image checks for palette, OCR-derived contrast estimates, alt text, and optional vision analysis. A user-supplied reference logo can be compared. Visual checks are heuristics, not formal accessibility certification or pixel-perfect identity verification.
- Workspace and team administration, role-based review assignments/decisions, comments, retention controls, and audit history.
- Admin-only workspace edit/delete; team member edit/remove; knowledge rule edit/delete; and deletion of approved/rejected reviews.
- Local or S3-compatible object storage, including Supabase Storage S3; an automated retention worker is configured for Compose and Render.
- Real-data workspace dashboard insights for review counts, recurring findings, dismissed/overridden findings, contested cited rules, and guidance gaps.
- Responsive Next.js UI with keyboard focus styling, labelled forms, navigation landmarks, and a skip link.
- Custom forest-green Markwise branding, including a browser favicon, Apple Touch Icon, app shortcut icons, and web app manifest.

The original proposal also describes future ideas such as SSO, slide-deck review, CMS integrations, invitations, and multilingual/on-prem workflows. These are not claimed as implemented. See [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md).

## Architecture and technology stack

| Layer | Implementation |
| --- | --- |
| Frontend | Next.js 16, React 19, TypeScript, CSS |
| API | Python 3.12, FastAPI, Pydantic Settings |
| Persistence | SQLAlchemy 2, Alembic, PostgreSQL with pgvector; SQLite for isolated local tests |
| Document/image processing | pypdf, python-docx, pypdfium2, Tesseract OCR, Pillow |
| AI analysis | OpenRouter Nemotron Super VL primary; Gemini Flash secondary when configured and consented |
| Embeddings | OpenRouter Nemotron Embed, 1536-dimensional vectors in PostgreSQL `vector(1536)` |
| Object storage | Local filesystem or S3-compatible storage (Supabase Storage S3 supported) |
| Deployment | Docker Compose for local services; Render Blueprint for prepared deployment |

FastAPI owns authentication, authorization, workspace scoping, reviews, retrieval, provider orchestration, and storage access. The browser calls the API using a bearer token. Alembic migrations live in `backend/migrations/versions`; Compose applies them before starting the API and Render is configured with a pre-deploy migration command.

## AI providers and model configuration

- **Text/vision primary:** OpenRouter `nvidia/nemotron-3.5-super-vl:free`, via the OpenAI-compatible Chat Completions endpoint with structured JSON output and image input.
- **Text/vision fallback:** Google Gemini Interactions API. Primary model `gemini-3.5-flash`; alternate `gemini-3.5-flash-lite` for text and vision.
- **Embeddings:** OpenRouter `nvidia/nemotron-3.5-embed-1b:free`. Markwise requests 1536 dimensions and validates the output before storing it.
- Model IDs and retry/circuit settings are configurable with `OPENROUTER_*`, `GEMINI_*`, `EMBEDDING_*`, and `AI_*` variables. See [backend/.env.example](backend/.env.example) and [render.yaml](render.yaml); examples contain placeholders, never real secrets.

When both AI providers are configured and the workspace has enabled both required permissions, provider order is OpenRouter then Gemini. Provider calls use bounded retries for retryable failures, quota-aware model/provider failover, circuit cooldown, and recovery. Vision fallback is restricted to models marked vision-capable. Failed analysis remains incomplete; it is not shown as a clean AI review. Live provider availability and quota depend on the configured accounts and may change.

The backend retains a legacy OpenAI-compatible adapter for installations that configure `AI_PROVIDER=openai` or `openai_compatible` and `AI_API_KEY` while neither OpenRouter nor Gemini is configured. That compatibility path still requires general external-AI consent. It is not used by the default OpenRouter/Gemini configuration, and OpenAI embeddings are not used.

### Important: Enable AI Review Permissions

An Admin must explicitly enable workspace consent before external AI processing. The settings are **disabled by default**.

In Markwise, open **Settings** and enable:

1. **Allow external AI analysis** — permits consented review content to be sent to an external AI provider.
2. **Allow OpenRouter free models and embeddings** — separately permits OpenRouter free models and OpenRouter embeddings. The first permission must already be enabled before this setting can be turned on.

Both permissions are required for the OpenRouter-based AI review and embedding workflow. If general external-AI consent is off, no external AI analysis should run. If OpenRouter-specific consent is off, OpenRouter analysis and embeddings are unavailable; configured Gemini may still be used for analysis when general consent is on, but Gemini does not replace the OpenRouter embedding workflow. If no eligible provider is configured and consented, AI analysis is unavailable and the app uses its clearly labelled Knowledge Base fallback where possible.

OpenRouter free-provider inputs may be logged and used to improve provider products. **Do not enable these permissions for confidential or personal data.** Review provider terms and your organisation's data policy before opting in. Consent withdrawal prevents later requests and clears stored OpenRouter embeddings/cache; a request already sent to an external provider cannot be cancelled.

## Knowledge Base, guideline import, and embeddings

Admins can add rules/facts manually or import PDF/DOCX through **Knowledge base → Admin tool → Import a guideline**. Extraction creates candidate passages and stores the source file; candidates are pending and do not influence review until an Admin confirms them. Scanned PDFs/images use the Tesseract OCR path. The original file is a tracked private object and extracted text/passages are database records.

On confirmation, Markwise creates searchable chunks and attempts an OpenRouter embedding request immediately, provided both external-AI and OpenRouter consent are enabled and embedding configuration is valid. Successful 1536-dimensional vectors are saved to the chunk's pgvector column. If provider configuration, quota, rate limits, or network access prevent the request, the knowledge is still activated and chunks remain with null embedding values for lexical retrieval. This confirmation path does not enqueue an automatic retry. Admins can use **Settings → Resume missing source embeddings** to request a bounded, resumable backfill. Enabling/saving privacy settings with both consents enabled can also schedule a backfill. Backfill provider failures pause the pass; they do not create an uncontrolled retry loop.

Migration 0007 tags each vector with an embedding-space fingerprint so old OpenAI embeddings are not compared against Nemotron vectors. The PostgreSQL vector dimension remains `1536`; migration 0008 adds workspace consent-generation fields. Migration commands should be run only against the intended database after taking a backup; see [DEPLOYMENT.md](DEPLOYMENT.md).

Approved-source retrieval uses workspace-scoped pgvector cosine search when compatible vectors exist and lexical retrieval as a fallback. Citations must refer to approved knowledge and validated excerpts. When evidence cannot support a factual claim, Markwise uses `CANNOT VERIFY`; it does not invent a citation. The deterministic Knowledge Base fallback is a separate, incomplete review mode and an empty fallback result is not proof of compliance.

## Review and finding workflow

Submitting a text or image review persists the submitted content and analysis state. The review uses approved workspace knowledge for retrieval and AI analysis only when consent and a capable provider are available. If AI analysis fails, the review remains incomplete and the app presents Knowledge Base fallback findings/abstentions with an **AI Analysis Unavailable** status. A user may select **Retry AI Analysis**; retries are manual after the initial submission and use the configured bounded provider failover.

Each active finding may offer a contextual rewrite where a safe rewrite is available. **Apply suggestion** updates the applicable draft span, writes a content version and finding decision, and resolves only that finding. **Edit suggestion** persists the edited suggestion on the existing finding. **Dismiss with reason** persists a required reason and terminal decision. None of these actions launches a full AI re-analysis or recreates sibling findings. **Finding History** loads persisted applied/dismissed decisions and associated audit/decision data after refresh. A generated rewrite does not itself resolve a `CANNOT VERIFY` finding.

## Roles and admin-only management

The application role value `creator` is the content creator/member role.

| Role | Current capabilities |
| --- | --- |
| Admin | Workspace-wide administration, member and role management, knowledge import/confirmation/edit/delete, privacy/provider and retention settings, upload management, review assignment/decisions, audit access, and workspace edit/delete. Admins can delete approved/rejected reviews. Last Admin cannot be demoted or removed. |
| Manager | Workspace review management, assignment, review decisions, and audit access. Cannot perform Admin-only workspace/team/knowledge/settings operations. |
| Reviewer | Can decide reviews assigned to them and participate in permitted review/finding workflows. Cannot use Admin-only management operations. |
| Member (`creator`) | Can create and work with own reviews and participate in workspace review/comment workflows allowed by the API. Cannot use Admin-only management operations. |

Backend endpoints enforce roles and workspace IDs; UI visibility alone is not the access control. Cross-workspace records are not available to another workspace. Admin-only actions include workspace edit/delete, team edit/remove, knowledge rule edit/delete, and approved/rejected review deletion. Review deletion removes its associated findings, decisions, comments, assignments, and versions while retaining the audit event. Workspace deletion is destructive and removes workspace-associated application data and stored assets.

## Database and storage

Neon PostgreSQL is supported through SQLAlchemy/psycopg 3 with TLS; pgvector stores `vector(1536)` embeddings. Alembic owns schema changes. Local development and automated tests can use SQLite, but SQLite checks do not establish PostgreSQL compatibility.

The storage adapter supports local files and private S3-compatible buckets. Supabase S3 uses the project endpoint, region, private bucket, and server-only S3 access keys. Supabase S3 does not accept the AWS server-side-encryption request header, so the example leaves that setting blank. There is no public/presigned download URL flow; object reads are server-side. Review/upload retention is controlled per workspace; `0` means no automatic expiry. Compose runs a periodic retention worker and Render has a daily cron service in its Blueprint.

## Project structure

```text
markwise_app/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, SQLAlchemy models and API routes
│   │   ├── ai.py             # OpenRouter/Gemini adapters, embeddings and failover
│   │   ├── retrieval.py      # Approved-source semantic/lexical retrieval
│   │   ├── document_processing.py # PDF/OCR/DOCX parsing
│   │   ├── visual_analysis.py # Image heuristics and vision preparation
│   │   ├── storage.py        # Local/S3 storage adapter
│   │   ├── storage_check.py  # Opt-in S3 connectivity probe
│   │   └── retention.py      # Retention purge entry point
│   ├── migrations/
│   │   ├── env.py
│   │   └── versions/         # Alembic migrations 0001–0008
│   ├── tests/
│   │   ├── test_api.py
│   │   ├── test_admin_management.py
│   │   ├── test_ai_failover.py
│   │   ├── test_finding_suggestions.py
│   │   └── test_openrouter_migrations.py
│   ├── .env.example          # Backend environment template (placeholders only)
│   ├── alembic.ini
│   ├── Dockerfile
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── app/
│   │   ├── page.tsx          # Main application UI and workflows
│   │   ├── finding-actions.mjs
│   │   ├── review-status.mjs
│   │   ├── layout.tsx
│   │   └── styles.css
│   ├── public/
│   │   ├── favicon.ico
│   │   ├── icon.svg
│   │   ├── apple-touch-icon.png
│   │   ├── icon-192.png
│   │   ├── icon-512.png
│   │   └── site.webmanifest
│   ├── tests/
│   │   ├── admin-management.test.mjs
│   │   ├── finding-actions.test.mjs
│   │   └── ui-smoke.test.mjs
│   ├── Dockerfile
│   ├── package.json
│   └── package-lock.json
├── .github/workflows/ci.yml  # CI checks
├── .gitignore
├── .env.example              # Docker Compose environment template
├── docker-compose.yml        # pgvector, API, web and retention worker
├── render.yaml               # Render web services and retention cron blueprint
├── DEPLOYMENT.md
├── IMPLEMENTATION_CHECKLIST.md
├── PROJECT_STATUS.md
├── TODO.md
└── README.md
```

## Markwise browser and app branding

The custom icon follows the existing forest-green palette and pairs an **M** monogram with a verification check. It is original Markwise artwork, designed to remain recognizable at small browser-tab sizes.

- `frontend/public/icon.svg` is the scalable SVG favicon.
- `frontend/public/favicon.ico` contains 16px, 32px, and 48px variants for browser tabs and bookmarks.
- `frontend/public/apple-touch-icon.png` is the 180×180 Apple Touch Icon.
- `frontend/public/icon-192.png` and `frontend/public/icon-512.png` are app shortcut icons referenced by `frontend/public/site.webmanifest`.
- `frontend/app/layout.tsx` declares the Next.js metadata title **“Markwise – Intelligent Brand Governance Platform”**, favicon/icon links, manifest, and Apple web-app metadata.

These assets support browser tabs and bookmarks, plus supported mobile browser shortcuts and home-screen installation. The frontend Docker build copies `public/` into the production image, so a Render rebuild/deploy serves the same assets. Browser and operating-system caching can delay icon changes after a deployment.

Local environment files, uploads, database files, dependency directories, and generated build/test output are excluded from the tree and should remain untracked. No screenshots are checked into the repository.

## Prerequisites and local setup

- Docker Desktop/Engine with Compose, or Python 3.12 and Node.js 22 with npm.
- Tesseract OCR when running the backend directly on a host. The backend Docker image installs Tesseract.
- Optional accounts/credentials for Neon, Supabase S3, OpenRouter, and Gemini. Real AI calls require enabled models and available account quota.

### Docker Compose

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
```

Set a unique `SECRET_KEY` (at least 32 characters) and `POSTGRES_PASSWORD` in the ignored root `.env`; leave `DATABASE_URL` blank for the local pgvector service. Configure AI/S3 only when needed. Then run:

```powershell
docker compose config
docker compose up --build
```

Open `http://localhost:3000`, API health at `http://localhost:8000/health`, and FastAPI docs at `http://localhost:8000/docs`. The first registered account is workspace Admin. Compose applies migrations before API startup and runs the retention loop as a separate service.

### Backend and frontend separately

From the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend/requirements.txt
Copy-Item backend/.env.example backend/.env
```

Set `DATABASE_URL` and a strong unique `SECRET_KEY` in the ignored `backend/.env`. For local SQLite exploration, `DATABASE_URL=sqlite:///./markwise.db`; remote PostgreSQL requires TLS. Start the API from the repository root:

```powershell
python -m uvicorn app.main:app --reload --app-dir backend --port 8000
```

In a second terminal:

```powershell
cd frontend
npm ci
$env:NEXT_PUBLIC_API_URL = "http://localhost:8000"
npm run dev
```

The frontend API origin must be allowed by backend `CORS_ORIGINS`.

## Environment configuration

Use the root `.env.example` for Docker Compose or `backend/.env.example` for direct backend/Alembic runs. These templates use placeholders only. Keep actual secrets in ignored `.env` files or protected Render environment settings; do not put credentials in frontend variables, docs, source, Git, or chat.

Important backend settings include `DATABASE_URL`, `SECRET_KEY`, `CORS_ORIGINS`, `STORAGE_BACKEND`, `UPLOAD_DIR`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `AI_PROVIDER`, `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE_URL`, `OPENROUTER_TEXT_MODEL`, `OPENROUTER_VISION_MODEL`, `OPENROUTER_MAX_REQUESTS_PER_MINUTE`, `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL`, `GEMINI_VISION_MODEL`, `GEMINI_TEXT_MODEL_ALTERNATE`, `GEMINI_VISION_MODEL_ALTERNATE`, `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `AI_TIMEOUT_SECONDS`, `AI_PROVIDER_MAX_RETRIES`, `AI_RETRY_BASE_SECONDS`, `AI_RETRY_MAX_SECONDS`, `AI_CIRCUIT_BREAKER_FAILURES`, `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, `ACCESS_TOKEN_MINUTES`, `OCR_LANGUAGE`, and `RETENTION_INTERVAL_SECONDS`. Optional legacy OpenAI-compatible settings are `AI_API_BASE_URL`, `AI_API_KEY`, `AI_MODEL`, and `VISION_MODEL`; these only apply when OpenRouter and Gemini are not configured. `NEXT_PUBLIC_API_URL` and `NEXT_PUBLIC_API_HOST` are frontend build/runtime values and are not secrets. See the environment examples for defaults and placeholder formats.

## Neon PostgreSQL and Alembic

For direct local backend/Alembic use, put the Neon connection URL only in `backend/.env`, with `sslmode=require`, for example:

```dotenv
DATABASE_URL=postgresql://<user>:<password>@<host>/<database>?sslmode=require
```

This is a placeholder, not a connection credential. SQLAlchemy normalizes PostgreSQL URLs to the installed psycopg 3 driver. Remote PostgreSQL connections require TLS. Migrations are run from the repository root with:

```powershell
python -m alembic -c backend/alembic.ini current
python -m alembic -c backend/alembic.ini upgrade head
python -m alembic -c backend/alembic.ini current
```

The checked-in migration head is `0008_consent_generation` after 0007. Back up the intended database before applying migrations and verify the target without printing the URL. Do not use production DATABASE_URL for tests.

## API routes and authentication

Protected routes require `Authorization: Bearer <access_token>`. Register at `POST /api/auth/register`; the first account in a new workspace is Admin. Login is `POST /api/auth/login`, current-user details `GET /api/auth/me`. Tokens expire according to `ACCESS_TOKEN_MINUTES` (default 30). See `/docs` for request/response schemas.

| Area | Routes |
| --- | --- |
| Team | `GET/POST /api/team`, `PATCH/DELETE /api/team/{member_id}` |
| Workspace | `PATCH/DELETE /api/settings/organization` |
| Privacy/providers | `GET/PUT /api/settings/privacy`, `GET /api/settings/providers`, `POST /api/settings/embeddings/backfill` |
| Retention | `GET/PUT /api/settings/retention` |
| Knowledge | `GET/POST /api/knowledge`, `PATCH/DELETE /api/knowledge/{item_id}`, `POST /api/knowledge/import`, `POST /api/knowledge/{item_id}/approve` |
| Reviews | `POST/GET /api/reviews`, `POST /api/reviews/{review_id}/retry-ai`, `/submit`, `/assign`, `/decision`, `GET/POST /api/reviews/{review_id}/comments`, `GET /api/reviews/{review_id}/versions`, `PATCH/DELETE /api/reviews/{review_id}` |
| Findings/history | `POST /api/findings/{finding_id}/decision`, `GET /api/findings/{finding_id}/decisions`, `GET /api/reviews/{review_id}/finding-history` |
| Audit | `GET /api/audit-events` |
| Uploads/vision | `POST /api/uploads`, `GET /api/uploads`, `DELETE /api/uploads/{asset_id}`, `POST /api/visual-reviews` |
| Dashboard/health | `GET /api/dashboard`, `GET /health` |

## Tests and production build

Backend tests use SQLite and mocked provider/storage requests. From the repository root, install dev dependencies and explicitly select test-only settings:

```powershell
python -m pip install -r backend/requirements-dev.txt
$env:DATABASE_URL = "sqlite://"
$env:SECRET_KEY = "local-test-secret-change-this-value-32-chars"
python -m pytest backend/tests -q
```

Frontend tests and build, from `frontend/`:

```powershell
npm ci
npm run build
npm test
```

The build should run before `npm test`, because UI smoke checks inspect production output. Node tests cover selected UI, action, accessibility-markup, and status behavior; they are not full browser end-to-end or screen-reader certification tests. Mocked and SQLite tests do not contact AI providers, Neon, or Supabase.

## Render deployment

`render.yaml` describes a Docker FastAPI web service, Next.js web service, Alembic pre-deploy command, and daily retention cron service. It is configuration, not proof that all services are currently healthy.

1. Connect the repository in Render and review Blueprint service plans.
2. Set protected backend values for Neon `DATABASE_URL` with TLS, Supabase S3 endpoint/bucket/region/access keys, `OPENROUTER_API_KEY`, and optional `GEMINI_API_KEY`. Render generates `SECRET_KEY`.
3. Set backend `CORS_ORIGINS` to the exact frontend HTTPS origin. Verify frontend `NEXT_PUBLIC_API_URL` or `NEXT_PUBLIC_API_HOST` points to the backend service.
4. Confirm migration output, `/health`, frontend HTTPS response, and retention cron logs.
5. Verify registration/login, consent, private upload, guideline confirmation, AI analysis/citations, role boundaries, finding decisions/history, dashboard data, CORS, retention, monitoring, and backups in a dedicated test workspace.

Markwise is already deployed and running. For access to the live application or backend URLs, please contact the project owner. This documentation update did not probe the live deployment. Detailed release steps and limitations are in [DEPLOYMENT.md](DEPLOYMENT.md).

## Security, known limitations, and demo

- Passwords use PBKDF2-HMAC-SHA256 with per-account salts; bearer tokens expire; backend routes enforce roles and workspace scope.
- The frontend keeps the access token in React memory, not persistent storage; a page refresh signs the user out. Invitations, password recovery, and a refresh-token/cookie session flow are not implemented.
- Rate limits are process-local and do not aggregate across workers/instances. Add edge/shared limits before scaling.
- External providers receive opted-in draft/image content and retrieved approved passages. OpenRouter free inputs may be logged/used for provider improvement; do not consent for sensitive data.
- OCR, contrast, palette, and logo checks are heuristic. No formal WCAG certification or exact logo identity guarantee is provided.
- S3 keys are server-only; Supabase S3 keys bypass bucket RLS, so keep the bucket private. Retention value `0` means no automatic expiry.
- Full browser E2E/accessibility review, shared rate limits, Docker runtime verification, and production monitoring/backup drills remain incomplete or unverified. Compose runtime and live provider quotas depend on local tools/accounts.

**Demo/screenshots:** No screenshots are checked into the repository. Markwise is already deployed and running; contact the project owner for live application or backend URLs.
