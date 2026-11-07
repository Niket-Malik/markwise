import importlib.util
import io
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


MIGRATIONS = Path(__file__).parents[1] / "migrations" / "versions"


def load_migration(filename):
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), MIGRATIONS / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_all_migration_revision_ids_fit_alembic_version_and_form_one_chain():
    migrations = [load_migration(path.name) for path in sorted(MIGRATIONS.glob("*.py"))]
    revisions = [migration.revision for migration in migrations]
    revision_set = set(revisions)

    assert all(len(revision) <= 32 for revision in revisions)
    assert len(revision_set) == len(revisions)
    assert all(
        migration.down_revision is None or migration.down_revision in revision_set
        for migration in migrations
    )
    assert sum(migration.down_revision is None for migration in migrations) == 1
    assert sum(
        migration.revision not in {item.down_revision for item in migrations}
        for migration in migrations
    ) == 1

    migration_0007 = load_migration("0007_openrouter_embedding_space.py")
    migration_0008 = load_migration("0008_workspace_consent_generation.py")
    assert migration_0007.revision == "0007_openrouter_embedding_space"
    assert migration_0007.down_revision == "0006_review_visual_assets"
    assert migration_0008.revision == "0008_consent_generation"
    assert migration_0008.down_revision == migration_0007.revision


def test_0007_upgrade_and_downgrade_generate_postgresql_sql_in_safe_order():
    migration = load_migration("0007_openrouter_embedding_space.py")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    upgrade_sql = output.getvalue().lower()
    assert "add column openrouter_free_ai_enabled boolean default false not null" in upgrade_sql
    assert "add column embedding_space varchar(200)" in upgrade_sql

    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.downgrade()
    downgrade_sql = output.getvalue().lower()
    clear_at = downgrade_sql.index("update knowledge_chunks set embedding = null")
    drop_space_at = downgrade_sql.index("drop column embedding_space")
    drop_consent_at = downgrade_sql.index("drop column openrouter_free_ai_enabled")
    assert clear_at < drop_space_at < drop_consent_at
    assert "drop table knowledge_chunks" not in downgrade_sql


def test_0008_consent_generation_upgrade_and_downgrade_sql():
    migration = load_migration("0008_workspace_consent_generation.py")
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.upgrade()
    upgrade_sql = output.getvalue().lower()
    assert "add column external_ai_consent_version integer default '0' not null" in upgrade_sql
    assert "add column openrouter_consent_version integer default '0' not null" in upgrade_sql

    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration.downgrade()
    downgrade_sql = output.getvalue().lower()
    assert "drop column openrouter_consent_version" in downgrade_sql
    assert "drop column external_ai_consent_version" in downgrade_sql


def test_downgrade_clears_only_rebuildable_vectors_and_preserves_knowledge_text():
    migration_0007 = load_migration("0007_openrouter_embedding_space.py")
    migration_0008 = load_migration("0008_workspace_consent_generation.py")
    engine = create_engine("sqlite://")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE organizations (id INTEGER PRIMARY KEY, external_ai_consent_version INTEGER NOT NULL, openrouter_free_ai_enabled BOOLEAN NOT NULL, openrouter_consent_version INTEGER NOT NULL)"))
        connection.execute(text("CREATE TABLE knowledge_chunks (id INTEGER PRIMARY KEY, text TEXT NOT NULL, embedding TEXT, embedding_space VARCHAR(200))"))
        connection.execute(text("INSERT INTO organizations VALUES (1, 2, 1, 4)"))
        connection.execute(text("INSERT INTO knowledge_chunks VALUES (1, 'approved source passage', '[1, 0]', 'openrouter:nemotron:1536')"))
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            migration_0008.downgrade()
            migration_0007.downgrade()

        row = connection.execute(text("SELECT text, embedding FROM knowledge_chunks WHERE id=1")).one()
        assert row.text == "approved source passage"
        assert row.embedding is None
        assert "embedding_space" not in {column["name"] for column in inspect(connection).get_columns("knowledge_chunks")}
        assert "openrouter_free_ai_enabled" not in {column["name"] for column in inspect(connection).get_columns("organizations")}
        assert "openrouter_consent_version" not in {column["name"] for column in inspect(connection).get_columns("organizations")}
        assert "external_ai_consent_version" not in {column["name"] for column in inspect(connection).get_columns("organizations")}
    engine.dispose()
