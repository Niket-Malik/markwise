# Markwise project status

## Scope and current state

Work continues in the existing FastAPI, SQLAlchemy/Alembic, PostgreSQL/pgvector, and Next.js application. The original Round 1 proposal is the product acceptance reference; the pasted master prompt supplies the user's implementation instructions. This repository is an expanded MVP implementation, but it is not yet production deployed and several service-dependent requirements remain unverified.

## Implemented

- Workspace-scoped accounts, roles, review assignments, decisions, comments, content versions, audit events, and admin-confirmed knowledge.
- Consent-gated AI chain: OpenRouter `nvidia/nemotron-3.5-super-vl:free` (text, vision and JSON schema) → Gemini `gemini-3.5-flash` / `gemini-3.5-flash-lite` → existing Knowledge Base fallback. HTTP 429/quota skips same-provider alternates; temporary Gemini failures can advance to its alternate. OpenAI analysis and embeddings are no longer called by the configured free stack. Embeddings use `nvidia/nemotron-3.5-embed-1b:free`, requested as 1536 dimensions to preserve the existing pgvector schema; every chunk records an embedding-space fingerprint. External AI stays workspace opt-in. Approved-source chunks can be retrieved through pgvector on PostgreSQL (lexical fallback otherwise). Provider findings must cite retrieved source IDs and exact excerpts or in-bounds image regions; unsupported claims can abstain as `CANNOT VERIFY`; suggestions retain citation and intent validation.
- AI provider calls use bounded retries, exponential backoff with jitter and Retry-After, 429/quota/5xx/timeout/connection classification, per-process circuit cooldown and half-open recovery, capability checks for image failover, credential-free structured logs, and retryable user-facing failure status. Workspace admins must separately consent to OpenRouter free-provider data processing; disabling consent clears vectors and workspace query-cache entries. AI generation/retry is limited to 10 requests per minute per client IP, and a separate process-wide OpenRouter budget limits all upstream analysis/retries/embeddings to 15 calls per minute by default; production still needs shared limits across replicas. Provider health is exposed to authenticated workspace settings and an admin-only status route.
- When an opted-in text review has no matching source, AI can still inspect for factual claims; validated unsupported claims are explicitly marked `CANNOT VERIFY`. The review disclosure distinguishes this from a grounded source match.
- PDF and DOCX parsing, scanned PDF and image OCR using Tesseract, pending imported guideline passages, and source document storage.
- Image review with optional vision provider, optional user-supplied logo reference, palette sampling, OCR-derived contrast estimates, and alt-text checks. Heuristic measurements are disclosed as estimates, not definitive accessibility certification.
- Local or S3-compatible storage adapter; upload and review retention settings; Compose retention worker; review deletion and reasoned finding decisions. AWS S3 writes default to AES256 server-side encryption and support KMS; Cloudflare R2 and Supabase Storage omit unsupported SSE headers. Audit history is preserved through content purge.
- File type signatures are checked before storage/OCR; DOCX decompression, upload size, pixel count, and visual-reference limits are bounded.
- PDF/DOCX extracted text now populates the draft field so users can continue through the review workflow.
- Applying a source-validated text suggestion replaces a unique flagged span only, saves a review version, preserves prior finding reasons, and re-runs analysis. Ambiguous repeated spans and non-rewrite/CANNOT VERIFY findings cannot be applied as edits.
- Workspace admins can list and delete their tracked uploads from Settings; non-admins receive no upload inventory.
- Review-submission and finding-decision authorization now enforce ownership/management or assigned-reviewer access. Stale findings are hidden from current results while their reason history remains available.
- Dashboard data for repeated unsupported claims, finding decisions, dismissed findings, and contested rules.
- Rate limits and basic response security headers. Docker Compose configuration includes PostgreSQL/pgvector and health dependencies.

## Verification completed in this workspace

- AI model/provider failover tests: prior Gemini/OpenAI configuration had 20 mocked tests; the free-stack update adds mocked OpenRouter request, vision, embedding, vector-space, quota failover, consent and cache tests. Live generation/embedding attempts and their transient service/quota failures are recorded below. No environment file values are included in this status.
- Free-stack verification (2026-11-06): full backend suite **68 passed** using SQLite and mocked providers/storage, frontend tests **6 passed**, and Next.js production build passed. No live AI or embedding calls were made. The test runner uses local loopback access for Starlette TestClient and explicit test-only database/secret values.
- Live provider probe (2026-11-06): Gemini model metadata access returned HTTP 200, but both text and image generation returned HTTP 503. OpenAI text, image, and embedding requests returned HTTP 429 classified from whitelisted error fields as quota exhaustion. Simulated Gemini transient failure correctly routed a live request to OpenAI, which was also blocked by quota. No live analysis or embedding vector was produced; no credentials or response bodies were printed. Both keys are configured in backend/.env (presence only checked).
- Quota-safe verification (2026-11-06): resolved local model settings match both requested primary/alternate orders; all four IDs pass the application's model/capability allowlists. Gemini/OpenAI keys are present (presence only); OpenAI embeddings remain `text-embedding-3-small` and the metadata column remains `vector(1536)`. No live requests were made in this verification because the prior probe found Gemini generation 503 and OpenAI quota exhaustion 429.
- Quota-safe final verification (2026-11-06): **20 model/failover tests passed** and the complete backend suite passed **39 tests** with SQLite, provider disabled in the test process, and mocked provider/storage calls. Zero live API requests were made. Model capability allowlists passed for all four models, and `vector(1536)` metadata plus OpenAI `text-embedding-3-small` were confirmed without a database connection.

- Earlier backend integration baseline: **18 passed** using in-memory SQLite and mocked AI/storage; the current full suite has since passed 35 tests and did not connect to or modify Neon.
- Frontend production build and four Node-based production artifact smoke/accessibility checks passed. These are not interactive browser end-to-end tests.
- Python compile passed. SQLite Alembic upgrade and drift checks passed on an isolated temporary database.
- Neon connection was verified from `backend/.env` using SQLAlchemy 2.0.40, psycopg 3.2.6, and TLS (`sslmode=require`); psycopg/libpq confirmed the active client socket is encrypted. A read-only `SELECT 1` succeeded.
- All five checked-in Alembic migrations (`0001_initial` through `0005_finding_decisions`) were applied to the initially empty Neon public schema. Verified all 12 expected application tables, Alembic revision `0005_finding_decisions`, pgvector extension 0.8.6, `knowledge_chunks.embedding` type `vector(1536)`, and the cosine HNSW index. Alembic `check` reports no new upgrade operations after aligning SQLAlchemy metadata with the existing HNSW index.
- Render and Compose YAML parsed. Docker/Compose is unavailable, so containers were not started.
- Browser keyboard inspection is not a full cross-browser or WCAG audit.

## Current migration note

- Added forward Alembic migration `0007_openrouter_embedding_space` after production-known revision 0006. It adds an admin opt-in boolean (default false) and nullable embedding-space tag. Existing `vector(1536)` values are retained but not used until re-embedded under the new fingerprint. Migration SQL generation is verified locally only; do not infer that production Neon has applied 0007.

## Outstanding mandatory work / external blockers

- OpenRouter/Gemini model calls and the free embedding endpoint remain unverified live; all current provider checks are mocked. OpenRouter free endpoint uptime/quotas may fluctuate. Supabase Storage probe passed previously. The Supabase Storage probe has been run against the configured private bucket and verified upload, download, anonymous denial, and post-delete absence.
- OCR depends on Tesseract; actual scanned document OCR needs verification in the deployment image or configured host.
- Automated frontend checks are build-artifact checks, not interactive browser workflows or full assistive-technology review.
- Security review remains incomplete: process-local rate limiting needs a shared edge limit in production; verify session lifecycle, proxy trust, storage retention, backups, logging, and operational monitoring.
- Docker Compose startup cannot be verified because Docker/Compose is unavailable in the environment.
- No public host, deployment authorization, HTTPS monitoring, or verified live URL is available; Supabase Storage is configured and its probe passed.

See [TODO.md](TODO.md), [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md), and [DEPLOYMENT.md](DEPLOYMENT.md). Do not treat mocked or SQLite-only tests as live-service verification.


## Deployment preparation (not a live deployment)

- Added `render.yaml` for Render API/frontend Docker services, pre-deploy Alembic migration, HTTPS health endpoint, and daily retention cron; frontend resolves its API host from Render service metadata.
- Neon URL normalization, TLS enforcement for remote PostgreSQL, pooler-safe psycopg settings, and Supabase Storage S3 configuration are implemented; live Supabase connectivity and private-bucket lifecycle probe passed.
- No public service was deployed. Render authorization and Docker CLI are unavailable. The Git remote exists, but its visibility is unknown. No live URLs or managed-service checks are claimed.

## Neon integration (verified)

- Local backend settings and Alembic read `backend/.env`; root `.env` and Compose configuration remain unchanged. The user-provided database URL was not printed or copied into documentation.
- Read-only Neon connectivity and schema checks succeeded. Existing Alembic revisions through `0005_finding_decisions` were applied; all required tables and pgvector schema/index were verified. `alembic check` reports no drift.
- Backend tests were forced to in-memory SQLite and do not touch Neon data.


## GitHub push preparation

- Git branch `main` has an `origin/main` upstream and one reachable commit; this task did not create a commit or push. The remote visibility/ownership remains unverified.
- The current `origin/main` tree has 41 files. A secret-pattern scan of that history found no high-confidence credential patterns; five database-URL matches were test placeholders only. Current uncommitted Supabase storage changes remain in the working tree. All matched values were suppressed.
- `.gitignore` excludes environment files (while allowing `.env.example` templates), uploads, local databases/backups, credentials/key material, developer environments, and generated build/test output.
- No commit or push was made during this storage task. The existing remote’s visibility/ownership is not verified; do not publish more changes without explicit approval.


## Supabase Storage integration (live probe verified)

- Supabase Storage S3 is configured in code with Signature V4, path-style requests, HTTPS-only endpoint validation, and no SSE headers. Added server-side `get_object` and a temporary connectivity probe covering upload, download, anonymous public-access denial, and cleanup.
- `backend/.env` remains private and was not modified by this task. Configuration presence was checked without printing values.
- Backend tests: **18 passed** using in-memory SQLite and mocked S3; compileall and Render/Compose YAML parsing passed. The live Supabase probe passed upload, authenticated download, anonymous access denial, and HeadObject confirmation that the temporary key was absent after deletion.
- The existing app had no presigned URL generator or object download route. The new probe uses private server-side S3 GetObject and creates no signed/public URL.
