"""Owner and viewer: a viewer (her partner) sees only the shared jars, read only."""

import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app

OWNER = {"Tailscale-User-Login": "Owner@Example.com"}
PARTNER = {"Tailscale-User-Login": "partner@example.com"}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("OWNER_LOGIN", raising=False)
    db.init_db()
    with db.connection() as conn:
        conn.execute("INSERT INTO jars (name, currency, target, quote, shared) VALUES ('Trip together', 'UAH', 100000, 'Go!', 1)")
        conn.execute("INSERT INTO jars (name, currency, opening, shared) VALUES ('My secret jar', 'UAH', 5000, 0)")
    return TestClient(app)


def restrict(monkeypatch):
    monkeypatch.setenv("OWNER_LOGIN", "owner@example.com")


def test_nothing_is_restricted_until_owner_login_is_set(client):
    assert client.get("/", headers=PARTNER).status_code == 200
    assert "My secret jar" in client.get("/jars", headers=PARTNER).text


def test_owner_sees_everything(client, monkeypatch):
    restrict(monkeypatch)
    for path in ("/", "/transactions", "/settings", "/analytics"):
        assert client.get(path, headers=OWNER).status_code == 200  # the login matches in any letter case
    page = client.get("/jars", headers=OWNER).text
    assert "My secret jar" in page and "Trip together" in page and "New jar" in page


def test_server_itself_is_the_owner(client, monkeypatch):
    restrict(monkeypatch)  # no account, not through the proxy: opened on the server (update.sh checks this way)
    assert client.get("/").status_code == 200


def test_viewer_sees_only_shared_jars_read_only(client, monkeypatch):
    restrict(monkeypatch)
    page = client.get("/jars", headers=PARTNER).text
    assert "Trip together" in page and "Go!" in page
    assert "My secret jar" not in page
    for hidden in ("New jar", "Edit", "Add money", "Withdraw", "Archived", "/settings", "/transactions"):
        assert hidden not in page
    assert client.get("/", headers=PARTNER, follow_redirects=False).headers["location"] == "/jars"


def test_viewer_is_shut_out_of_everything_else(client, monkeypatch):
    restrict(monkeypatch)
    for path in ("/transactions", "/settings", "/analytics", "/year", "/add", "/unsorted", "/jars/new", "/jars/1/edit"):
        assert client.get(path, headers=PARTNER).status_code == 403, path
    for path in ("/jars/new", "/jars/1/archive", "/add", "/settings/quotes/add", "/transactions/1/delete"):
        assert client.post(path, headers={**PARTNER, "Origin": "http://testserver"}, data={}).status_code == 403, path
    assert client.get("/static/style.css", headers=PARTNER).status_code == 200
    assert client.get("/manifest.webmanifest", headers=PARTNER).status_code == 200
    with db.connection() as conn:  # nothing was changed
        assert conn.execute("SELECT archived FROM jars WHERE id = 1").fetchone()[0] == 0


def test_proxied_request_without_an_account_is_a_viewer(client, monkeypatch):
    restrict(monkeypatch)
    assert client.get("/transactions", headers={"X-Forwarded-For": "100.64.0.9"}).status_code == 403


def test_whoami_tells_who_the_site_sees(client, monkeypatch):
    page = client.get("/whoami", headers=OWNER).text
    assert "Owner@Example.com" in page and "off (everyone is the owner)" in page and "OWNER_LOGIN=Owner@Example.com" in page
    restrict(monkeypatch)
    assert "a viewer" in client.get("/whoami", headers=PARTNER).text
    assert "the owner" in client.get("/whoami", headers=OWNER).text


def test_shared_checkbox_is_saved(client, monkeypatch):
    restrict(monkeypatch)
    client.post("/jars/new", headers=OWNER, data={"name": "Flat", "currency": "UAH", "shared": "1"})
    client.post("/jars/new", headers=OWNER, data={"name": "Mine", "currency": "UAH"})
    with db.connection() as conn:
        got = {r["name"]: r["shared"] for r in conn.execute("SELECT name, shared FROM jars")}
    assert got["Flat"] == 1 and got["Mine"] == 0
    assert "checked" in client.get("/jars/1/edit", headers=OWNER).text
    seen = client.get("/jars", headers=PARTNER).text
    assert "Flat" in seen and "Mine" not in seen


def test_a_double_slash_in_the_address_is_fixed(client, monkeypatch):
    base = "http://testserver"  # a bare "//whoami" would be read as a host name
    fixed = client.get(f"{base}//whoami", follow_redirects=False)
    assert fixed.status_code == 307 and fixed.headers["location"] == "/whoami"
    assert client.get(f"{base}//whoami").status_code == 200
    restrict(monkeypatch)
    assert client.get(f"{base}//jars", headers=PARTNER, follow_redirects=False).headers["location"] == "/jars"
    assert client.get(f"{base}//transactions", headers=PARTNER, follow_redirects=False).status_code == 307  # fixed first ...
    assert client.get(f"{base}//transactions", headers=PARTNER).status_code == 403  # ... and still closed afterwards
