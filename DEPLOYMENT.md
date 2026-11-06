# Deployment guide

## Current deployment state

No public deployment has been created or verified. A GitHub `origin/main` ref is present, but its visibility/ownership and Render authorization are unverified; Docker is unavailable. `render.yaml` prepares a Render Blueprint with a FastAPI Docker web service, a Next.js Docker web service, and a daily retention cron job. It expects a Git repository connected to Render and the external secrets listed below. Never treat the planned service names or hostnames as live URLs.

## Prepared architecture

- Render Singapore region: FastAPI backend (`/health`), Next.js frontend, and daily retention worker. Render terminates HTTPS. The API image runs Alembic in the pre-deploy phase; web startup runs Uvicorn only. The cron invokes `python -m app.retention` at 04:15 UTC daily.
- Neon PostgreSQL with `vector` extension: configure the Neon pooled connection string as `DATABASE_URL`. Runtime and Alembic convert Neon’s standard `postgresql://`/`postgres://` URL to `postgresql+psycopg://`. Use SSL (`sslmode=require`) and enable pgvector; migrations also create the extension if permitted. Do not expose a database port publicly.
- Supabase Storage S3: keep bucket `markwise-assets` private. Configure the project endpoint and region copied from Supabase Project Settings → Storage → S3, plus the generated server-side access key pair. The app uses HTTPS, Signature V4, path-style requests, and omits unsupported SSE headers. Supabase-generated S3 keys are server-only and bypass bucket RLS; never expose them to the frontend.
- AI providers: OpenRouter `nvidia/nemotron-3.5-super-vl:free` is primary for text and vision through `/api/v1/chat/completions`; Gemini `gemini-3.8-flash` → `gemini-3.7-flash` is the configured secondary. Nemotron Super VL accepts text/images and OpenRouter JSON-schema structured output. OpenRouter free services may log prompts and use data to improve services, so a workspace admin must explicitly enable both external AI and the separate OpenRouter free-provider consent before its data is sent there. Without OpenRouter consent, the app may use configured Gemini only.
- Embeddings use OpenRouter `nvidia/nemotron-3.5-embed-1b:free` at exactly 1536 dimensions through `/api/v1/embeddings`, with `input_type=query` for searches and `passage` for indexed chunks. The provider documents a 2048-D model with truncation. Markwise requests 1536 and re-normalizes the returned vector; the PostgreSQL `vector(1536)` column remains unchanged. Migration 0007 tags embedding spaces; old OpenAI vectors are ignored and require opted-in re-embedding before semantic retrieval uses them. A failed bounded backfill leaves chunks lexical-only and can be resumed by a workspace admin through `POST /api/settings/embeddings/backfill`; there are no automatic periodic retries. Downgrading below 0007 clears rebuildable `knowledge_chunks.embedding` vectors before dropping the space tag, while preserving source text and knowledge records.
- Migration 0008 adds separate monotonic workspace consent generations for general external AI and OpenRouter. Both are re-checked at the outbound provider boundary. Revoking OpenRouter-specific consent prevents subsequent OpenRouter requests while allowing Gemini only if general external-AI consent remains unchanged; withdrawing general consent blocks both providers. Revocation invalidates stale in-flight results before they are used/persisted; an already transmitted request cannot be cancelled. OpenRouter's request budget and the API HTTP rate limiter are process-local, so they are not global across workers/instances; configure edge-level protection when scaling, without assuming the local budget is shared.
- An explicitly configured legacy `AI_PROVIDER=openai` or `openai_compatible` remains supported only when OpenRouter and Gemini are not configured, and it still requires the workspace's general external-AI consent at each outbound call. When OpenRouter and/or Gemini are configured, the production chain is OpenRouter first and Gemini second; legacy OpenAI settings cannot bypass that order.

## Deploy steps requiring account access

1. Confirm the existing `origin` is the intended private repository, verify its visibility/ownership, and authorize Render to access it. One commit is present on `origin/main`. This task does not publish any code. Do not commit `.env` or secrets.
2. In Render, create a Blueprint from the repository’s `render.yaml`. Review the service plans and estimated cost before accepting; the API plan is configured as `1c-2g`, and the frontend/cron use `0.5c-512mb`.
3. Create or select a Neon project, enable pgvector, and provide its pooled connection string as the Blueprint’s `DATABASE_URL` secret. Keep `sslmode=require` in the URL.
4. Use the private Supabase `markwise-assets` bucket and generated `markwise-backend` S3 key pair. Set `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, and `S3_SECRET_ACCESS_KEY` in Render secret settings.
5. Set protected AI values: `AI_PROVIDER=openrouter`, `OPENROUTER_API_KEY`, `OPENROUTER_API_BASE_URL=https://openrouter.ai/api/v1`, `OPENROUTER_TEXT_MODEL=nvidia/nemotron-3.5-super-vl:free`, `OPENROUTER_VISION_MODEL=nvidia/nemotron-3.5-super-vl:free`, optional Gemini `GEMINI_API_KEY`, `EMBEDDING_PROVIDER=openrouter`, `EMBEDDING_MODEL=nvidia/nemotron-3.5-embed-1b:free`, and `EMBEDDING_DIMENSIONS=1536`. Retry/circuit and `OPENROUTER_MAX_REQUESTS_PER_MINUTE` settings are in `backend/.env.example`. The app defaults to 15 OpenRouter calls/minute per process; configure shared limits if scaling replicas. No OpenAI key is needed for analysis or embeddings in this architecture. After deployment, a workspace admin must separately opt in to external AI and OpenRouter free-provider use. Do not claim live provider availability until tested with the configured accounts.
6. Deploy and inspect the Render build, pre-deploy migration, web health, and cron logs. Render generates the API secret. The frontend reads the API host from the API web service reference and builds its HTTPS base URL.
7. Confirm the deployed frontend origin equals the backend’s `CORS_ORIGINS`. If Render assigns a different hostname, update the API allowlist and redeploy. Confirm TLS redirects and configure shared edge rate limiting/monitoring at the public edge before inviting users.

Render Blueprint provisioning may not be able to resolve a `sync:false` secret from an upstream service into the cron service in every account/setup. If the retention worker fails to receive Neon/Supabase Storage secrets, enter the same values in that service’s protected environment settings and verify one successful run in cron logs. Do not paste secrets into chat or source files.

## Environment values

The Render Blueprint defines public and non-secret settings. Required secret inputs are `DATABASE_URL`, `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, and `OPENROUTER_API_KEY`; `GEMINI_API_KEY` is optional for the secondary provider. `SECRET_KEY` is generated by Render. The backend also supports `CORS_ORIGINS`, `ACCESS_TOKEN_MINUTES`, `OPENROUTER_API_BASE_URL`, `OPENROUTER_MAX_REQUESTS_PER_MINUTE`, the OpenRouter model settings, `EMBEDDING_MODEL`, `EMBEDDING_DIMENSIONS`, `AI_TIMEOUT_SECONDS`, `AI_PROVIDER_MAX_RETRIES`, `AI_RETRY_BASE_SECONDS`, `AI_RETRY_MAX_SECONDS`, `AI_CIRCUIT_BREAKER_FAILURES`, `AI_CIRCUIT_BREAKER_COOLDOWN_SECONDS`, and `OCR_LANGUAGE`. Set the CORS origin to the exact frontend HTTPS origin. Set secrets only in service secret settings.

## Local Compose checks

1. Copy `.env.example` to `.env`, set a unique random `SECRET_KEY` and `POSTGRES_PASSWORD`, and leave `DATABASE_URL` blank for Compose to construct a local URL.
2. Run `docker compose config`, then `docker compose up --build`.
3. Check `http://localhost:8000/health` and `http://localhost:3000`; exercise a workspace workflow. Compose uses PostgreSQL/pgvector, API migration startup, frontend health dependency, and a separate periodic retention worker.

Compose is a development stack. The root `.env.example` now matches its OpenRouter/Nemotron defaults. It does not replace production secret management, backups, shared rate limiting, external monitoring, or TLS configuration. Both the OpenRouter request budget and API rate limiter are in-process only and do not coordinate across multiple workers or instances; add edge-level limits before scaling.

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
