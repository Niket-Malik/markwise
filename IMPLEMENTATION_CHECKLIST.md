# Implementation checklist

Status is based on checked-in implementation and recorded local verification. `[x]` means code exists; it does not imply that a live provider, production service, or deployment workflow has been verified.

## Product and workflow acceptance

- [x] Registration/login, workspace isolation, bearer-token auth, roles, team management, and review assignment/decision/comment/version flows.
- [x] Admin-only workspace rename/delete, team member edit/remove, knowledge edit/delete, and approved/rejected review deletion. Backend role and workspace checks are present; last Admin cannot be demoted or removed.
- [x] PDF/DOCX guideline import, extracted candidate passages, pending Admin confirmation, original upload storage, OCR integration, and DOCX parsing.
- [x] Confirmation creates knowledge chunks and attempts immediate embedding when both external-AI and OpenRouter consent/configuration are enabled.
- [x] Failed embedding requests leave null-vector chunks for lexical use and later bounded backfill. No automatic retry/job is created by the confirmation request; Admin can manually resume. Saving privacy settings with both permissions enabled can queue a backfill.
- [x] Text/image review with actual provider adapters, approved-source retrieval, validated citations, `CANNOT VERIFY`, guarded rewrite suggestions, failed-analysis status, KB fallback, and manual AI retry.
- [x] Finding Apply/Edit/Dismiss persistence, review versions/audit records, and persisted Finding History; individual decisions do not cause a full AI re-analysis or recreate sibling findings.
- [x] Gemini text/vision models: `gemini-3.5-flash` primary → `gemini-3.5-flash-lite` alternate. OpenRouter is first when OpenRouter-specific consent is enabled; Gemini is secondary when configured/consented.
- [x] OpenRouter Nemotron Embed outputs are validated as 1536-D and stored in PostgreSQL `vector(1536)`. Migration 0007 distinguishes embedding spaces; migration 0008 stores consent generations.
- [x] S3-compatible storage adapter, private Supabase Storage configuration support, workspace retention settings, and scheduled worker configuration.
- [x] Dashboard analytics use stored workspace data; visual accessibility measurements are explicitly heuristic.
- [x] Forest-green Markwise icon set: M/check logo, SVG favicon, 16/32/48px ICO, 180×180 Apple Touch Icon, 192px/512px app icons, web manifest, and Next.js title/icon metadata.

## Verification and operations

- [x] Focused Admin backend regression tests: 5 passed in the latest recorded run.
- [x] Frontend Node test suite: 14 passed in the latest recorded run.
- [x] Next.js production build passed in the latest recorded run.
- [x] `git diff --check` passed with line-ending warnings only.
- [ ] Full backend suite not rerun as part of the latest documentation update; run it before release using an explicitly test-only database configuration.
- [ ] Live OpenRouter/Gemini generation and embeddings remain dependent on configured accounts, model availability, and quota. Do not treat mocks as live verification.
- [ ] Full Compose service startup and integration verification.
- [ ] Verify production Alembic revision and migrate to local head `0008_consent_generation` only after backup and migration review. Latest owner-reported production revision was `0006_review_visual_assets`; this documentation update did not access production.
- [ ] Verify current Render health, frontend availability, CORS, retention cron, storage, real AI/citation workflow, branding asset delivery, and production permissions.
- [ ] Shared rate limits, monitoring/alerting, backup restore drill, full security audit, browser E2E, and assistive-technology evaluation.

## Known limitations

- OpenRouter free endpoints may be unavailable or quota-limited. Their processing may log/use request content for provider improvement; both Admin consent settings are required for OpenRouter.
- Confirmation-time embedding failure is represented by missing vector values, not a durable queued job; recovery is controlled backfill.
- API and provider rate limits are process-local, not global across replicas.
- OCR, contrast, palette, and logo analysis are heuristic or runtime-dependent; there is no formal WCAG or exact-logo certification.
- Workspace deletion removes stored objects before deleting DB rows. A storage failure partway through can leave already removed objects unavailable even while workspace records remain for retry.
- Invitations, password recovery, SSO, slide-deck/CMS integrations, and multilingual/on-prem support are not implemented.

## Deployment record

Markwise is already deployed and running. For access to the live application or backend URLs, please contact the project owner. No deployed URL is included here. Production workflow verification remains subject to the release checks in [DEPLOYMENT.md](DEPLOYMENT.md).
