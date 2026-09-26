"""Datenbankzugriff: Engine, Sessions und Schema-Migrationen."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    kwargs: dict = {}
    if parsed.get_backend_name() == "sqlite":
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if parsed.database in (None, "", ":memory:"):
            kwargs["poolclass"] = StaticPool
        else:
            # Schlägt das fehl, meldet SQLite beim Verbinden einen verständlichen Fehler.
            with contextlib.suppress(OSError):
                Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()

    return engine


def alembic_config(connection=None):
    from alembic.config import Config as AlembicConfig

    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


class Database:
    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        self._sessionmaker = sessionmaker(self.engine, expire_on_commit=False)

    def upgrade(self) -> None:
        """Bringt das Schema per Alembic auf den neuesten Stand."""
        from alembic import command

        sqlite = self.engine.dialect.name == "sqlite"
        with self.engine.connect() as conn:
            if sqlite:
                # SQLite baut Tabellen für manche Änderungen neu auf (DROP + RENAME). Bei aktivem
                # Fremdschlüssel-Schutz würde das DROP per ON DELETE CASCADE die abhängigen Zeilen
                # (Volumes, Dateiindex, SMART-Verlauf, Labels) mitlöschen. Der PRAGMA wirkt nur
                # außerhalb einer Transaktion.
                conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
                conn.commit()
            try:
                with conn.begin():
                    command.upgrade(alembic_config(conn), "head")
            finally:
                if sqlite:
                    conn.exec_driver_sql("PRAGMA foreign_keys=ON")
                    conn.commit()
        from diskatlas.services.ingest import backfill_identity

        with self.session() as session:
            backfill_identity(session)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._sessionmaker()
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def new_session(self) -> Session:
        return self._sessionmaker()

    def dispose(self) -> None:
        self.engine.dispose()
