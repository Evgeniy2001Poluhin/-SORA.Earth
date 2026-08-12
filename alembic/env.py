import os, sys
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Load .env if dotenv available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from app.database import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# DATABASE_URL from env, fallback to sqlite
db_url = os.getenv("DATABASE_URL", "sqlite:///data/sora.db")
config.set_main_option("sqlalchemy.url", db_url)
target_metadata = Base.metadata


def run_migrations_offline():
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run against a caller-supplied connection when one is given.

    Without this, a test cannot put a migration anywhere but the default
    search_path: Alembic builds its own engine from `sqlalchemy.url`, so a
    `search_path` set on the caller's connection has no effect, and the
    migration lands in `public` while the assertions inspect a private schema
    (#121). It also means a statement recorder attached to the caller's engine
    never sees a single migration statement -- the ordering assertions were
    inspecting a connection the migration did not use.

    The supplied connection is not closed here. It belongs to whoever opened
    it, and closing it would end their transaction and drop the search_path
    with it.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.", poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _run(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
