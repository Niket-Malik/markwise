# TODO

Items are marked done only to the level tested locally. External-service and public-deployment requirements remain open until verified with real services.

## Mandatory MVP

### Phase 1 — AI and grounded review

- [x] Gemini-first text and vision adapters with OpenAI secondary models, workspace consent gating, source grounding, citations, CANNOT VERIFY, and guarded suggestions.
- [x] Bounded 429/quota/5xx/timeout/connection retries, exponential jitter with Retry-After, provider circuit cooldown/recovery, capability-aware image failover, safe logs, and admin-visible provider status.
- [x] Provider-internal model order: Gemini `gemini-3.8-flash` → `gemini-3.7-flash`; OpenAI `gpt-6-luna` → `gpt-5.6-luna`. Temporary model errors can try same-provider alternate; 429/quota skips the alternate and changes provider.
- [x] Model-level tests cover Gemini primary-to-alternate, Gemini quota-to-OpenAI, OpenAI primary-to-alternate, OpenAI quota skipping its alternate, strict JSON schema on text/image calls, and fail-closed vision capability checks.
- [x] Preserve OpenAI `text-embedding-3-small` vectors at 1536 dimensions; no schema or stored vector-space change.
- [x] Approved-source chunking, pgvector cosine retrieval, lexical fallback, embedding backfill, and cleanup on consent withdrawal.
- [x] Validate source IDs and exact excerpts (or bounded image regions); validate citations; explicitly abstain as `CANNOT VERIFY` when evidence is missing.
- [x] Ask opted-in AI to identify claims even when retrieval returns no matching source, and validate abstentions without adding invented citations.
- [x] Guard suggestions for intent preservation and supported numeric claims.
- [ ] Complete a successful live Gemini/OpenAI analysis and 1536-dimensional OpenAI embedding check. Probe attempted 2026-11-06: Gemini model metadata access passed (HTTP 200), generation returned 503 for text and image; OpenAI returned 429 quota exhaustion for text, image, and embeddings. Simulated Gemini outage did route to OpenAI, but secondary quota blocked completion. See PROJECT_STATUS.md.
- [x] Resolved backend settings now use `gpt-6-luna` → `gpt-5.6-luna` and `gemini-3.8-flash` → `gemini-3.7-flash`; no credential values were read or changed.
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
- [ ] Add shared ingress rate limiting and complete the session, proxy-trust, secrets, logging, and security review.

### Phase 4 — Insights, accessibility, and tests

- [x] Dashboard reports real review/finding counts, recurring unsupported claims, dismissed decisions, and contested rules.
- [x] Keyboard login navigation inspected; CSS text contrast values were statically checked against the application’s light backgrounds.
- [x] AI failover adapter tests: 20 passed, including text and vision alternates, provider-level 429 skip behavior, strict JSON schema, and fail-closed image capability.
- [x] Quota-safe final run: complete backend suite 39 passed using SQLite and mocks with the provider disabled; 0 live API calls.
- [x] Re-run frontend tests (4 passed) and Next.js production build after provider changes (no frontend code changed during the final quota-safe verification).
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
