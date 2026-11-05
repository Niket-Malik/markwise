# Markwise project status

## Scope and current state

Work continues in the existing FastAPI, SQLAlchemy/Alembic, PostgreSQL/pgvector, and Next.js application. The original Round 1 proposal is the product acceptance reference; the pasted master prompt supplies the user's implementation instructions. This repository is an expanded MVP implementation, but it is not yet production deployed and several service-dependent requirements remain unverified.

## Implemented

- Workspace-scoped accounts, roles, review assignments, decisions, comments, content versions, audit events, and admin-confirmed knowledge.
- OpenAI-compatible chat, vision, and embedding adapters; external AI is workspace opt-in. Approved-source chunks can be semantically retrieved through pgvector on PostgreSQL (lexical fallback otherwise). Provider findings must cite retrieved workspace source IDs and exact excerpts or in-bounds image regions; unsupported claims can abstain as `CANNOT VERIFY`. Suggestions are guarded to preserve intent and supported numeric claims.
- When an opted-in text review has no matching source, AI can still inspect for factual claims; validated unsupported claims are explicitly marked `CANNOT VERIFY`. The review disclosure distinguishes this from a grounded source match.
- PDF and DOCX parsing, scanned PDF and image OCR using Tesseract, pending imported guideline passages, and source document storage.
- Image review with optional vision provider, optional user-supplied logo reference, palette sampling, OCR-derived contrast estimates, and alt-text checks. Heuristic measurements are disclosed as estimates, not definitive accessibility certification.
- Local or S3-compatible storage adapter; upload and review retention settings; Compose retention worker; review deletion and reasoned finding decisions. AWS S3 writes default to AES256 server-side encryption and support KMS; Cloudflare R2 uses managed encryption and receives no unsupported SSE headers. Audit history is preserved through content purge.
- File type signatures are checked before storage/OCR; DOCX decompression, upload size, pixel count, and visual-reference limits are bounded.
- PDF/DOCX extracted text now populates the draft field so users can continue through the review workflow.
- Applying a source-validated text suggestion replaces a unique flagged span only, saves a review version, preserves prior finding reasons, and re-runs analysis. Ambiguous repeated spans and non-rewrite/CANNOT VERIFY findings cannot be applied as edits.
- Workspace admins can list and delete their tracked uploads from Settings; non-admins receive no upload inventory.
- Review-submission and finding-decision authorization now enforce ownership/management or assigned-reviewer access. Stale findings are hidden from current results while their reason history remains available.
- Dashboard data for repeated unsupported claims, finding decisions, dismissed findings, and contested rules.
- Rate limits and basic response security headers. Docker Compose configuration includes PostgreSQL/pgvector and health dependencies.

## Verification completed in this workspace

- Backend integration suite: **17 passed** using in-memory SQLite and mocked AI/storage; the run explicitly set `DATABASE_URL=sqlite://`, so tests did not connect to or modify Neon.
- Frontend production build and four Node-based production artifact smoke/accessibility checks passed. These are not interactive browser end-to-end tests.
- Python compile passed. SQLite Alembic upgrade and drift checks passed on an isolated temporary database.
- Neon connection was verified from `backend/.env` using SQLAlchemy 2.0.40, psycopg 3.2.6, and TLS (`sslmode=require`); psycopg/libpq confirmed the active client socket is encrypted. A read-only `SELECT 1` succeeded.
- All five checked-in Alembic migrations (`0001_initial` through `0005_finding_decisions`) were applied to the initially empty Neon public schema. Verified all 12 expected application tables, Alembic revision `0005_finding_decisions`, pgvector extension 0.8.6, `knowledge_chunks.embedding` type `vector(1536)`, and the cosine HNSW index. Alembic `check` reports no new upgrade operations after aligning SQLAlchemy metadata with the existing HNSW index.
- Render and Compose YAML parsed. Docker/Compose is unavailable, so containers were not started.
- Browser keyboard inspection is not a full cross-browser or WCAG audit.

## Outstanding mandatory work / external blockers

- Live provider-backed AI/vision/embedding and object storage workflows remain unverified in this round. Keep them marked open until exercised using real configured services.
- OCR depends on Tesseract; actual scanned document OCR needs verification in the deployment image or configured host.
- Automated frontend checks are build-artifact checks, not interactive browser workflows or full assistive-technology review.
- Security review remains incomplete: process-local rate limiting needs a shared edge limit in production; verify session lifecycle, proxy trust, storage policy, backups, logging, and retention operations.
- Docker Compose startup cannot be verified because Docker/Compose is unavailable in the environment.
- No public host, storage bucket, deployment authorization, HTTPS monitoring, or verified live URL is available.

See [TODO.md](TODO.md), [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md), and [DEPLOYMENT.md](DEPLOYMENT.md). Do not treat mocked or SQLite-only tests as live-service verification.


## Deployment preparation (not a live deployment)

- Added `render.yaml` for Render API/frontend Docker services, pre-deploy Alembic migration, HTTPS health endpoint, and daily retention cron; frontend resolves its API host from Render service metadata.
- Neon URL normalization, TLS enforcement for remote PostgreSQL, pooler-safe psycopg settings, and Cloudflare R2 encryption compatibility are implemented.
- No public service was deployed. This workspace has no Git remote, Render authorization, R2 credentials, or Docker CLI. No live URLs or managed-service checks are claimed.

## Neon integration (verified)

- Local backend settings and Alembic read `backend/.env`; root `.env` and Compose configuration remain unchanged. The user-provided database URL was not printed or copied into documentation.
- Read-only Neon connectivity and schema checks succeeded. Existing Alembic revisions through `0005_finding_decisions` were applied; all required tables and pgvector schema/index were verified. `alembic check` reports no drift.
- Backend tests were forced to in-memory SQLite and do not touch Neon data.


## GitHub push preparation

- Git was initialized locally on branch `main`; there are no commits or remotes.
- The reviewed deployment/source tree is staged locally as 41 files. A high-confidence credential-pattern scan of all staged files found no credentials; no Git history exists to audit. The scan suppressed all matched values.
- `.gitignore` excludes environment files (while allowing `.env.example` templates), uploads, local databases/backups, credentials/key material, developer environments, and generated build/test output.
- Nothing has been committed, pushed, or published. A remote and explicit user approval are required before publishing.
