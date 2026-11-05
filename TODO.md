# TODO

Items are marked done only to the level tested locally. External-service and public-deployment requirements remain open until verified with real services.

## Mandatory MVP

### Phase 1 — AI and grounded review

- [x] OpenAI-compatible chat, embedding, and vision adapters; workspace consent gating.
- [x] Approved-source chunking, pgvector cosine retrieval, lexical fallback, embedding backfill, and cleanup on consent withdrawal.
- [x] Validate source IDs and exact excerpts (or bounded image regions); validate citations; explicitly abstain as `CANNOT VERIFY` when evidence is missing.
- [x] Ask opted-in AI to identify claims even when retrieval returns no matching source, and validate abstentions without adding invented citations.
- [x] Guard suggestions for intent preservation and supported numeric claims.
- [ ] Run live provider and semantic-search checks with configured AI credentials and PostgreSQL/pgvector.
- [x] Configure backend/.env loading for local runtime/Alembic; enforce TLS for remote PostgreSQL and use psycopg 3 with Neon pooler-safe migration settings.
- [x] Connect to Neon from backend/.env over TLS; apply Alembic through 0005, verify all 12 tables, pgvector 0.8.6, vector(1536), cosine HNSW index, and no Alembic drift.

### Phase 2 — Documents and visual review

- [x] Parse PDF/DOCX and import passages as admin-pending knowledge; store source uploads.
- [x] OCR scanned PDFs/images through Tesseract integration; OCR errors are surfaced.
- [x] Optional vision model review with source validation; logo comparison to a user-supplied reference; approved palette screening; OCR-derived contrast estimate; alt-text checks.
- [x] Populate the draft editor with extracted PDF/DOCX text; validate upload file signatures, DOCX package limits, image format, and pixel bounds before storage.
- [x] Admin upload inventory and deletion are available in Settings; applying an eligible suggestion updates one unique text span, records a version, and reruns analysis.
- [ ] Verify scanned-file OCR and vision review using the configured deployment image/provider.
- [ ] Review heuristics with accessibility specialists and add more complete visual criteria such as logo clear-space and full contrast sampling.

### Phase 3 — Data lifecycle and security

- [x] Review content versions, reasoned finding decisions, workspace audit events, deletion, and configurable review/upload retention.
- [x] Local/S3-compatible object storage adapter and scheduled purge implementation.
- [x] Organization scoping, privileged-role checks, upload limits, and process-local rate limiting.
- [x] Restrict finding decisions and submissions to the review creator, manager/admin, or assigned reviewer; keep stale finding reasons auditable.
- [x] AWS S3 storage writes support AES256 or KMS server-side encryption; Cloudflare R2 uses managed encryption without unsupported SSE headers.
- [ ] Verify tenant boundaries and authorization across the full API with adversarial security tests.
- [ ] Verify S3 bucket policy, encryption, deletion, backups, and scheduled purge against a real service; add job monitoring.
- [ ] Add shared ingress rate limiting and complete the session, proxy-trust, secrets, logging, and security review.

### Phase 4 — Insights, accessibility, and tests

- [x] Dashboard reports real review/finding counts, recurring unsupported claims, dismissed decisions, and contested rules.
- [x] Keyboard login navigation inspected; CSS text contrast values were statically checked against the application’s light backgrounds.
- [x] Backend suite: 17 integration tests pass; frontend production build and 4 production-artifact smoke/accessibility checks pass.
- [ ] Add interactive frontend end-to-end workflow and automated accessibility tests.
- [ ] Expand backend security and PostgreSQL integration tests; complete a manual assistive-technology and responsive accessibility review.

### Phase 5 — Docker and deployment

- [x] Compose configuration includes PostgreSQL/pgvector, health checks, API migration startup, frontend dependency readiness, localhost-only DB port, and a periodic retention worker; YAML syntax was parsed.
- [ ] Start the full Compose stack and verify service-to-service workflows (Docker CLI is unavailable in this environment).
- [ ] Provision production database, object storage, AI credentials, secrets, CORS, HTTPS, monitoring, and backups.
- [x] Prepare a Render Blueprint for frontend/backend, migration and daily retention worker; normalize Neon URLs and account for Cloudflare R2 managed encryption.
- [ ] Deploy frontend/backend and verify public HTTPS URLs and health checks (requires source Git remote, Render authorization, Neon/R2 services and AI secrets).

## Stretch / future

- [ ] Invitations, password recovery, SSO, slide-deck review, CMS integrations, multilingual guidance, and on-prem model support.


## GitHub and deployment safety

- [x] Expand `.gitignore` for nested environment/secrets files, uploads, local database/export files, private keys, cloud credentials, dependencies, generated builds, and test artifacts.
- [x] Initialize local Git and stage the 41-file source/deployment proposal; scan all staged files for high-confidence credential patterns (none found). Empty history and no remote confirmed.
- [ ] Add a GitHub remote and publish only after explicit approval; no commit, remote, or push has been created.
