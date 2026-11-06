# Implementation checklist

## Proposal MVP acceptance

- [x] Text and image review, with findings persisted to workspace-scoped reviews.
- [x] Admin-confirmed and versioned knowledge entries; PDF/DOCX import creates pending passages.
- [x] AI-assisted text/vision analysis is opt-in; evidence is drawn from approved source retrieval and citation checked. `CANNOT VERIFY` is used where supported facts are absent.
- [x] OpenRouter `nvidia/nemotron-3.5-super-vl:free` handles consented text, vision and JSON-schema analysis; Gemini `gemini-3.8-flash` → `gemini-3.7-flash` is the secondary. Bounded retries, quota-aware failover, circuit recovery and capability checks are retained.
- [x] OpenRouter `nvidia/nemotron-3.5-embed-1b:free` adapter requests and validates 1536-dimensional vectors (mock-verified, not live-verified). Migration 0007 tags embedding space; existing pgvector dimension is unchanged and prior embeddings are excluded until re-indexed.
- [x] AI failover unit tests cover provider error classification, retry bounds, fallback, permanent errors, recovery, visual capability, and total outage.
- [x] Human decisions, override reasons, comments, assignments, content versions, and audit history are persisted.
- [x] Dashboard reads real workspace review/finding/decision data and surfaces repeated unsupported claims and contested rules.
- [x] Configurable review/upload retention and deletion paths exist; local/S3-compatible storage adapter implemented.
- [x] File signatures and parser size limits are checked; extracted document text can be reviewed in the draft editor.
- [x] Review decision permissions enforce review ownership/management or assignment; S3 encryption is configurable and defaults to AES256.
- [x] Applying eligible suggestions updates a unique flagged draft span, saves a version, retains prior decision reasons, and reruns analysis.
- [x] Workspace administrators can inventory and delete tracked uploads.
- [x] A Compose retention worker is configured; S3 and scheduled purge have not been live-tested.
- [x] Render deployment Blueprint prepared; Neon URL normalization and Supabase Storage S3 compatibility implemented. Live Supabase upload/download/privacy/cleanup probe passed.
- [x] Local backend/Alembic configuration reads `backend/.env`; remote PostgreSQL requires TLS and uses psycopg 3.
- [x] Live Neon PostgreSQL/pgvector connection, TLS, migrations, all 12 tables, vector schema/index, and Alembic drift check verified.
- [ ] Successful live OpenRouter/Gemini generation and OpenRouter embeddings remain unverified; no live calls were made for this change. Free endpoint availability/quota can fluctuate. Supabase Storage probe previously passed its temporary upload/download/privacy/cleanup workflow.
- [x] Free-stack full backend suite: 68 passed on SQLite with mocked external calls; frontend tests 6 passed; Next.js production build passed.
- [ ] Interactive frontend workflows, full assistive-technology review, shared rate limiting across replicas and full security review remain open.
- [ ] Docker Compose runtime and public deployment remain unverified; hosting authorization, Supabase storage secrets or a verified public URL are unavailable; `origin/main` exists but visibility is unknown.

## Verification record

- Backend tests: 18 passed using SQLite and mocked external adapters.
- AI model/provider failover tests: 16 passed with mocked providers; no live model responses were used in these tests.
- Historical baseline before OpenRouter free-stack changes: 35 backend tests and 4 frontend checks passed. Current verification is recorded above: 67 backend tests and 6 frontend tests passed; production build passed.
- Frontend production build: passed.
- Historical frontend artifact smoke/accessibility baseline: 4 checks passed; current suite has 6 checks.
- Python compilation: passed.
- Alembic offline PostgreSQL SQL generation for migration 0007 upgrade and downgrade: passed locally; no production database was contacted and no migration was applied.
- Compose and Render Blueprint YAML: parsed; Docker/Compose is unavailable to launch the stack.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [DEPLOYMENT.md](DEPLOYMENT.md) for outstanding external dependencies. Mocked or SQLite-only checks do not constitute live service verification.

- Latest backend suite: 18 passed with `DATABASE_URL=sqlite://`; tests did not connect to Neon.
- Neon: active psycopg TLS verified; revision `0005_finding_decisions`; pgvector 0.8.6; `vector(1536)` embedding column; cosine HNSW index; Alembic drift check clean.
