"""Das Dashboard merkt sich die zuletzt gewählten Filter."""

from conftest import make_disk

from diskatlas.services import ingest


def _disks(db):
    with db.session() as s:
        ingest.upsert_disk(s, "pc", make_disk("sn:ONLINE1", label="Eins"))
        ingest.upsert_disk(s, "pc", make_disk("sn:OFFLINE1", label="Zwei"))
        ingest.mark_connected(s, "pc", ["sn:ONLINE1"])


def test_filters_are_remembered_and_restored(client, db):
    _disks(db)
    first = client.get("/?q=&health=&connected=yes&group=none&sort=name")
    assert first.status_code == 200 and "OFFLINE1" not in first.text
    assert "connected=yes" in first.headers["set-cookie"]
    back = client.get("/", follow_redirects=False)  # z. B. nach dem Bearbeiten einer Platte
    assert back.status_code == 303 and back.headers["location"] == "/?connected=yes"
    assert "OFFLINE1" not in client.get("/").text and "Filter zurücksetzen" in client.get("/").text


def test_reset_and_clearing_forget_the_filters(client, db):
    _disks(db)
    client.get("/?connected=yes")
    reset = client.get("/?reset=1", follow_redirects=False)
    assert reset.status_code == 303 and 'diskatlas_dashboard=""' in reset.headers["set-cookie"]
    page = client.get("/")
    assert "OFFLINE1" in page.text and "ONLINE1" in page.text
    client.get("/?connected=no")
    cleared = client.get("/?q=&connected=&group=none&sort=name")  # alles von Hand geleert
    assert "OFFLINE1" in cleared.text and "ONLINE1" in cleared.text
    assert client.get("/", follow_redirects=False).status_code == 200


def test_saved_cookie_only_restores_known_filters(client, db):
    _disks(db)
    client.cookies.set("diskatlas_dashboard", "connected=yes&next=https://evil.example&x=1")
    back = client.get("/", follow_redirects=False)
    assert back.headers["location"] == "/?connected=yes"
