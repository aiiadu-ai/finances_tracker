import pytest
from fastapi.testclient import TestClient

from app import db
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def row(name, kind="expense"):
    with db.connection() as conn:
        return conn.execute("SELECT * FROM categories WHERE kind = ? AND name = ?", (kind, name)).fetchone()


def test_settings_lists_categories_and_quotes(client):
    page = client.get("/settings").text
    assert "Expense categories" in page and "Skin &amp; body care" in page and "Goal: outdo me." in page


def test_add_rename_regroup_and_archive_a_category(client):
    assert client.post("/settings/categories/add", data={"kind": "expense", "name": "Books", "group": "fun"}).status_code == 303
    books = row("Books")
    assert books["group"] == "fun"
    assert client.post(f"/settings/categories/{books['id']}", data={"name": "Reading", "group": "essentials"}).status_code == 303
    assert (row("Reading")["group"], row("Books")) == ("essentials", None)
    assert "Reading" in client.get("/add").text
    client.post(f"/settings/categories/{books['id']}/archive")
    assert "Reading" not in client.get("/add").text and "Archived categories (1)" in client.get("/settings").text
    client.post(f"/settings/categories/{books['id']}/restore")
    assert "Reading" in client.get("/add").text


def test_category_errors(client):
    dup = client.post("/settings/categories/add", data={"kind": "expense", "name": "food", "group": "fun"})
    assert dup.status_code == 422 and "already" in dup.text
    assert client.post("/settings/categories/add", data={"kind": "expense", "name": "X", "group": "nope"}).status_code == 422
    assert client.post("/settings/categories/add", data={"kind": "expense", "name": "  ", "group": "fun"}).status_code == 422
    assert client.post("/settings/categories/add", data={"kind": "other", "name": "X"}).status_code == 400
    assert client.post("/settings/categories/9999", data={"name": "X"}).status_code == 404
    # income categories have no group
    assert client.post("/settings/categories/add", data={"kind": "income", "name": "Prize"}).status_code == 303
    assert row("Prize", "income")["group"] is None


def test_quotes_add_edit_delete(client):
    assert client.post("/settings/quotes/add", data={"text": "Onward."}).status_code == 303
    with db.connection() as conn:
        qid = conn.execute("SELECT id FROM quotes WHERE text = 'Onward.'").fetchone()["id"]
    assert client.post(f"/settings/quotes/{qid}", data={"text": "Onward!"}).status_code == 303
    assert client.post("/settings/quotes/add", data={"text": "Onward!"}).status_code == 422  # duplicate
    assert client.post("/settings/quotes/add", data={"text": ""}).status_code == 422
    assert client.post(f"/settings/quotes/{qid}/delete").status_code == 303
    assert "Onward!" not in client.get("/settings").text


def test_settings_has_tabs_and_returns_to_the_right_one(client):
    page = client.get("/settings").text
    for name in ("expense", "income", "accounts", "rules", "quotes"):
        assert f'data-tab="{name}"' in page and f'data-panel="{name}"' in page
    bad = client.post("/settings/quotes/add", data={"text": ""})
    assert bad.status_code == 422 and 'data-open="quotes"' in bad.text
    bad = client.post("/settings/categories/add", data={"kind": "income", "name": ""})
    assert bad.status_code == 422 and 'data-open="income"' in bad.text
    done = client.post("/settings/categories/add", data={"kind": "income", "name": "Cashback"}, follow_redirects=False)
    assert done.headers["location"].endswith("#income")


def test_money_now_start_rate_and_all_money(client):
    from app import db
    home = client.get("/?month=2026-11").text
    assert "Set how much money you have now" in home and "All money" not in home
    assert 'data-tab="money"' in client.get("/settings").text
    bad = client.post("/settings/money", data={"start_date": "2026-11-01", "uah": "abc", "eur": "", "rate": ""})
    assert bad.status_code == 422 and 'data-open="money"' in bad.text and 'value="abc"' in bad.text
    done = client.post("/settings/money", data={"start_date": "2026-11-01", "uah": "5 000", "eur": "100", "rate": "40"}, follow_redirects=False)
    assert done.status_code == 303 and done.headers["location"].endswith("#money")
    with db.connection() as conn:
        food = conn.execute("SELECT id FROM categories WHERE name = 'Food'").fetchone()["id"]
        salary = conn.execute("SELECT id FROM categories WHERE name = 'Salary'").fetchone()["id"]
        conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES ('2026-11-05','expense',?,100000,'UAH')", (food,))
        conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES ('2026-11-06','income',?,300000,'UAH')", (salary,))
        conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES ('2026-10-30','expense',?,999999,'UAH')", (food,))  # before the start day
        conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-07','transfer',20000,'UAH','out','people')")
        conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-08','transfer',5000,'UAH','in','accounts')")  # between own accounts: nothing changes
    page = client.get("/?month=2026-11").text.replace(" ", " ").replace(" ", " ")
    # 5 000 − 1 000 + 3 000 − 200 = 6 800 ₴; euros untouched: 100 €; together 6 800 + 100 × 40 = 10 800
    assert "6 800 ₴" in page and "100 €" in page and "Together ≈ 10 800 ₴ at your rate 40" in page
    settings = client.get("/settings").text
    assert 'value="5000"' in settings and 'value="100"' in settings and 'value="40"' in settings
