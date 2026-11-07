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


def test_ai_provides_contextual_unverified_rewrite_without_resolving_finding():
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
        assert finding.status == "open"  # Suggestion generation alone does not dismiss the finding.
        result = main.finding_decision(finding.id, {"decision": "accepted"}, user, db)
        assert result["content_updated"] is True
        assert review.content == replacement
        versions = db.scalars(select(main.ReviewVersion).where(main.ReviewVersion.review_id == review.id)).all()
        assert len(versions) == 1  # Initial version is not created by this route-level unit setup.
        assert finding.status == "stale"  # The applied finding is retained in history, no longer active.
        assert db.scalar(select(main.FindingDecision.decision).where(main.FindingDecision.finding_id == finding.id)) == "accepted"
        assert db.scalar(select(main.AuditEvent.action).where(main.AuditEvent.entity_id == finding.id)) == "finding.accepted"
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
            "reason": "Make this more specific to our voice.",
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
            ("edited", "Make this more specific to our voice."),
            ("dismissed", "This phrase is required in this approved context."),
        }
        actions = db.scalars(select(main.AuditEvent.action).where(main.AuditEvent.entity_id == finding.id)).all()
        assert set(actions) == {"finding.edited", "finding.dismissed"}
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
