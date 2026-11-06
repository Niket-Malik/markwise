# Implementation checklist

## Proposal MVP acceptance

- [x] Text and image review, with findings persisted to workspace-scoped reviews.
- [x] Admin-confirmed and versioned knowledge entries; PDF/DOCX import creates pending passages.
- [x] AI-assisted text/vision analysis is opt-in; evidence is drawn from approved source retrieval and citation checked. `CANNOT VERIFY` is used where supported facts are absent.
- [x] Gemini model order (`gemini-3.8-flash` → `gemini-3.7-flash`) and OpenAI fallback order (`gpt-6-luna` → `gpt-5.6-luna`) support text, vision, and structured JSON; temporary model failover and provider-level 429/quota routing are implemented with retry/circuit recovery and capability checks.
- [x] Keep OpenAI `text-embedding-3-small` at `vector(1536)`; no migration or embedding dimension change.
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
- [ ] Successful live Gemini/OpenAI/embedding calls remain unverified: last probe returned Gemini 503 and OpenAI quota 429. Supabase Storage probe previously passed its temporary upload/download/privacy/cleanup workflow.
- [x] Full backend suite rerun after provider changes: 35 passed on SQLite with mocked external calls; frontend tests 4 passed; Next.js production build passed.
- [ ] Interactive frontend workflows, full accessibility tests, and full security review remain open.
- [ ] Docker Compose runtime and public deployment remain unverified; hosting authorization, Supabase storage secrets or a verified public URL are unavailable; `origin/main` exists but visibility is unknown.

## Verification record

- Backend tests: 18 passed using SQLite and mocked external adapters.
- AI model/provider failover tests: 16 passed with mocked providers; no live model responses were used in these tests.
- Latest full backend run: 35 passed with test-only `SECRET_KEY`; frontend `npm test`: 4 passed; `npm run build`: passed. No live AI calls were made during this implementation.
- Frontend production build: passed.
- Frontend artifact smoke/accessibility checks: 4 passed.
- Python compilation: passed.
- Alembic upgrade and drift check: passed on SQLite through revision `0005_finding_decisions`.
- Compose and Render Blueprint YAML: parsed; Docker/Compose is unavailable to launch the stack.

See [PROJECT_STATUS.md](PROJECT_STATUS.md), [TODO.md](TODO.md), and [DEPLOYMENT.md](DEPLOYMENT.md) for outstanding external dependencies. Mocked or SQLite-only checks do not constitute live service verification.

- Latest backend suite: 18 passed with `DATABASE_URL=sqlite://`; tests did not connect to Neon.
- Neon: active psycopg TLS verified; revision `0005_finding_decisions`; pgvector 0.8.6; `vector(1536)` embedding column; cosine HNSW index; Alembic drift check clean.
