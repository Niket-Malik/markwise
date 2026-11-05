# Implementation checklist

## Proposal MVP acceptance

- [x] Text and image review, with findings persisted to workspace-scoped reviews.
- [x] Admin-confirmed and versioned knowledge entries; PDF/DOCX import creates pending passages.
- [x] AI-assisted text/vision analysis is opt-in; evidence is drawn from approved source retrieval and citation checked. `CANNOT VERIFY` is used where supported facts are absent.
- [x] Human decisions, override reasons, comments, assignments, content versions, and audit history are persisted.
- [x] Dashboard reads real workspace review/finding/decision data and surfaces repeated unsupported claims and contested rules.
- [x] Configurable review/upload retention and deletion paths exist; local/S3-compatible storage adapter implemented.
- [x] File signatures and parser size limits are checked; extracted document text can be reviewed in the draft editor.
- [x] Review decision permissions enforce review ownership/management or assignment; S3 encryption is configurable and defaults to AES256.
- [x] Applying eligible suggestions updates a unique flagged draft span, saves a version, retains prior decision reasons, and reruns analysis.
- [x] Workspace administrators can inventory and delete tracked uploads.
- [x] A Compose retention worker is configured; S3 and scheduled purge have not been live-tested.
- [x] Render deployment Blueprint prepared; Neon URL normalization and Cloudflare R2 encryption compatibility implemented.
- [x] Local backend/Alembic configuration reads `backend/.env`; remote PostgreSQL requires TLS and uses psycopg 3.
- [x] Live Neon PostgreSQL/pgvector connection, TLS, migrations, all 12 tables, vector schema/index, and Alembic drift check verified.
- [ ] Live AI/vision provider and S3 services remain unverified.
- [x] Backend integration tests pass (17); frontend production build and 4 generated-artifact smoke/accessibility checks pass.
- [ ] Interactive frontend workflows, full accessibility tests, and full security review remain open.
- [ ] Docker Compose runtime and public deployment remain unverified; no Git remote, hosting authorization, service secrets or public URL are available.

## Verification record

- Backend tests: 17 passed using SQLite and mocked external adapters.
- Frontend production build: passed.
- Frontend artifact smoke/accessibility checks: 4 passed.
- Python compilation: passed.
- Alembic upgrade and drift check: passed on SQLite through revision `0005_finding_decisions`.
- Compose and Render Blueprint YAML: parsed; Docker/Compose is unavailable to launch the stack.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [DEPLOYMENT.md](DEPLOYMENT.md) for outstanding external dependencies. Mocked or SQLite-only checks do not constitute live service verification.

- Latest backend suite: 17 passed with `DATABASE_URL=sqlite://`; tests did not connect to Neon.
- Neon: active psycopg TLS verified; revision `0005_finding_decisions`; pgvector 0.8.6; `vector(1536)` embedding column; cosine HNSW index; Alembic drift check clean.
