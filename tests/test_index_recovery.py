"""Hängende Indizierung: abgebrochene Läufe erkennen, Reste aufräumen, Agent versucht es erneut."""

import threading
from datetime import timedelta

from conftest import make_disk
from sqlalchemy import func, select

from diskatlas.agent import agent as agent_module
from diskatlas.agent.agent import Agent
from diskatlas.agent.sinks import DatabaseSink
from diskatlas.config import AgentConfig
from diskatlas.db.models import FileEntry, Volume
from diskatlas.probe.types import SmartInfo
from diskatlas.services import ingest, queries
from diskatlas.services.ingest import utcnow

KEY, VOL = "sn:TEST1", "part:sn:TEST1-1"


def _files(n, prefix="f"):
    return [(f"{prefix}{i}.txt", 1, 1_700_000_000.0) for i in range(n)]


def _volume(s):
    return s.scalar(select(Volume))


def _count(s, **where):
    stmt = select(func.count(FileEntry.id))
    for key, value in where.items():
        stmt = stmt.where(getattr(FileEntry, key) == value)
    return s.scalar(stmt)


def _setup(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk(KEY))
        scan = ingest.begin_index(s, KEY, VOL)
        ingest.add_files(s, KEY, VOL, scan, _files(10))
        ingest.finish_index(s, KEY, VOL, scan)  # gültiger Index mit 10 Dateien


def test_index_without_progress_expires_and_the_banner_disappears(db):
    _setup(db)
    with db.session() as s:
        scan = ingest.begin_index(s, KEY, VOL)
        ingest.add_files(s, KEY, VOL, scan, _files(3, "neu"))
        ingest.mark_connected(s, "pc", [KEY])
    with db.session() as s:
        (running,) = queries.running_indexes(s)
        assert running.files_so_far == 3, "zählt nur den laufenden Durchlauf"
    later = utcnow() + ingest.INDEX_STALE + timedelta(minutes=1)
    with db.session() as s:
        ingest.mark_connected(s, "pc", [KEY], now=later)  # Heartbeat nach langer Stille
    with db.session() as s:
        assert _volume(s).index_status == "failed"
        assert queries.running_indexes(s) == []
        assert _volume(s).file_count == 10, "der alte Index bleibt gültig"


def test_old_running_state_from_earlier_versions_expires(db):
    """Wie beim Betreiber: seit Wochen „läuft“, ohne Zeitstempel aus älterer Version."""
    _setup(db)
    with db.session() as s:
        volume = _volume(s)
        volume.index_status, volume.index_touched_at = "running", None
    with db.session() as s:
        assert queries.running_indexes(s) == [], "Banner verschwindet sofort"
        ingest.mark_connected(s, "pc", [KEY])
    with db.session() as s:
        assert _volume(s).index_status == "failed"


def test_leftovers_are_purged_in_the_background_in_small_steps(db, monkeypatch):
    from diskatlas.services import maintenance

    _setup(db)
    monkeypatch.setattr(maintenance, "BATCH", 7)  # in mehreren kurzen Transaktionen
    with db.session() as s:
        for attempt in range(3):  # drei abgebrochene Läufe ohne Abschluss
            scan = ingest.begin_index(s, KEY, VOL)
            ingest.add_files(s, KEY, VOL, scan, _files(20, f"a{attempt}-"))
        assert _count(s) == 10 + 60, "begin_index löscht nichts (keine lange Sperre)"
    maintenance.run_once(db, pause=0)
    with db.session() as s:
        assert _count(s) == 10 + 20, "nur aktiver Index und der laufende Versuch bleiben"
        ingest.add_files(s, KEY, VOL, _volume(s).pending_scan_id, _files(5, "ok"))
        ingest.finish_index(s, KEY, VOL, _volume(s).pending_scan_id)
        assert _volume(s).file_count == 25 and _volume(s).index_status == "done"
    maintenance.run_once(db, pause=0)
    with db.session() as s:
        assert _count(s) == 25 and _volume(s).purge_pending is False


def test_finish_does_not_delete_the_old_index_itself(db):
    """Der Abschluss schaltet nur um; 846 000 alte Zeilen zu löschen dauerte auf Unraid so lange,
    dass andere Anfragen „database is locked“ bekamen."""
    _setup(db)
    with db.session() as s:
        scan = ingest.begin_index(s, KEY, VOL)
        ingest.add_files(s, KEY, VOL, scan, _files(3, "neu"))
        ingest.finish_index(s, KEY, VOL, scan)
        assert _count(s) == 13 and _volume(s).file_count == 3
    with db.session() as s:
        rows, total = queries.search_files(s, queries.FileQuery(q=".txt"))
        assert total == 3, "die Suche sieht sofort nur den neuen Index"


def test_last_seen_update_never_fails_a_request(db, monkeypatch):
    from sqlalchemy.exc import OperationalError

    from diskatlas.db.models import Client, User
    from diskatlas.services import users
    from diskatlas.web import security

    with db.session() as s:
        users.bootstrap_master(s, "geheimgeheim")
        admin = s.scalar(select(User))
        _, token = users.create_client(s, admin, "pc")
    with db.session() as s:
        real = s.commit

        def locked():
            raise OperationalError("UPDATE clients", {}, Exception("database is locked"))

        monkeypatch.setattr(s, "commit", locked)
        client = users.find_client_by_token(s, token)
        assert client is not None and client.token_hash == security.hash_token(token)
        monkeypatch.setattr(s, "commit", real)
    assert Client


def test_late_batch_revives_an_expired_run(db):
    _setup(db)
    with db.session() as s:
        scan = ingest.begin_index(s, KEY, VOL)
        _volume(s).index_status = "failed"  # zwischendurch als abgebrochen markiert
        ingest.add_files(s, KEY, VOL, scan, _files(2))
        assert _volume(s).index_status == "running"
        ingest.finish_index(s, KEY, VOL, scan)
        assert _volume(s).index_status == "done" and _volume(s).file_count == 2


def test_agent_retries_a_failed_index_soon(db, tmp_path, monkeypatch):
    monkeypatch.setattr(agent_module, "RETRY_FIRST", 0.2)
    root = tmp_path / "mnt"
    root.mkdir()
    (root / "a.txt").write_text("x")
    done = threading.Event()
    calls = {"n": 0}

    class FlakySink(DatabaseSink):
        def begin_index(self, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("Server kurz weg")
            return super().begin_index(*args, **kwargs)

        def finish_index(self, *args, **kwargs):
            super().finish_index(*args, **kwargs)
            done.set()

    agent = Agent(AgentConfig(poll_interval=0.05, rescan_interval_hours=24), FlakySink(db),
                  prober=lambda: [make_disk(mountpoint=str(root))],
                  smart_reader=lambda d: SmartInfo(available=True, health="ok"), host="h")
    thread = threading.Thread(target=agent.watch, daemon=True)
    thread.start()
    try:
        assert done.wait(10), "kein zweiter Versuch – erst nach 24 h?"
    finally:
        agent.stop()
        thread.join(5)
    assert calls["n"] >= 2
    with db.session() as s:
        assert _volume(s).file_count == 1


def test_running_index_shows_as_compact_pill_in_the_top_bar(client, db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk(KEY))
        scan = ingest.begin_index(s, KEY, VOL)
        ingest.add_files(s, KEY, VOL, scan, _files(1234))
    html = client.get("/").text
    assert 'class="activity-pill"' in html and "Indiziert:" in html and "1.234" in html
    assert "nicht abziehen" in html, "Hinweis bleibt im Tooltip"
    assert 'class="activity"' not in html, "kein breites Banner mehr über dem Inhalt"
    with db.session() as s:
        ingest.finish_index(s, KEY, VOL, scan)
    html = client.get("/").text
    assert "Indiziert:" not in html, "versteckt, wenn nichts läuft"


def test_admin_sees_recent_server_errors(db, tmp_path):
    import logging

    from test_authz import World

    world = World(db, tmp_path)
    try:
        raise RuntimeError("database is locked")
    except RuntimeError:
        logging.getLogger("diskatlas.test").exception("Fehler beim Speichern")
    page = world.web["master"].get("/admin/users").text
    assert "Server-Meldungen" in page and "Fehler beim Speichern" in page
    assert "database is locked" in page
    assert world.web["anna"].get("/admin/users").status_code == 403
