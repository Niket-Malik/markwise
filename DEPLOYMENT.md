# Deployment guide

## Deployment state

Markwise is already deployed and running. For access to the live application or backend URLs, please contact the project owner. No deployed URLs are included in this repository documentation. This documentation update did not probe the live services or inspect the current managed-service configuration. The repository also has a Render Blueprint for Docker FastAPI and Next.js services plus a daily retention cron job; that Blueprint documents deployable configuration and does not itself verify current service health.

The latest production Alembic revision explicitly reported by the owner in the migration troubleshooting history was `0006_review_visual_assets`; a failed attempt to upgrade beyond it rolled back. The current checked-in code has migration head `0008_consent_generation`. Confirm the production revision read-only before deploying/applying migrations. Do not assume production is at head.

## Prepared architecture

- **Render:** Backend Docker Web Service (`/health`), frontend Docker Web Service, and daily retention Cron Job. Render terminates HTTPS. The Blueprint has an Alembic pre-deploy command for the backend service and runs `python -m app.retention` in the cron service at 04:15 UTC daily. The cron service must receive the same database and S3 environment configuration as the backend.
- **Neon PostgreSQL:** Set `DATABASE_URL` to the pooled PostgreSQL URL with TLS (`sslmode=require`). SQLAlchemy and Alembic use psycopg 3 and normalize PostgreSQL URL schemes. Enable pgvector; migrations create the extension if the database role permits. The current embedding column is `vector(1536)`.
- **Supabase Storage S3:** Use the private `markwise-assets` bucket and endpoint/region from Supabase Project Settings → Storage → S3. Configure `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY`. The adapter uses HTTPS, Signature V4, and path-style addressing. Keep the generated server-side keys private; they bypass bucket RLS. Keep `S3_SERVER_SIDE_ENCRYPTION` empty for Supabase because its S3 endpoint rejects the SSE request header.
- **AI analysis:** OpenRouter `nvidia/nemotron-3.5-super-vl:free` is primary for text and vision; Gemini `gemini-3.5-flash` is secondary with `gemini-3.5-flash-lite` alternate for text and vision. OpenRouter uses structured JSON and image input. OpenRouter free processing may log/use inputs to improve provider products. Admins must enable both workspace consent settings before OpenRouter is used. If OpenRouter-specific consent is off but general external-AI consent is on, configured Gemini may serve analysis; embeddings remain unavailable because the embedding provider is OpenRouter-only. Live provider/model availability and quota depend on the configured accounts.
- **Embedding and recovery:** OpenRouter `nvidia/nemotron-3.5-embed-1b:free` is requested at 1536 dimensions and stored in the existing pgvector column. Indexing and query input types are differentiated. Migration 0007 tags embedding spaces so incompatible vectors are not mixed; migration 0008 adds monotonic consent generations. Consent is rechecked before outbound requests. A request already sent to a provider cannot be recalled.
- **Retention and request limits:** Review/upload retention is per workspace; `0` disables automatic expiry. Compose includes a retention loop and Render schedules the daily job. API request limits and the OpenRouter budget are in-process; they are not global across replicas. Configure edge/shared limits before scaling.
- **Frontend branding:** The forest-green M plus verification-check mark is served as `frontend/public/icon.svg` and `favicon.ico` (16/32/48px), with `apple-touch-icon.png` (180×180), `icon-192.png`, `icon-512.png`, and `site.webmanifest` for supported mobile shortcuts. `frontend/app/layout.tsx` declares the title “Markwise – Intelligent Brand Governance Platform” and icon metadata for browser tabs/bookmarks and Apple devices. The frontend Docker build includes these public assets. A Render redeploy is needed before a new local icon change appears in the deployed app; browser icon caches may take time to refresh.

## Required environment configuration

Use `backend/.env.example`, root `.env.example`, and the `envVars` section of `render.yaml` as the source of exact names and defaults. Examples use placeholders only. Enter real values only in ignored local `.env` files or Render's protected environment settings.

Backend configuration includes:

- Runtime/database: `SECRET_KEY`, `DATABASE_URL`, `CORS_ORIGINS`, `ACCESS_TOKEN_MINUTES`.
- Storage: `STORAGE_BACKEND`, `UPLOAD_DIR`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_SERVER_SIDE_ENCRYPTION`, `S3_SSE_KMS_KEY_ID`.
- OpenRouter: `AI_PROVIDER`, `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE_URL`, `OPENROUTER_TEXT_MODEL`, `OPENROUTER_VISION_MODEL`, `OPENROUTER_MAX_REQUESTS_PER_MINUTE`.
- Gemini fallback: optional `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL`, `GEMINI_VISION_MODEL`, `GEMINI_TEXT_MODEL_ALTERNATE`, `GEMINI_VISION_MODEL_ALTERNATE`.
- Embeddings: `EMBEDDING_PROVIDER=openrouter`, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS=1536`.
- Resilience and parsing: `AI_TIMEOUT_SECONDS`, `AI_PROVIDER_MAX_RETRIES`, `AI_RETRY_BASE_SECONDS`, `AI_RETRY_MAX_SECONDS`, `AI_CIRCUIT_BREAKER_FAILURES`, `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, `OCR_LANGUAGE`.
- Retention worker: `RETENTION_INTERVAL_SECONDS` for Compose. Render cron cadence is in `render.yaml`.

Frontend uses `NEXT_PUBLIC_API_URL` or `NEXT_PUBLIC_API_HOST` as defined by the Docker build and Render configuration. These values are public and must not contain secrets. Set backend `CORS_ORIGINS` to the exact HTTPS frontend origin, not a wildcard.

## Admin AI consent instructions

Workspace consent is off by default. In Markwise, an Admin navigates to **Settings** and explicitly enables **Allow external AI analysis**, then **Allow OpenRouter free models and embeddings**. OpenRouter consent cannot be enabled until general external-AI consent is on. Both are required for OpenRouter analysis and embedding requests. Do not enable free-provider processing for confidential or personal data. If general consent is on and OpenRouter consent is off, Gemini may still be used for analysis if configured; OpenRouter embeddings will not run. Turning off a consent setting prevents subsequent requests and invalidates cached/stored OpenRouter embeddings, but cannot cancel a request already transmitted.

## Provision and deploy

1. Verify that the connected GitHub repository and Render account are the intended ones. Review service plans and costs before provisioning. Do not commit local `.env` files or credentials.
2. Connect the repository in Render and create/update the Blueprint from `render.yaml`.
3. Create/select a Neon database, enable pgvector, and add the pooled TLS URL as protected `DATABASE_URL` for the backend and retention worker.
4. Keep the Supabase bucket private. Add its endpoint, bucket, region, access key ID, and secret access key as protected backend settings. Ensure the retention worker receives these same storage settings.
5. Add protected `OPENROUTER_API_KEY`; add `GEMINI_API_KEY` if Gemini fallback is desired. Confirm model values and retry settings in `render.yaml`. Render generates backend `SECRET_KEY`.
6. Confirm `CORS_ORIGINS` matches the actual frontend HTTPS URL. Confirm the frontend build-time API host/URL points to the backend service URL.
7. Before deploying schema changes, make a Neon backup/restore point and check the current Alembic revision without printing `DATABASE_URL`. The current local head is `0008_consent_generation`. Apply migrations only through the configured release process or a carefully verified local Alembic invocation. Do not treat a failed/rolled-back deployment as a migration success.
8. Inspect Render build and pre-deploy logs, `GET /health`, HTTPS frontend response, CORS behavior, and cron execution. Confirm the cron service is attached to the intended database and private bucket.
9. In a dedicated test workspace, verify registration/login, Admin and non-Admin permissions, workspace isolation, private uploads, PDF/DOCX import, confirmation, AI-consent gating, real provider analysis/citations when quota permits, `CANNOT VERIFY`, finding actions/history, dashboard, and retention deletion.
10. Configure monitoring, alerting, database backups and a restore drill, plus shared ingress rate limits before opening the service to users.

Render may not resolve a `sync:false` secret from the web service into the cron service in every setup. If a cron job lacks Neon or S3 configuration, add the same value to that service's protected settings and verify its logs; never print secrets.

## Local Compose verification

From the repository root in PowerShell:

```powershell
Copy-Item .env.example .env
```

Set a unique `SECRET_KEY` (at least 32 characters) and `POSTGRES_PASSWORD` in the ignored root `.env`; leave `DATABASE_URL` blank to use the Compose PostgreSQL/pgvector service. Then run:

```powershell
docker compose config
docker compose up --build
```

Check `http://localhost:8000/health` and `http://localhost:3000`. Compose is a development configuration; successful YAML parsing is not equivalent to successful service startup.

## Local Neon migration procedure

For direct local Alembic use, copy `backend/.env.example` to the ignored `backend/.env` and enter the intended Neon pooled URL with `sslmode=require`. Verify its host/database safely without printing the URL. Back up the database, check current revision, then run from the repository root:

```powershell
python -m alembic -c backend/alembic.ini current
python -m alembic -c backend/alembic.ini upgrade head
python -m alembic -c backend/alembic.ini current
```

The middle command modifies the configured database. Current code expects `0008_consent_generation` as Alembic head, but compare with the live revision and review migration source before applying. Migration 0007 adds OpenRouter consent and embedding-space tracking. Its downgrade drops rebuildable vectors to prevent mixing embedding spaces but preserves knowledge content. Migration 0008 adds consent-generation columns. Never run test commands while the production URL is loaded.

## Current verification and known gaps

- Latest locally recorded focused admin tests: 5 backend tests passed; frontend Node suite: 14 passed; Next.js production build passed. These are local test/build results, not production workflow verification.
- Backend tests use SQLite and mocks unless explicitly configured otherwise; they do not prove Neon-specific behavior. Tests must be directed at a test database.
- A Supabase Storage connectivity probe was previously reported as passing upload, server-side download, anonymous denial, and cleanup. No storage probe was run in this documentation update.
- Real OpenRouter/Gemini generation and embeddings remain quota/account dependent and are not asserted by mocked tests. The embedding attempt at knowledge confirmation does not automatically retry after failure; missing vector values can be resumed through the Admin backfill action or consent-settings backfill.
- Docker Compose runtime, complete production workflow, retention schedule in production, shared limits, monitoring, backup restore, assistive-technology review, and public frontend URL remain unverified here.
