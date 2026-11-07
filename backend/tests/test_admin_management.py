import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app import main


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    main.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    engine.dispose()


def make_org(db, name, email):
    org = main.Organization(name=name)
    db.add(org); db.flush()
    admin = main.User(organization_id=org.id, email=email, name="Admin", role="admin", password_hash="old-hash")
    db.add(admin); db.flush()
    return org, admin


def add_review(db, org, admin, *, status="approved", title="Review"):
    review = main.Review(organization_id=org.id, creator_id=admin.id, title=title,
                         content="A review draft.", status=status, ai_status="complete")
    db.add(review); db.flush()
    finding = main.Finding(review_id=review.id, kind="rule", excerpt="draft", explanation="Reason",
                           severity="medium", confidence=0.8, citation="Guide", suggestion="Copy", status="open")
    db.add(finding); db.flush()
    decision = main.FindingDecision(organization_id=org.id, finding_id=finding.id, user_id=admin.id,
                                    decision="dismissed", reason="Reviewed")
    version = main.ReviewVersion(organization_id=org.id, review_id=review.id, user_id=admin.id, content=review.content)
    db.add_all([decision, version]); db.commit()
    return review, finding, decision, version


def test_admin_workspace_edit_and_delete_are_scoped_and_remove_only_target_workspace(db_session, monkeypatch):
    db = db_session
    org, admin = make_org(db, "Old name", "admin@old.test")
    other_org, other_admin = make_org(db, "Other workspace", "admin@other.test")
    own_review, *_ = add_review(db, org, admin, title="Owned")
    other_review, *_ = add_review(db, other_org, other_admin, title="Unrelated")
    updated = main.update_organization(main.OrganizationUpdateIn(name="New name"), admin, db)
    assert updated["name"] == "New name"
    assert db.get(main.Organization, other_org.id).name == "Other workspace"

    def remove_asset(db, asset, user=None):
        db.delete(asset); db.commit()
    monkeypatch.setattr(main, "delete_stored_asset", remove_asset)
    deleted = main.delete_organization(admin, db)
    assert deleted["deleted"] is True
    assert db.get(main.Organization, org.id) is None
    assert db.get(main.Review, own_review.id) is None
    assert db.get(main.Organization, other_org.id) is not None
    assert db.get(main.Review, other_review.id) is not None


def test_team_admin_edit_remove_last_admin_guard_and_workspace_isolation(db_session):
    db = db_session
    org, admin = make_org(db, "Team", "admin@team.test")
    member = main.User(organization_id=org.id, email="member@team.test", name="Member", role="creator", password_hash="hash")
    other_org, other_admin = make_org(db, "Other", "admin@other-team.test")
    outsider = main.User(organization_id=other_org.id, email="outside@other.test", name="Outside", role="creator", password_hash="hash")
    db.add_all([member, outsider]); db.commit()

    updated = main.update_team_member(member.id, main.TeamMemberUpdateIn(
        name="Updated member", email="updated@team.test", role="reviewer"
    ), admin, db)
    assert updated["role"] == "reviewer" and updated["name"] == "Updated member"
    with pytest.raises(main.HTTPException) as cross_workspace:
        main.update_team_member(outsider.id, main.TeamMemberUpdateIn(
            name="Changed", email="changed@other.test", role="manager"
        ), admin, db)
    assert cross_workspace.value.status_code == 404
    with pytest.raises(main.HTTPException) as last_demote:
        main.update_team_member(admin.id, main.TeamMemberUpdateIn(
            name="Admin", email=admin.email, role="creator"
        ), admin, db)
    assert last_demote.value.status_code == 409
    with pytest.raises(main.HTTPException) as last_remove:
        main.remove_team_member(admin.id, admin, db)
    assert last_remove.value.status_code == 409

    another_admin = main.User(organization_id=org.id, email="second-admin@team.test", name="Second", role="admin", password_hash="hash")
    db.add(another_admin); db.commit()
    result = main.remove_team_member(member.id, admin, db)
    assert result["removed"] is True
    assert member.role == "removed"
    assert member not in db.scalars(select(main.User).where(main.User.organization_id == org.id, main.User.role != "removed")).all()
    with pytest.raises(main.HTTPException) as revoked:
        main.current_user(None, db)
    assert revoked.value.status_code == 401


def test_admin_knowledge_edit_delete_refreshes_chunks_and_preserves_unrelated_rules(db_session):
    db = db_session
    org, admin = make_org(db, "Knowledge", "admin@knowledge.test")
    other_org, other_admin = make_org(db, "Other knowledge", "admin@other-knowledge.test")
    item = main.Knowledge(organization_id=org.id, kind="rule", title="Old title", body="Old guidance",
                          category="voice", severity="medium", source="Old guide", passage="Old excerpt", status="active")
    other = main.Knowledge(organization_id=other_org.id, kind="rule", title="Keep", body="Keep this",
                           category="voice", severity="low", source="Other", passage="Keep", status="active")
    db.add_all([item, other]); db.flush()
    chunk = main.KnowledgeChunk(organization_id=org.id, knowledge_id=item.id, chunk_index=0, text="Old guidance")
    other_chunk = main.KnowledgeChunk(organization_id=other_org.id, knowledge_id=other.id, chunk_index=0, text="Keep this")
    db.add_all([chunk, other_chunk]); db.commit()
    updated = main.update_knowledge(item.id, main.KnowledgeUpdateIn(
        kind="fact", title="New title", body="New guidance", category="claims", severity="high",
        source="New source", passage="Exact new quote",
    ), admin, db)
    assert updated["status"] == "pending" and updated["version"] == 2
    assert updated["kind"] == "fact" and updated["source"] == "New source" and updated["passage"] == "Exact new quote"
    assert db.scalars(select(main.KnowledgeChunk).where(main.KnowledgeChunk.knowledge_id == item.id)).all() == []
    with pytest.raises(main.HTTPException) as cross_workspace:
        main.delete_knowledge(other.id, admin, db)
    assert cross_workspace.value.status_code == 404
    assert main.delete_knowledge(item.id, admin, db)["deleted"] is True
    assert db.get(main.Knowledge, item.id) is None
    assert db.get(main.Knowledge, other.id) is not None
    assert db.get(main.KnowledgeChunk, other_chunk.id) is not None


def test_only_admin_can_delete_decided_reviews_and_related_rows_are_removed(db_session):
    db = db_session
    org, admin = make_org(db, "Review deletion", "admin@review-delete.test")
    reviewer = main.User(organization_id=org.id, email="reviewer@review-delete.test", name="Reviewer", role="reviewer", password_hash="hash")
    member = main.User(organization_id=org.id, email="creator@review-delete.test", name="Creator", role="creator", password_hash="hash")
    db.add_all([reviewer, member]); db.commit()
    approved, finding, decision, version = add_review(db, org, admin, status="approved", title="Approved target")
    rejected, *_ = add_review(db, org, admin, status="rejected", title="Rejected target")
    unrelated, unrelated_finding, *_ = add_review(db, org, admin, status="approved", title="Keep this")

    for non_admin in (reviewer, member):
        with pytest.raises(main.HTTPException) as denied:
            main.delete_review(approved.id, non_admin, db)
        assert denied.value.status_code == 403
    with pytest.raises(main.HTTPException) as cross_workspace:
        other_org, other_admin = make_org(db, "Another", "admin@another-review.test")
        main.delete_review(approved.id, other_admin, db)
    assert cross_workspace.value.status_code == 404

    assert main.delete_review(approved.id, admin, db)["deleted"] is True
    assert db.get(main.Review, approved.id) is None
    assert db.get(main.Finding, finding.id) is None
    assert db.get(main.FindingDecision, decision.id) is None
    assert db.get(main.ReviewVersion, version.id) is None
    assert db.get(main.Review, rejected.id) is not None
    assert db.get(main.Review, unrelated.id) is not None
    assert db.get(main.Finding, unrelated_finding.id) is not None


def test_admin_only_dependency_rejects_reviewers_and_members():
    admin = main.User(id=1, organization_id=7, email="admin@test", name="Admin", role="admin", password_hash="x")
    assert main.roles("admin")(admin) is admin
    for role in ("reviewer", "creator", "manager"):
        user = main.User(id=2, organization_id=7, email=f"{role}@test", name=role, role=role, password_hash="x")
        with pytest.raises(main.HTTPException) as denied:
            main.roles("admin")(user)
        assert denied.value.status_code == 403
