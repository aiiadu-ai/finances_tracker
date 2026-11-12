from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, jars
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def cat(name, kind="expense"):
    with db.connection() as conn:
        return conn.execute("SELECT id FROM categories WHERE kind = ? AND name = ?", (kind, name)).fetchone()["id"]


def make_jar(client, name="Trip", currency="UAH", **extra):
    data = {"name": name, "currency": currency, "target": "", "rate": "", "opening": "", "quote": "", **extra}
    response = client.post("/jars/new", data=data)
    assert response.status_code == 303, response.text
    with db.connection() as conn:
        return conn.execute("SELECT id FROM jars WHERE name = ?", (name,)).fetchone()["id"]


def move(client, jar_id, amount, direction="in", day="2026-11-05", paid="", comment=""):
    return client.post(
        "/add",
        data={"kind": "jar", "jar_id": str(jar_id), "direction": direction, "amount": amount, "currency": "UAH",
              "date": day, "paid": paid, "comment": comment},
    )


def balance(jar_id):
    with db.connection() as conn:
        return jars.get_jar(conn, jar_id)["balance"]


def flows(first=date(2026, 11, 1), following=date(2026, 12, 1)):
    with db.connection() as conn:
        return jars.month_flows(conn, first, following)


def test_starting_quotes_are_added_once(client):
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 10
        conn.execute("DELETE FROM quotes")
    db.init_db()  # a restart must not bring deleted quotes back
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotes").fetchone()[0] == 0


def test_old_database_gets_the_new_columns(tmp_path, monkeypatch):
    import sqlite3

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE categories (id INTEGER PRIMARY KEY, kind TEXT, name TEXT, "group" TEXT, archived INTEGER DEFAULT 0);
        CREATE TABLE transactions (id INTEGER PRIMARY KEY, date TEXT, kind TEXT, category_id INTEGER, amount INTEGER,
            currency TEXT, comment TEXT DEFAULT '', source TEXT DEFAULT 'site', external_id TEXT, jar_id INTEGER);
        """
    )
    conn.close()
    monkeypatch.setenv("FINANCE_DB", str(path))
    db.init_db()
    with db.connection() as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(transactions)")}
    assert {"direction", "paid_uah"} <= columns


def test_create_jar_with_opening_balance_and_validation(client):
    jar = make_jar(client, "Trip", "UAH", target="50000", opening="1200", quote="Go.")
    assert balance(jar) == 120000
    bad = client.post("/jars/new", data={"name": "trip", "currency": "UAH", "target": "x"})
    assert bad.status_code == 422 and "already a jar" in bad.text and "Enter the goal" in bad.text
    assert client.post("/jars/new", data={"name": "", "currency": "UAH"}).status_code == 422


def test_deposit_and_withdraw_move_the_balance_but_are_not_income_or_expense(client):
    jar = make_jar(client, opening="100")
    assert move(client, jar, "250").status_code == 303
    assert move(client, jar, "50", "out").status_code == 303
    assert balance(jar) == 10000 + 25000 - 5000
    assert flows()["UAH"] == 20000
    page = client.get("/").text
    assert "In jars" in page and "Free" in page
    with db.connection() as conn:  # nothing leaked into income / expense totals
        assert conn.execute("SELECT COUNT(*) FROM transactions WHERE kind IN ('income','expense')").fetchone()[0] == 0


def test_free_is_balance_minus_in_jars_per_currency(client):
    jar = make_jar(client)
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES ('2026-11-01','income',?,1000000,'UAH')",
            (cat("Salary", "income"),),
        )
    move(client, jar, "300")
    assert flows()["UAH"] == 30000 and flows()["EUR"] == 0


def test_cannot_withdraw_more_than_the_jar_holds(client):
    jar = make_jar(client)
    move(client, jar, "100")
    response = move(client, jar, "100.01", "out")
    assert response.status_code == 422 and "not that much" in response.text
    assert balance(jar) == 10000  # the failed withdrawal was undone


def test_euro_deposit_paid_in_hryvnias_takes_free_hryvnias(client):
    jar = make_jar(client, "Rainy day", "EUR", rate="43.5")
    assert move(client, jar, "100", paid="4200").status_code == 303
    assert balance(jar) == 10000
    assert flows() == {"UAH": 420000, "EUR": 0}  # free ₴ drop, free € untouched
    assert move(client, jar, "20").status_code == 303  # no "paid": plain euros
    assert flows() == {"UAH": 420000, "EUR": 2000}
    with db.connection() as conn:
        assert jars.get_jar(conn, jar)["uah_value"] == 12000 * 4350 // 100
    assert "≈" in client.get("/jars").text


def test_paid_in_only_for_deposits_to_euro_jars(client):
    uah, eur = make_jar(client, "A", "UAH"), make_jar(client, "B", "EUR")
    assert move(client, uah, "10", paid="100").status_code == 422
    assert move(client, eur, "10", "out", paid="100").status_code == 422
    assert move(client, eur, "10", paid="abc").status_code == 422


def test_deleting_or_editing_a_deposit_cannot_leave_a_jar_negative(client):
    jar = make_jar(client)
    move(client, jar, "100")
    move(client, jar, "80", "out")
    with db.connection() as conn:
        deposit = conn.execute("SELECT id FROM transactions WHERE direction = 'in'").fetchone()["id"]
    blocked = client.post(f"/transactions/{deposit}/delete")
    assert blocked.status_code == 303 and "blocked=" in blocked.headers["location"]
    assert balance(jar) == 2000
    smaller = client.post(
        f"/transactions/{deposit}/edit",
        data={"kind": "jar", "jar_id": str(jar), "direction": "in", "amount": "50", "currency": "UAH", "date": "2026-11-05"},
    )
    assert smaller.status_code == 422


def test_edit_transfer_form_is_prefilled_and_listed_with_its_jar(client):
    jar = make_jar(client, "Trip")
    move(client, jar, "75.5", comment="gift")
    with db.connection() as conn:
        tx = conn.execute("SELECT id FROM transactions").fetchone()["id"]
    page = client.get(f"/transactions/{tx}/edit").text
    assert 'id="kind-jar" value="jar" checked' in page.replace("\n", " ").replace("  ", " ") or 'value="jar" checked' in page
    assert "To Trip" in client.get("/transactions").text
    assert "To Trip" in client.get(f"/transactions?tx={tx}").text


def test_add_page_prefills_jar_and_shows_a_quote_after_deposit(client):
    jar = make_jar(client, "Trip", quote="My own words.")
    page = client.get(f"/add?kind=jar&jar={jar}&dir=out").text
    assert f'value="{jar}"' in page
    move(client, jar, "10")
    with db.connection() as conn:
        tx = conn.execute("SELECT id FROM transactions").fetchone()["id"]
    assert "My own words." in client.get(f"/add?saved={tx}").text
    plain = make_jar(client, "Other")
    move(client, plain, "10")
    with db.connection() as conn:
        tx = conn.execute("SELECT id FROM transactions WHERE jar_id = ?", (plain,)).fetchone()["id"]
    assert "quote" in client.get(f"/add?saved={tx}").text


def test_edit_archive_delete_jar(client):
    jar = make_jar(client, "Trip", "EUR")
    assert client.post(f"/jars/{jar}/edit", data={"name": "Trip 2", "target": "900", "rate": "44", "opening": "", "quote": ""}).status_code == 303
    with db.connection() as conn:
        row = conn.execute("SELECT * FROM jars WHERE id = ?", (jar,)).fetchone()
    assert (row["name"], row["target"], row["my_rate"], row["currency"]) == ("Trip 2", 90000, 4400, "EUR")
    move(client, jar, "5")
    assert client.post(f"/jars/{jar}/delete").status_code == 400  # has transfers
    assert client.post(f"/jars/{jar}/archive").status_code == 303
    assert "Trip 2" not in client.get("/add").text.split('data-kind="jar"')[1].split("</fieldset>")[0]
    assert "Archived (1)" in client.get("/jars").text
    assert client.post(f"/jars/{jar}/restore").status_code == 303
    empty = make_jar(client, "Empty")
    assert client.post(f"/jars/{empty}/delete").status_code == 303
    assert client.get("/jars/999/edit").status_code == 404


def test_home_jars_card_per_currency(client):
    make_jar(client, "Hryvnia pot", "UAH", target="1000")
    make_jar(client, "Euro pot", "EUR", target="500")
    page = client.get("/").text
    uah_view, eur_view = page.split('data-cur="UAH"')[1].split('data-cur="EUR"')[0], page.split('class="cur-view" data-cur="EUR"')[1]
    assert "Hryvnia pot" in uah_view and "Euro pot" not in uah_view
    assert "Euro pot" in eur_view and "Hryvnia pot" not in eur_view
