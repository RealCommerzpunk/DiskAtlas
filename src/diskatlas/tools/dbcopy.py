"""Kopiert den Datenbestand in eine andere Datenbank (z. B. SQLite -> PostgreSQL auf dem Unraid)."""

from __future__ import annotations

import logging

from sqlalchemy import func, insert, select, text

from diskatlas.db import Database
from diskatlas.db.models import Base

log = logging.getLogger(__name__)
BATCH = 5000


def copy_database(source_url: str, target_url: str) -> dict[str, int]:
    source, target = Database(source_url), Database(target_url)
    source.upgrade()
    target.upgrade()
    tables = Base.metadata.sorted_tables
    counts: dict[str, int] = {}
    try:
        with source.engine.connect() as src, target.engine.begin() as dst:
            for table in tables:
                if dst.execute(select(func.count()).select_from(table)).scalar():
                    raise RuntimeError(f"Ziel-Datenbank ist nicht leer (Tabelle {table.name})")
            for table in tables:
                n = 0
                batch: list[dict] = []
                result = src.execution_options(yield_per=BATCH).execute(select(table))
                for row in result.mappings():
                    batch.append(dict(row))
                    if len(batch) >= BATCH:
                        dst.execute(insert(table), batch)
                        n += len(batch)
                        batch = []
                if batch:
                    dst.execute(insert(table), batch)
                    n += len(batch)
                counts[table.name] = n
                log.info("%s: %d Zeilen kopiert", table.name, n)
            if dst.dialect.name == "postgresql":
                _reset_sequences(dst, tables)
    finally:
        source.dispose()
        target.dispose()
    return counts


def _reset_sequences(conn, tables) -> None:
    for table in tables:
        if "id" not in table.c:
            continue
        conn.execute(
            text(
                f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                f"COALESCE((SELECT MAX(id) FROM {table.name}), 0) + 1, false)"
            )
        )
