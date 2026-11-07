# TODO

Items are marked done only to the level tested locally. External-service and public-deployment requirements remain open until verified with real services.

## Mandatory MVP

### Phase 1 — AI and grounded review

- [x] Consent-gated OpenRouter free text/vision adapter with Gemini secondary, source grounding, citations, CANNOT VERIFY, and guarded suggestions. OpenAI is not called by the configured free stack.
- [x] Bounded 429/quota/5xx/timeout/connection retries, exponential jitter with Retry-After, provider circuit cooldown/recovery, capability-aware image failover, safe logs, and admin-visible provider status.
- [x] Model order: OpenRouter `nvidia/nemotron-3.5-super-vl:free` → Gemini `gemini-3.5-flash` / `gemini-3.5-flash-lite`. HTTP 429/quota skips same-provider alternates; temporary model errors use only compatible model alternatives.
- [x] Mock tests cover OpenRouter text/vision strict JSON, model capability guard, embeddings, 1536 dimensions, workspace cache scope/revocation, OpenRouter quota-to-Gemini, and existing bounded retry/circuit behavior.
- [x] Use OpenRouter Nemotron Embed requested at 1536 dimensions; preserve the pgvector schema and tag model spaces with forward migration 0007. Existing OpenAI vectors are ignored and re-embedded only after explicit workspace consent.
- [x] Approved-source chunking, pgvector cosine retrieval, lexical fallback, embedding backfill, and cleanup on consent withdrawal.
- [x] Validate source IDs and exact excerpts (or bounded image regions); validate citations; explicitly abstain as `CANNOT VERIFY` when evidence is missing.
- [x] Ask opted-in AI to identify claims even when retrieval returns no matching source, and validate abstentions without adding invented citations.
- [x] Guard suggestions for intent preservation and supported numeric claims.
- [ ] Make a quota-safe live verification of OpenRouter/Gemini text/vision and OpenRouter embeddings when the owner authorizes it and provider capacity is available. No live calls were made during the free-stack change.
- [x] Added OpenRouter API/model configuration and optional Gemini model/fallback configuration to templates/Render Blueprint; local `backend/.env` was not modified.
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
- [x] AWS S3 storage writes support AES256 or KMS server-side encryption; Supabase Storage and Cloudflare R2 omit unsupported SSE headers.
- [ ] Verify tenant boundaries and authorization across the full API with adversarial security tests.
- [x] Add Supabase Storage S3 Signature V4/path-style support, private server-side get helper, and a temporary upload/download/anonymous-access/cleanup probe command.
- [x] Verify configured Supabase S3 connectivity, upload, authenticated download, anonymous access denial, and post-delete absence using a temporary object; credentials remain in ignored backend/.env.
- [ ] Verify storage deletion, backups, and scheduled purge against the real service; add job monitoring.
- [ ] Add shared ingress/provider rate limiting across replicas (current AI endpoint limit is 10/min/client IP and OpenRouter budget is 15/min/process) and complete the session, proxy-trust, secrets, logging, and security review.

### Phase 4 — Insights, accessibility, and tests

- [x] Dashboard reports real review/finding counts, recurring unsupported claims, dismissed decisions, and contested rules.
- [x] Keyboard login navigation inspected; CSS text contrast values were statically checked against the application’s light backgrounds.
- [x] Free-stack mocked tests exercise OpenRouter text/vision/embedding and Gemini fallback paths; final suite results below.
- [x] Free-stack verification: backend suite 68 passed with SQLite/mocks, frontend tests 6 passed and production build succeeded; zero live AI calls.
- [x] Frontend privacy opt-in UI, tests (6 passed) and Next.js production build verified.
- [ ] Add interactive frontend end-to-end workflow and automated accessibility tests.
- [ ] Expand backend security and PostgreSQL integration tests; complete a manual assistive-technology and responsive accessibility review.

### Phase 5 — Docker and deployment

- [x] Compose configuration includes PostgreSQL/pgvector, health checks, API migration startup, frontend dependency readiness, localhost-only DB port, and a periodic retention worker; YAML syntax was parsed.
- [ ] Start the full Compose stack and verify service-to-service workflows (Docker CLI is unavailable in this environment).
- [ ] Provision production database, object storage, AI credentials, secrets, CORS, HTTPS, monitoring, and backups.
- [x] Prepare a Render Blueprint for frontend/backend, migration and daily retention worker; normalize Neon URLs and configure Supabase Storage S3 support.
- [ ] Deploy frontend/backend and verify public HTTPS URLs and health checks (requires Render authorization, Supabase Storage credentials and AI secrets; existing remote visibility is unverified).

## Stretch / future

- [ ] Invitations, password recovery, SSO, slide-deck review, CMS integrations, multilingual guidance, and on-prem model support.


## GitHub and deployment safety

- [x] Expand `.gitignore` for nested environment/secrets files, uploads, local database/export files, private keys, cloud credentials, dependencies, generated builds, and test artifacts.
- [x] Initialize Git and check ignore rules; scan the 41-file `origin/main` history and current source tree for high-confidence credentials (none found; test DSNs are placeholders).
- [ ] Verify ownership/visibility of the existing `origin` remote; commit or push changes only after explicit approval.


## Supabase Storage connection

- [x] Implement Supabase S3 Signature V4/path-style client configuration, HTTPS enforcement, and omission of unsupported SSE headers.
- [x] Add server-side object download and opt-in upload/download/anonymous-access/cleanup probe; mocked storage roundtrip is covered by the backend suite.
- [x] Use endpoint, region, bucket and generated server-side access-key pair from ignored `backend/.env`; live private-bucket probe passed.
