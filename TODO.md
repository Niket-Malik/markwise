# TODO

Checkboxes distinguish implemented code from service/runtime verification. A feature is not considered production-verified merely because a local mock or SQLite test passes.

## Mandatory MVP

### AI, retrieval, and consent

- [x] OpenRouter Nemotron Super VL text/vision adapter, strict structured output, Gemini secondary, capability checks, bounded retries, quota-aware failover, circuit cooldown, and recovery.
- [x] Current Gemini model defaults: `gemini-3.5-flash` primary and `gemini-3.5-flash-lite` alternate for text and vision.
- [x] Approved-source retrieval, citation/excerpt validation, `CANNOT VERIFY`, intent-guarded suggestions, failed-analysis state, Knowledge Base fallback, and manual retry.
- [x] Consent checks and generation/version checks before external provider requests; no retries after consent withdrawal.
- [x] OpenRouter Nemotron Embed adapter with 1536-D validation and `vector(1536)` compatibility. OpenAI embedding vectors are tagged separately and not compared with the new embedding space.
- [x] Missing-vector detection and bounded/resumable backfill endpoint.
- [ ] Live quota-safe OpenRouter/Gemini text, vision, and embedding checks when explicitly authorized and account capacity is available.
- [ ] Add a durable, observable embedding task queue if automatic processing/recovery is required. Current confirmation embeds synchronously; failures persist null vectors and require controlled backfill. There is no automatic confirmation retry.

### Documents and visual review

- [x] PDF/DOCX guideline extraction, pending admin-confirmation queue, source upload storage, PDF text extraction, and Tesseract OCR integration for scanned PDFs/images.
- [x] Vision-provider path plus local palette, contrast-estimate, alt-text, and optional reference-logo checks.
- [ ] Verify scanned OCR and real vision behavior in the target deployment image/provider.
- [ ] Improve visual accessibility checks beyond heuristics and obtain specialist review; no WCAG certification is provided.

### Data, roles, and security

- [x] Workspace-scoped persistence, route authorization, input/file limits, audit events, review versions, reasoned finding decisions, and retention controls.
- [x] Admin-only workspace edit/delete, team edit/remove, knowledge edit/delete, and approved/rejected review deletion. Last Admin removal/demotion is rejected.
- [x] Local and S3-compatible storage; Supabase S3 private-bucket configuration and probe utility.
- [x] Compose retention loop and Render daily retention job configuration.
- [ ] Verify scheduled purge and recovery against deployment services; add operational alerting.
- [ ] Add shared rate limiting at the edge before multi-replica deployment; current API and provider budgets are per process.
- [ ] Complete full adversarial security review, proxy-trust/session review, and backup/restore drill.
- [ ] Review workspace deletion partial-failure recovery: storage objects are deleted before DB records; if a later object deletion fails, remaining DB rows can be retried but previously deleted objects cannot be restored automatically.

### Insights, accessibility, and tests

- [x] Real-data dashboard counts, recurring findings, dismissed/overridden items, contested rules, and guidance-gap indicators.
- [x] Responsive layout primitives, labelled forms, keyboard focus styling, navigation landmark, and skip link.
- [x] Forest-green Markwise M/check branding with SVG favicon, 16/32/48px ICO, 180×180 Apple Touch Icon, 192/512px app icons, web manifest, and Next.js metadata. Local production build passed; deployed asset delivery still follows the Render frontend build.
- [x] Focused Admin management backend regression tests and frontend Admin control tests.
- [ ] Run the complete backend suite and expand PostgreSQL integration/security coverage before release.
- [ ] Add interactive browser end-to-end workflows and automated accessibility testing; current Node checks are not screen-reader certification.
- [ ] Perform manual keyboard/screen-reader and responsive review in target browsers.

### Docker and deployment

- [x] Compose and Render configuration for PostgreSQL/pgvector, API, frontend, migrations, S3 settings, and retention worker.
- [ ] Verify full Docker Compose startup and service-to-service flows in an environment with Docker available.
- [ ] Read-only confirm production Alembic revision; last revision explicitly reported by the owner was `0006_review_visual_assets`, while checked-in head is `0008_consent_generation`.
- [ ] Verify production migration, CORS, health, cron, AI consent, private storage, and major user workflows after reviewed release.
- [ ] Verify current Render frontend/backend health, branding asset delivery, monitoring, and alerts. Do not record deployed URLs in project documentation; contact the project owner for live URLs.

Markwise is already deployed and running. For access to the live application or backend URLs, please contact the project owner.

## Stretch and future

- [ ] Email invitations and account recovery.
- [ ] SSO and secure refresh-cookie/session lifecycle.
- [ ] Slide-deck review, CMS integrations, multilingual guidance, and on-prem model support.

## Latest recorded local checks

- Backend Admin management focus: 5 passed.
- Frontend Node suite: 14 passed.
- Frontend production build: passed.
- `git diff --check`: passed (line-ending warnings only).
- These results are from the latest implementation verification recorded before this documentation-only update. No tests were run for documentation edits.
