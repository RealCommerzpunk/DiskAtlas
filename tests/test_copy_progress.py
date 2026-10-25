"""Fortschritt laufender Kopien und Relay-Belegung für den Admin (Roadmap M3)."""

from sqlalchemy import select
from test_copy_requests import (
    request_files,
    setup_anna,
    world,  # noqa: F401  (Fixture)
)

from diskatlas.db.models import CopyItem, CopyRequest


def test_running_item_shows_progress_and_page_refreshes(world):  # noqa: F811
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:film/b.mkv", f"f:{src}:doc.txt"], dst)
    with world.db.session() as s:
        item = s.scalar(select(CopyItem).where(CopyItem.source_path == "film/b.mkv"))
        item.state, item.bytes_done = "running", 500  # 2000 Bytes groß: 25 %
        rid = s.scalar(select(CopyRequest.id))
        s.commit()
    page = world.web["anna"].get(f"/requests/{rid}").text
    assert "25 %" in page and 'http-equiv="refresh"' in page
    overview = world.web["anna"].get("/requests").text
    assert 'class="bar"' in overview


def test_finished_request_does_not_refresh(world):  # noqa: F811
    src, dst = setup_anna(world)
    request_files(world, "anna", [f"f:{src}:doc.txt"], dst)
    with world.db.session() as s:
        s.scalar(select(CopyItem)).state = "done"
        rid = s.scalar(select(CopyRequest.id))
        s.commit()
    assert 'http-equiv="refresh"' not in world.web["anna"].get(f"/requests/{rid}").text


def test_admin_sees_relay_usage_but_nothing_inside(world):  # noqa: F811
    page = world.web["master"].get("/admin/users").text
    assert "Relay (Kopien zwischen Rechnern)" in page and "0 von 1" in page
    assert world.web["anna"].get("/admin/users").status_code == 403
