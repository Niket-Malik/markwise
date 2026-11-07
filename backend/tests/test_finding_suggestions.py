from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app import main
from backend.app.ai import CandidateFinding


def make_session():
    engine = create_engine("sqlite://")
    main.Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)()


def make_review(db, *, content, kind, excerpt, suggestion):
    organization = main.Organization(name="Suggestion Test", external_ai_enabled=False)
    db.add(organization)
    db.flush()
    user = main.User(organization_id=organization.id, email="reviewer@example.test", name="Reviewer",
                     role="admin", password_hash="test-only")
    db.add(user)
    db.flush()
    review = main.Review(organization_id=organization.id, creator_id=user.id, title="Draft",
                         content=content, status="analysis_incomplete", ai_status="complete")
    db.add(review)
    db.flush()
    finding = main.Finding(review_id=review.id, kind=kind, excerpt=excerpt,
                           explanation="Test finding", severity="high", confidence=0.9,
                           citation="No matching active approved fact in this workspace.",
                           suggestion=suggestion, status="open")
    db.add(finding)
    db.commit()
    return organization, user, review, finding


def test_ai_provides_contextual_unverified_rewrite_without_resolving_finding(monkeypatch):
    content = "Our service doubles your sales"
    replacement = "Explore our service to find a workflow that fits your team."
    candidate = CandidateFinding(
        kind="claim", excerpt=content,
        explanation="No evidence supports this performance claim.",
        severity="high", confidence=0.9, cited_source_ids=[], cannot_verify=True,
        suggested_rewrite=replacement, intent_preserved=True,
    )
    source = main.Knowledge(id=1, organization_id=1, kind="rule", title="Voice guide",
                            body="Use a clear, practical tone.", source="Brand guide",
                            passage="Use plain, helpful language.", status="active")
    finding_data = main.validate_ai_findings(content, [candidate], [source], [])

    assert len(finding_data) == 1
    assert finding_data[0]["kind"] == "cannot_verify"
    assert finding_data[0]["suggestion"] == replacement
    assert finding_data[0]["citation"] == "No matching active approved fact was validated in the retrieved workspace sources."
    # No evidence means an unverified rewrite cannot introduce numeric claims.
    unsafe = candidate.model_copy(update={"suggested_rewrite": "Explore our service and increase sales by 20%."})
    assert main.validate_ai_findings(content, [unsafe], [source], [])[0]["suggestion"] == ""

    engine, db = make_session()
    try:
        _organization, user, review, finding = make_review(
            db, content=content, kind=finding_data[0]["kind"], excerpt=content,
            suggestion=finding_data[0]["suggestion"],
        )
        sibling = main.Finding(review_id=review.id, kind="restricted_term", excerpt="workflow",
                               explanation="Keep this separate", severity="medium", confidence=0.8,
                               citation="Approved voice guide", suggestion="Use process", status="open")
        db.add(sibling)
        db.commit()
        sibling_id = sibling.id
        def unexpected_reanalysis(*_args, **_kwargs):
            raise AssertionError("Applying one suggestion must not run AI analysis")
        monkeypatch.setattr(main, "analyse_review_content", unexpected_reanalysis)
        assert finding.status == "open"  # Suggestion generation alone does not dismiss the finding.
        result = main.finding_decision(finding.id, {"decision": "accepted"}, user, db)
        assert result["content_updated"] is True
        assert review.content == replacement
        versions = db.scalars(select(main.ReviewVersion).where(main.ReviewVersion.review_id == review.id)).all()
        assert len(versions) == 1  # Initial version is not created by this route-level unit setup.
        assert finding.status == "accepted"
        assert sibling.id == sibling_id and sibling.status == "open"
        db.expire_all()  # Simulate reloading persisted data after navigation/refresh.
        reloaded = main.serialize_review(db.get(main.Review, review.id))
        assert [row["id"] for row in reloaded["findings"]] == [sibling_id]
        assert reloaded["clean_review"] is False
        assert len(db.scalars(select(main.Finding).where(main.Finding.review_id == review.id)).all()) == 2
        assert db.scalar(select(main.FindingDecision.decision).where(main.FindingDecision.finding_id == finding.id)) == "accepted"
        assert db.scalar(select(main.AuditEvent.action).where(main.AuditEvent.entity_id == finding.id)) == "finding.accepted"
        try:
            main.finding_decision(finding.id, {"decision": "accepted"}, user, db)
            assert False, "A terminal finding cannot be applied twice"
        except main.HTTPException as exc:
            assert exc.status_code == 409
    finally:
        db.close()
        engine.dispose()


def test_edit_and_dismiss_finding_actions_persist_reason_status_and_audit():
    engine, db = make_session()
    try:
        _organization, user, _review, finding = make_review(
            db, content="We offer guaranteed outcomes.", kind="restricted_term",
            excerpt="guaranteed", suggestion="Review this wording against the approved guidance.",
        )
        edited = main.finding_decision(finding.id, {
            "decision": "edited", "suggestion": "We work toward outcomes that matter to you.",
        }, user, db)
        assert edited["status"] == "edited"
        assert finding.suggestion == "We work toward outcomes that matter to you."

        dismissed = main.finding_decision(finding.id, {
            "decision": "dismissed", "reason": "This phrase is required in this approved context.",
        }, user, db)
        assert dismissed["status"] == "dismissed"
        decisions = db.scalars(select(main.FindingDecision).where(
            main.FindingDecision.finding_id == finding.id
        )).all()
        assert {(row.decision, row.reason) for row in decisions} == {
            ("edited", ""),
            ("dismissed", "This phrase is required in this approved context."),
        }
        actions = db.scalars(select(main.AuditEvent.action).where(main.AuditEvent.entity_id == finding.id)).all()
        assert set(actions) == {"finding.edited", "finding.dismissed"}
        db.expire_all()  # Simulate reopening the review from persisted storage.
        reloaded = main.serialize_review(db.get(main.Review, _review.id))
        assert reloaded["findings"] == []
        assert reloaded["clean_review"] is False
        try:
            main.finding_decision(finding.id, {"decision": "dismissed", "reason": "Duplicate dismissal"}, user, db)
            assert False, "A dismissed finding cannot be dismissed twice"
        except main.HTTPException as exc:
            assert exc.status_code == 409
        assert len(db.scalars(select(main.FindingDecision).where(main.FindingDecision.finding_id == finding.id)).all()) == 2
    finally:
        db.close()
        engine.dispose()


def test_review_finding_history_persists_decisions_rewrites_and_workspace_scope():
    engine, db = make_session()
    try:
        _organization, user, review, applied = make_review(
            db, content="Our service doubles your sales.", kind="cannot_verify",
            excerpt="Our service doubles your sales", suggestion="Explore our service for your team's workflow",
        )
        dismissed = main.Finding(review_id=review.id, kind="restricted_term", excerpt="guaranteed",
                                 explanation="Avoid guarantees", severity="high", confidence=0.9,
                                 citation="Rule #2: Avoid guarantees", suggestion="", status="open")
        still_active = main.Finding(review_id=review.id, kind="tone", excerpt="workflow",
                                    explanation="Use direct wording", severity="low", confidence=0.8,
                                    citation="Tone guide", suggestion="Use a clear workflow", status="open")
        db.add_all([dismissed, still_active]); db.commit()
        main.finding_decision(applied.id, {"decision": "accepted"}, user, db)
        main.finding_decision(dismissed.id, {
            "decision": "dismissed", "reason": "The legal team approved this required wording."
        }, user, db)

        history = main.review_finding_history(review.id, user, db)
        assert [(entry["finding_id"], entry["status"]) for entry in history] == [
            (applied.id, "Applied"), (dismissed.id, "Dismissed")
        ]
        assert history[0]["excerpt"] == "Our service doubles your sales"
        assert history[0]["applied_rewrite"] == "Explore our service for your team's workflow"
        assert history[1]["reason"] == "The legal team approved this required wording."
        assert history[1]["timestamp"]

        db.expire_all()  # Review history is reloaded from persisted decisions, not UI state.
        persisted = main.review_finding_history(review.id, user, db)
        assert [entry["id"] for entry in persisted] == [entry["id"] for entry in history]
        active = main.serialize_review(db.get(main.Review, review.id))["findings"]
        assert [entry["id"] for entry in active] == [still_active.id]

        other_org = main.Organization(name="Other workspace", external_ai_enabled=False)
        db.add(other_org); db.flush()
        outsider = main.User(organization_id=other_org.id, email="other@example.test", name="Other",
                             role="admin", password_hash="test-only")
        db.add(outsider); db.commit()
        try:
            main.review_finding_history(review.id, outsider, db)
            assert False, "A review's finding history must remain workspace scoped"
        except main.HTTPException as exc:
            assert exc.status_code == 404
    finally:
        db.close()
        engine.dispose()


def test_generic_unverified_advice_cannot_be_applied_as_a_rewrite():
    engine, db = make_session()
    try:
        content = "Our service doubles your sales"
        _organization, user, _review, finding = make_review(
            db, content=content, kind="cannot_verify", excerpt=content,
            suggestion="Confirm this claim with an approved source before publication.",
        )
        try:
            main.finding_decision(finding.id, {"decision": "accepted"}, user, db)
            assert False, "Generic advice must not be applied as draft copy."
        except main.HTTPException as exc:
            assert exc.status_code == 422
        assert finding.status == "open"
    finally:
        db.close()
        engine.dispose()
