# Deployment guide

## Current deployment state

No public deployment has been created or verified. A GitHub `origin/main` ref is present, but its visibility/ownership and Render authorization are unverified; Docker is unavailable. `render.yaml` prepares a Render Blueprint with a FastAPI Docker web service, a Next.js Docker web service, and a daily retention cron job. It expects a Git repository connected to Render and the external secrets listed below. Never treat the planned service names or hostnames as live URLs.

## Prepared architecture

- Render Singapore region: FastAPI backend (`/health`), Next.js frontend, and daily retention worker. Render terminates HTTPS. The API image runs Alembic in the pre-deploy phase; web startup runs Uvicorn only. The cron invokes `python -m app.retention` at 04:15 UTC daily.
- Neon PostgreSQL with `vector` extension: configure the Neon pooled connection string as `DATABASE_URL`. Runtime and Alembic convert Neon’s standard `postgresql://`/`postgres://` URL to `postgresql+psycopg://`. Use SSL (`sslmode=require`) and enable pgvector; migrations also create the extension if permitted. Do not expose a database port publicly.
- Supabase Storage S3: keep bucket `markwise-assets` private. Configure the project endpoint and region copied from Supabase Project Settings → Storage → S3, plus the generated server-side access key pair. The app uses HTTPS, Signature V4, path-style requests, and omits unsupported SSE headers. Supabase-generated S3 keys are server-only and bypass bucket RLS; never expose them to the frontend.
- AI providers: Gemini (`gemini-3.8-flash` → `gemini-3.7-flash`) via the Interactions API is primary for text and image analysis; requests set `store=false`. OpenAI (`gpt-6-luna` → `gpt-5.6-luna`) is secondary for both modalities. Both pairs support image input and structured output. Set `GEMINI_API_KEY` and `AI_FALLBACK_API_KEY` only in protected Render/backend environment settings. Bounded retries and Retry-After apply to temporary model errors; HTTP 429/quota skips that provider's alternate and switches to the other provider. Provider circuit cooldown and automatic recovery remain enabled. Workspace data is sent only when an admin opts in.
- Embeddings remain on OpenAI `text-embedding-3-small` at 1536 dimensions through `AI_FALLBACK_API_KEY`, preserving the existing `vector(1536)` index and embedding space. Do not switch embedding provider/model without re-embedding every indexed source in one consistent space.

## Deploy steps requiring account access

1. Confirm the existing `origin` is the intended private repository, verify its visibility/ownership, and authorize Render to access it. One commit is present on `origin/main`. This task does not publish any code. Do not commit `.env` or secrets.
2. In Render, create a Blueprint from the repository’s `render.yaml`. Review the service plans and estimated cost before accepting; the API plan is configured as `1c-2g`, and the frontend/cron use `0.5c-512mb`.
3. Create or select a Neon project, enable pgvector, and provide its pooled connection string as the Blueprint’s `DATABASE_URL` secret. Keep `sslmode=require` in the URL.
4. Use the private Supabase `markwise-assets` bucket and generated `markwise-backend` S3 key pair. Set `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` in Render secret settings.
5. Set protected AI environment values: `AI_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_TEXT_MODEL=gemini-3.8-flash`, `GEMINI_VISION_MODEL=gemini-3.8-flash`, `GEMINI_TEXT_MODEL_ALTERNATE=gemini-3.7-flash`, `GEMINI_VISION_MODEL_ALTERNATE=gemini-3.7-flash`, `AI_FALLBACK_PROVIDER=openai_compatible`, `AI_FALLBACK_API_BASE_URL=https://api.openai.com/v1`, `AI_FALLBACK_API_KEY`, `AI_FALLBACK_TEXT_MODEL=gpt-6-luna`, `AI_FALLBACK_VISION_MODEL=gpt-6-luna`, `AI_FALLBACK_TEXT_MODEL_ALTERNATE=gpt-5.6-luna`, `AI_FALLBACK_VISION_MODEL_ALTERNATE=gpt-5.6-luna`, `EMBEDDING_PROVIDER=openai_compatible`, and `EMBEDDING_MODEL=text-embedding-3-small`. Retry/circuit defaults are documented in `backend/.env.example`. Do not claim live failover is verified until provider probes complete successfully.
6. Deploy and inspect the Render build, pre-deploy migration, web health, and cron logs. Render generates the API secret. The frontend reads the API host from the API web service reference and builds its HTTPS base URL.
7. Confirm the deployed frontend origin equals the backend’s `CORS_ORIGINS`. If Render assigns a different hostname, update the API allowlist and redeploy. Confirm TLS redirects and configure shared edge rate limiting/monitoring at the public edge before inviting users.

Render Blueprint provisioning may not be able to resolve a `sync:false` secret from an upstream service into the cron service in every account/setup. If the retention worker fails to receive Neon/Supabase Storage secrets, enter the same values in that service’s protected environment settings and verify one successful run in cron logs. Do not paste secrets into chat or source files.

## Environment values

The Render Blueprint defines public and non-secret settings. Required secret inputs are `DATABASE_URL`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `GEMINI_API_KEY`, and `AI_FALLBACK_API_KEY`. `SECRET_KEY` is generated by Render. The backend also supports `CORS_ORIGINS`, `ACCESS_TOKEN_MINUTES`, `AI_FALLBACK_API_BASE_URL`, `EMBEDDING_MODEL`, `AI_TIMEOUT_SECONDS`, `AI_PROVIDER_MAX_RETRIES`, `AI_RETRY_BASE_SECONDS`, `AI_RETRY_MAX_SECONDS`, `AI_CIRCUIT_BREAKER_FAILURES`, `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, and `OCR_LANGUAGE`. Set the CORS origin to the exact frontend HTTPS origin. Set secrets only in service secret settings.

## Local Compose checks

1. Copy `.env.example` to `.env`, set a unique random `SECRET_KEY` and `POSTGRES_PASSWORD`, and leave `DATABASE_URL` blank for Compose to construct a local URL.
2. Run `docker compose config`, then `docker compose up --build`.
3. Check `http://localhost:8000/health` and `http://localhost:3000`; exercise a workspace workflow. Compose uses PostgreSQL/pgvector, API migration startup, frontend health dependency, and a separate periodic retention worker.

Compose is a development stack. It does not replace production secret management, backups, shared rate limiting, external monitoring, or TLS configuration.

## Production release verification

Only mark deployment complete after actually running each check with production services:

- Confirm Render deployment succeeds, Alembic reaches head, `/health` reports database connected, frontend responds over HTTPS, and CORS accepts only the expected origin.
- Register a dedicated test workspace, log in, verify role restrictions and cross-workspace isolation, upload a document to the private Supabase bucket, import and admin-confirm a guideline, and verify retention deletion removes both object and metadata.
- With external AI explicitly enabled in that test workspace, verify review analysis uses real model responses and real pgvector retrieval/citations. Exercise unsupported claim `CANNOT VERIFY`; inspect source citation excerpts and verify the vision workflow with a real image/reference logo. Never substitute fabricated citations or canned results.
- Verify dashboard values against the test workspace’s records; check HTTPS, headers, edge rate limits, logs, database backups, Supabase bucket privacy and purge outcomes. Run the frontend with keyboard and screen-reader checks.

The current local backend suite uses SQLite and mocked AI/S3 adapters. The frontend build and Node smoke/accessibility checks are not browser end-to-end tests. The local SQLite migration check does not prove Neon/pgvector compatibility. The live Supabase Storage probe passed upload, authenticated download, anonymous denial, and post-delete absence. Docker, live AI providers, purge scheduling, production workflows and public URL checks remain unverified.
## Connect a local backend to Neon

For a local backend or Alembic run, put the Neon connection string in `backend/.env` at the repository path `C:\Users\niket\OneDrive\Desktop\markwise_app\backend\.env`. Copy `backend/.env.example` to that filename and replace only the `DATABASE_URL` value with the pooled connection string from Neon. Do not paste credentials into chat or commit the `.env` file. Keep `sslmode=require` (or the stronger `verify-full` if your Neon connection settings provide the required certificate verification configuration). The backend automatically uses psycopg 3 via SQLAlchemy and enforces TLS for remote PostgreSQL hosts; local Compose database URLs are unchanged. Alembic reads the same settings and uses psycopg's PgBouncer-compatible non-prepared statement mode.

Neon connectivity has been verified from `backend/.env`. Existing migrations through `0005_finding_decisions` have been applied. Verified all 12 application tables, `vector` extension version 0.8.6, the `vector(1536)` embedding column, cosine HNSW index, and zero Alembic drift. Backend integration tests were explicitly run against in-memory SQLite and did not touch Neon.


## GitHub source safety status

Git metadata currently contains `origin/main` with one commit and a 41-file tree. A high-confidence scan found no credential patterns; database URL patterns in test fixtures were placeholders. `.gitignore` blocks `backend/.env`, other environment files, uploads, local databases/exports, key material, credentials directories, dependency folders, and generated artifacts, while retaining safe `.env.example` templates. This task did not commit or push. The remote’s visibility/ownership is unverified; review it before any further publish action.


## Supabase Storage S3 configuration

Put the actual storage settings only in the ignored `backend/.env` file. Required settings are `STORAGE_BACKEND=s3`, `S3_ENDPOINT_URL=https://<project-ref>.storage.supabase.co/storage/v1/s3`, `S3_BUCKET=markwise-assets`, `S3_REGION=<region copied from Supabase S3 settings>`, `S3_ACCESS_KEY_ID=<markwise-backend access key ID>`, `S3_SECRET_ACCESS_KEY=<paired secret access key>`, and `S3_SERVER_SIDE_ENCRYPTION=` (empty). Copy the endpoint and region from Supabase Project Settings → Storage → S3; don’t guess them. The S3 secret is never placed in examples or source files.

The application’s S3 adapter sends Signature V4 requests with path-style addressing and HTTPS for Supabase. Supabase’s S3 API rejects server-side encryption request headers, so the setting must be empty and the adapter omits SSE headers for Supabase. Keep the bucket private; Supabase’s generated S3 credentials are server-only and bypass bucket RLS. There is no existing application presigned-URL generator or download route. The backend storage check uses authenticated server-side `GetObject`, does not create signed URLs, deletes its unique probe object, and verifies absence with `HeadObject`: from the repository root run `python -m backend.app.storage_check` after setting the variables.
