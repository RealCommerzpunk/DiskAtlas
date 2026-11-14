from conftest import make_disk
from sqlalchemy import select

from diskatlas.db.models import Disk
from diskatlas.services import bays, hosts, ingest, lookup


def _add(db, key, serial, model="WDC WD40EFRX-68WT0N0", wwn=None):
    info = make_disk(key)
    info.serial, info.model, info.wwn = serial, model, wwn
    with db.session() as s:
        ingest.upsert_disk(s, "pc", info)


def test_normalize_and_matching(db):
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ")
    _add(db, "sn:B", "WRQ2CJ1L", wwn="wwn-0x5000c500ABCDEF12")
    _add(db, "sn:C", "Z1E4YE1Q")
    assert lookup.normalize(" wd-wcc4e2vrr0cj\n") == "WDWCC4E2VRR0CJ"
    with db.session() as s:
        names = lambda code: sorted(d.serial for d in lookup.find_disks(s, code))  # noqa: E731
        assert names("WD-WCC4E2VRR0CJ") == ["WD-WCC4E2VRR0CJ"]
        assert names("wdwcc4e2vrr0cj") == ["WD-WCC4E2VRR0CJ"], "Trennzeichen und Schreibweise egal"
        assert names("S/N: WRQ2CJ1L P/N 1234") == ["WRQ2CJ1L"], "Serie im Etikettentext"
        assert names("WRQ2CJ1LB") == ["WRQ2CJ1L"], "Code39 mit angehängter Prüfziffer"
        assert names("0x5000c500abcdef12") == ["WRQ2CJ1L"], "auch über die WWN"
        assert names("VRR0") == ["WD-WCC4E2VRR0CJ"], "getippter Teil"
        assert names("XYZ") == [] and names("") == [], "zu kurz / unbekannt"
        assert names("ZZZZZZZZ") == []


def test_exact_match_beats_partial(db):
    _add(db, "sn:A", "ABCD1234")
    _add(db, "sn:B", "ABCD12345678")
    with db.session() as s:
        assert [d.serial for d in lookup.find_disks(s, "ABCD1234")] == ["ABCD1234"]


def test_whereabouts_bay_connected_offline(db):
    _add(db, "sn:A", "SERIAL01")
    with db.session() as s:
        hosts.record(s, "pc", {"all_ports": ["ata3", "ata4"], "present": [
            {"port": "ata4", "device": "/dev/sdb", "serial": "SERIAL01", "disk_key": "sn:A"}]})
        bays.save_config(s, bays.BayConfig(host="pc", ports=["ata3", "ata4", None, None]))
    with db.session() as s:
        disk = s.scalar(select(Disk))
        where = lookup.whereabouts(s, disk)
        assert (where.state, where.bay, where.host) == ("bay", 2, "pc")
        bays.save_config(s, bays.BayConfig(host="pc", ports=["ata3", None, None, None]))
    with db.session() as s:
        assert lookup.whereabouts(s, s.scalar(select(Disk))).state == "connected"
        ingest.mark_connected(s, "pc", [])
    with db.session() as s:
        disk = s.scalar(select(Disk))
        disk.location = "Karton 3"
    with db.session() as s:
        where = lookup.whereabouts(s, s.scalar(select(Disk)))
        assert (where.state, where.location) == ("offline", "Karton 3")


def test_lookup_api_location_patch_and_pwa_routes(client, db):
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ")
    found = client.get("/api/v1/lookup", params={"code": "WD-WCC4E2VRR0CJ"}).json()
    assert len(found) == 1 and found[0]["serial"] == "WD-WCC4E2VRR0CJ"
    assert found[0]["brand"] == "WD Red" and found[0]["state"] == "connected"
    assert client.get("/api/v1/lookup", params={"code": "NICHTDA123"}).json() == []

    disk_id = found[0]["id"]
    patched = client.patch(f"/api/v1/disks/{disk_id}", json={"location": "  Karton 3, Regal B "})
    assert patched.status_code == 200 and patched.json()["location"] == "Karton 3, Regal B"
    assert client.patch(f"/api/v1/disks/{disk_id}", json={"location": "x" * 501}).status_code == 422
    client.post(f"/disks/{disk_id}", data={"custom_name": "", "notes": "", "location": "Schrank"})
    assert client.get("/api/v1/lookup", params={"code": "WD-WCC4E2VRR0CJ"}).json()[0][
        "location"] == "Schrank"

    page = client.get("/scan")
    assert page.status_code == 200 and 'id="video"' in page.text and "zxing.min.js" in page.text
    manifest = client.get("/manifest.webmanifest")
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json()["start_url"] == "/scan" and manifest.json()["display"] == "standalone"
    assert 'rel="manifest"' in client.get("/").text
    for asset in ("vendor/zxing.min.js", "scan.js", "scan.css", "icon-192.png", "icon-512.png"):
        assert client.get(f"/static/{asset}").status_code == 200, asset


def test_iphone_app_is_a_fullscreen_page_with_both_modes(client, db):
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ")
    page = client.get("/scan").text
    assert 'class="topbar"' not in page, "Vollbild-App ohne Navigation der Weboberfläche"
    assert 'data-mode="info"' in page and 'data-mode="find"' in page
    assert 'apple-mobile-web-app-capable' in page and 'viewport-fit=cover' in page
    find = client.get("/scan?mode=find&disk=7").text
    assert '<body class="app" data-mode="find" data-target="7"' in find
    odd = client.get("/scan?mode=quatsch&disk=abc").text
    assert '<body class="app" data-mode="info" data-target=""' in odd


def test_scan_index_has_what_the_app_needs(client, db):
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ", wwn="wwn-0x5000c500abcdef12")
    data = client.get("/api/v1/scan/index").json()
    (disk,) = data["disks"]
    assert {"id", "name", "serial", "wwn", "labels", "location", "state", "health"} <= set(disk)
    assert disk["serial"] == "WD-WCC4E2VRR0CJ" and disk["wwn"] == "wwn-0x5000c500abcdef12"
    assert data["generated_at"]


def test_manifest_and_service_worker(client):
    manifest = client.get("/manifest.webmanifest").json()
    assert manifest["orientation"] == "portrait"
    assert [s["url"] for s in manifest["shortcuts"]] == ["/scan?mode=info", "/scan?mode=find"]
    sw = client.get("/sw.js")
    assert sw.status_code == 200 and sw.headers["content-type"].startswith("application/javascript")
    assert sw.headers["service-worker-allowed"] == "/" and "/static/scan.js" in sw.text
    assert "caches" in sw.text


def test_disk_page_links_to_the_search_mode(client, db):
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ")
    with db.session() as s:
        disk_id = s.scalar(select(Disk.id))
    assert f"/scan?mode=find&amp;disk={disk_id}" in client.get(f"/disks/{disk_id}").text


def test_scan_index_and_service_worker_with_login(db, tmp_path):
    from test_authz import World

    world = World(db, tmp_path)
    world.ingest_disk("anna", "sn:A1", "pc")
    anna = world.web["anna"].get("/api/v1/scan/index").json()["disks"]
    assert [d["serial"] for d in anna] == ["A1"]
    assert world.web["bob"].get("/api/v1/scan/index").json()["disks"] == []
    from fastapi.testclient import TestClient

    anonymous = TestClient(world.app, follow_redirects=False)
    assert anonymous.get("/api/v1/scan/index").status_code == 401
    assert anonymous.get("/sw.js").status_code == 200, "Service Worker ohne Anmeldung ladbar"
    assert anonymous.get("/scan").status_code == 303, "die App selbst nur mit Anmeldung"


def test_assets_use_relative_paths_behind_https_proxies(client, db):
    """Hinter einem HTTPS-Proxy (tailscale serve, SWAG) sieht der Container nur HTTP. Absolute
    Adressen würden dann als http://… ausgeliefert und vom Browser blockiert (Seite ohne CSS/JS)."""
    _add(db, "sn:A", "WD-WCC4E2VRR0CJ")
    for path in ("/scan", "/", "/login"):
        html = client.get(path, headers={"host": "unraid.tail1234.ts.net"}).text
        assert "http://" not in html.split("<body")[0], path
        assert "/static/" in html, path
    page = client.get("/scan").text
    assert 'href="/static/scan.css?v=' in page and 'src="/static/scan.js?v=' in page


def test_wasm_barcode_reader_is_served_locally(client):
    page = client.get("/scan").text
    assert page.index("barcode-detector.js") < page.index("scan.js"), "Leser vor der App laden"
    wasm = client.get("/static/vendor/zxing_reader.wasm")
    assert wasm.status_code == 200 and wasm.headers["content-type"] == "application/wasm"
    assert wasm.content[:4] == b"\0asm"
    assert client.get("/static/vendor/barcode-detector.js").status_code == 200
    assert "zxing_reader.wasm" in client.get("/sw.js").text, "für schnellen Start vorgehalten"
