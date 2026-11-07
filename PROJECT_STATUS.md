# Markwise project status

## Scope and deployment state

Development continues in the existing FastAPI, SQLAlchemy/Alembic, PostgreSQL/pgvector, and Next.js project. This file records implementation state separately from external-service verification.

Markwise is already deployed and running. For access to the live application or backend URLs, please contact the project owner. No deployed URLs are stored in the documentation. This documentation update did not contact live services; the latest production Alembic revision explicitly reported during migration troubleshooting was `0006_review_visual_assets`, and the attempted upgrade beyond it failed and rolled back. The checked-in Alembic head is `0008_consent_generation`. Confirm the actual production revision before applying pending migrations.

## Implemented in the current code

- Workspace registration, bearer-token authentication, roles (`admin`, `manager`, `reviewer`, `creator`), workspace-scoped reviews and knowledge, comments, assignments, decisions, content versions, findings, and audit events.
- Admin-only workspace rename/delete, team member edit/removal, knowledge rule edit/delete, and deletion of approved/rejected reviews. The backend enforces roles and workspace scope. The last Admin cannot be demoted or removed. Removed members retain historical references but cannot log in.
- PDF/DOCX guideline import and passage extraction. The source upload is stored as an asset; extracted passages enter a pending queue and require Admin confirmation.
- OCR via Tesseract for scanned PDFs/images, DOCX parsing, upload signature/size validation, PDF text extraction, and extraction errors returned to the UI.
- Consent-gated OpenRouter-first text/vision analysis with Gemini secondary. Current configured Gemini models are `gemini-3.5-flash` primary and `gemini-3.5-flash-lite` alternate for text and vision. Structured output, image capability checks, approved-source retrieval, citation validation, `CANNOT VERIFY`, guarded suggestions, bounded retries, quota-aware failover, circuit cooldown/recovery, and provider status are implemented.
- Failed AI analysis is distinct from completed clean analysis. A deterministic Knowledge Base fallback uses approved workspace guidance and can return `CANNOT VERIFY`; it is not labelled an AI success. Manual AI retry is available on eligible failed/incomplete reviews; no scheduled AI retry is configured.
- Finding Apply, Edit suggestion, and Dismiss-with-reason update persisted finding records. Apply writes a review version and resolves only the selected finding; edit updates that finding; dismiss stores its reason. Finding History is loaded from persisted decisions. These actions do not trigger full AI re-analysis or regenerate sibling findings.
- PostgreSQL/pgvector semantic retrieval and lexical fallback. OpenRouter Nemotron Embed requests 1536 dimensions for the existing `vector(1536)` column; embedding-space metadata prevents comparison with old OpenAI vectors. Consent generation columns invalidate in-flight operations before subsequent outbound calls.
- Guideline confirmation attempts chunk embedding synchronously when external-AI and OpenRouter consents/configuration allow it. If that request fails, the knowledge can still become active with chunks whose embeddings are null. Confirmation does not automatically retry or enqueue a durable job. Admin backfill scans missing/stale chunks in bounded batches; it can be started from the UI and is also queued when privacy settings are saved with both consents enabled. A backfill failure pauses, and can be resumed later.
- Local/S3-compatible storage, private Supabase Storage S3 support, upload inventory/deletion for Admins, configurable review/upload retention, and Compose/Render retention worker configuration.
- Image palette/contrast/alt-text heuristics and optional vision analysis/reference-logo comparison. These are estimates and not a formal accessibility or exact-logo certification.
- Workspace dashboard analytics and insights based on stored review/findings/decisions/knowledge data.
- Responsive Next.js UI, keyboard focus indicators, form labels, navigation landmarks, and skip link.
- Custom forest-green Markwise favicon/app branding: M plus verification check, multi-resolution ICO, SVG, Apple Touch Icon, 192px/512px app icons, web manifest, and Next.js metadata in `frontend/app/layout.tsx`.

## Known limitations and partial work

- Live AI generation, image analysis, and embedding availability depend on provider account credentials, model availability, and quota. Mocked tests do not establish live provider health. OpenRouter free inputs may be logged or used to improve provider products; Admin consent is required and should not be enabled for confidential/personal data.
- OpenRouter embedding generation during confirmation is a single bounded HTTP request path with no automatic retry. Failed chunks remain detectable by null vector metadata; recovery is manual/controlled, not a durable task queue.
- Existing vectors from another embedding space need re-indexing under the current consented model before semantic retrieval can use them. Lexical retrieval remains available.
- Rate limiting and OpenRouter request budgets are in-process, not shared across workers or instances. Production needs edge/shared limits.
- Tesseract/visual heuristics need more deployment-image and assistive-technology evaluation. UI unit/smoke tests are not full browser E2E or screen-reader certification.
- Workspace deletion removes S3 objects before deleting related DB records. If storage deletion fails partway, database rows remain for retry, but objects already removed earlier in the loop cannot be restored automatically.
- Invitations, password recovery, SSO, secure refresh-cookie flow, slide-deck/CMS integrations, multilingual/on-prem providers, monitoring/alerting, and a backup-restore drill are not implemented or verified.

## Verification record

Latest locally recorded focused verification (2026-11-07):

- Backend Admin management tests: **5 passed** on a local isolated test database; no Neon/production database was contacted.
- Frontend Node tests: **14 passed**.
- Next.js production build: **passed**.
- `git diff --check`: **passed** with Git line-ending conversion warnings only.

These checks were recorded before this documentation-only update. The complete backend suite was not rerun for the documentation changes. Earlier test counts in prior working notes conflicted; those historical counts are intentionally not used as the current suite result.

Other prior verification reported by the project history (not repeated during this task): a Supabase private-bucket probe passed temporary upload, server-side download, anonymous denial, and cleanup; Neon connection and pgvector schema were previously verified. Do not infer that a production migration has been applied from those earlier checks. Current production database state was not accessed.

## Migrations

- Current local Alembic chain: `0001_initial` → `0002_ai_retrieval` → `0003_asset_storage_retention` → `0004_review_retention` → `0005_finding_decisions` → `0006_review_visual_assets` → `0007_openrouter_embedding_space` → `0008_consent_generation`.
- Migration 0007 tracks OpenRouter consent and embedding-space identity. Its downgrade clears rebuildable vectors before removing their space metadata; source text and knowledge records remain.
- Migration 0008 adds separate general external-AI and OpenRouter consent generation columns.
- Latest production revision explicitly reported in prior user troubleshooting was 0006, and an upgrade beyond it failed/rolled back. Production revision must be checked read-only before any migration; this task did not contact or modify Neon.

## Remaining release work

- Confirm production Alembic revision and apply reviewed pending migrations through the release procedure with a backup.
- Verify Render frontend availability, health, CORS, migrations, cron configuration, and critical workflows against production without publishing deployed URLs in repository documentation.
- Perform quota-safe live text/vision/embedding checks when provider capacity is available and explicitly authorized.
- Verify Compose runtime, production retention purge, monitoring/alerting, shared rate limiting, backup restore, responsive behavior, and assistive-technology workflows.
- Complete full backend suite and interactive end-to-end/security testing before a production-readiness claim.

See [README.md](README.md) for setup and user workflows, [TODO.md](TODO.md) for remaining work, [IMPLEMENTATION_CHECKLIST.md](IMPLEMENTATION_CHECKLIST.md) for acceptance tracking, and [DEPLOYMENT.md](DEPLOYMENT.md) for release procedure.
