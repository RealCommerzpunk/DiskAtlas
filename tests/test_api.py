from conftest import make_disk

from diskatlas.services import ingest


def _seed(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:A", label="Filme"))
        ingest.upsert_disk(s, "pc", make_disk("sn:B", label="Fotos", smart_health="warning"))
        ingest.mark_connected(s, "pc", ["sn:A"])


def test_health(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"


def test_disks_filter_and_sort(client, db):
    _seed(db)
    names = [d["display_name"] for d in client.get("/api/v1/disks", params={"sort": "name"}).json()]
    assert names == ["Filme", "Fotos"]
    warn = client.get("/api/v1/disks", params={"health": "warning"}).json()
    assert [d["display_name"] for d in warn] == ["Fotos"]
    offline = client.get("/api/v1/disks", params={"connected": "no"}).json()
    assert [d["display_name"] for d in offline] == ["Fotos"]


def test_labels_and_patch(client, db):
    _seed(db)
    label = client.post("/api/v1/labels", json={"name": "Keller", "category": "Standort"}).json()
    assert client.post("/api/v1/labels", json={"name": "Keller"}).status_code == 409
    disk_id = client.get("/api/v1/disks").json()[0]["id"]
    r = client.put(f"/api/v1/disks/{disk_id}/labels", json={"label_ids": [label["id"]]})
    assert r.json()["labels"][0]["name"] == "Keller"
    body = {"custom_name": "Filmarchiv", "notes": "Regal 2"}
    r = client.patch(f"/api/v1/disks/{disk_id}", json=body)
    assert r.json()["display_name"] == "Filmarchiv"
    found = client.get("/api/v1/disks", params={"q": "regal keller"}).json()
    assert [d["id"] for d in found] == [disk_id]
    assert client.delete(f"/api/v1/labels/{label['id']}").status_code == 204
    assert client.get(f"/api/v1/disks/{disk_id}").json()["labels"] == []


def test_ingest_token(client, config):
    config.server.api_token = "geheim"
    payload = {"host": "x", "disk_keys": []}
    assert client.post("/api/v1/ingest/connected", json=payload).status_code == 401
    ok = client.post("/api/v1/ingest/connected", json=payload,
                     headers={"Authorization": "Bearer geheim"})
    assert ok.status_code == 200


def test_ingest_unknown_volume_404(client, db):
    _seed(db)
    r = client.post("/api/v1/ingest/index/begin", json={"disk_key": "sn:A", "volume_key": "nope"})
    assert r.status_code == 404


def test_html_pages_render(client, db):
    _seed(db)
    for url in ["/", "/?group=health", "/?group=label&sort=free&desc=true", "/?group=category",
                "/files", "/files?q=test", "/labels", "/disks/1", "/static/style.css"]:
        r = client.get(url)
        assert r.status_code == 200, url
    assert "Filme" in client.get("/").text
    assert client.get("/disks/999").status_code == 404


def test_html_forms(client, db):
    _seed(db)
    r = client.post("/labels", data={"name": "Backup", "category": "Zweck", "color": "#ff0000"},
                    follow_redirects=False)
    assert r.status_code == 303
    client.post("/disks/1/labels", data={"new_name": "Neu", "new_category": "Ort"})
    client.post("/disks/1", data={"custom_name": "Meine Platte", "notes": "Test"})
    page = client.get("/disks/1").text
    assert "Meine Platte" in page and "Neu" in page
    client.post("/disks/1/delete")
    assert client.get("/disks/1").status_code == 404
