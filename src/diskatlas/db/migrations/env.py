"""Alembic-Umgebung. Wird von Database.upgrade() mit bestehender Verbindung aufgerufen."""

from alembic import context
from sqlalchemy import create_engine

from diskatlas.db.models import Base

config = context.config
target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,  # nötig für ALTER TABLE unter SQLite
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


connection = config.attributes.get("connection")
if connection is not None:
    _run(connection)
else:
    url = config.get_main_option("sqlalchemy.url")
    if not url:
        raise RuntimeError("sqlalchemy.url ist nicht gesetzt")
    engine = create_engine(url)
    with engine.begin() as conn:
        _run(conn)
