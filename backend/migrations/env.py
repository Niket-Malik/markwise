from logging.config import fileConfig
import os
import sys

from alembic import context
from sqlalchemy import create_engine, pool
from pgvector.sqlalchemy import Vector

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.main import Base, normalize_database_url, settings  # noqa: E402

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
database_url = normalize_database_url(settings.database_url)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
target_metadata = Base.metadata


def compare_type(migration_context, inspected_column, metadata_column, inspected_type, metadata_type):
    # SQLite has no pgvector type; it reports VECTOR(1536) as NUMERIC during local migration checks.
    if migration_context.dialect.name == "sqlite" and isinstance(metadata_type, Vector):
        return False
    return None


def run_migrations_offline():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"}, compare_type=compare_type)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    connect_args = {"prepare_threshold": None} if database_url.startswith("postgresql+") else {}
    connectable = create_engine(database_url, poolclass=pool.NullPool, connect_args=connect_args)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=compare_type)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
