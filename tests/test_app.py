import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, ui
from app.main import app, money, parse_amount


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def category_id(kind, name):
    with db.connection() as conn:
        return conn.execute(
            "SELECT id FROM categories WHERE kind = ? AND name = ?", (kind, name)
        ).fetchone()["id"]


def all_transactions():
    with db.connection() as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM transactions ORDER BY id")]


def entry(**changes):
    values = {
        "kind": "expense",
        "category_id": category_id("expense", "Coffee"),
        "amount": "80",
        "currency": "UAH",
        "comment": "latte",
        "date": "2026-11-08",
    }
    return {**values, **changes}


def test_starting_categories(client):
    with db.connection() as conn:
        counts = dict(conn.execute("SELECT kind, COUNT(*) FROM categories GROUP BY kind").fetchall())
    assert counts == {"income": 5, "expense": 19}
    # Gifts exists as both an income and an expense category.
    assert category_id("income", "Gifts") != category_id("expense", "Gifts")


def test_restart_does_not_restore_removed_categories(client):
    with db.connection() as conn:
        conn.execute("DELETE FROM categories WHERE name = 'Tax'")
    db.init_db()
    with db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM categories WHERE name = 'Tax'").fetchone()[0] == 0


def test_starting_categories_have_groups(client):
    with db.connection() as conn:
        rows = conn.execute('SELECT kind, name, "group" FROM categories').fetchall()
    groups = {row["name"]: row["group"] for row in rows if row["kind"] == "expense"}
    assert groups["Food"] == groups["Pets"] == "essentials"
    assert groups["Phone"] == groups["Tax"] == "fixed"
    assert groups["Coffee"] == groups["Other"] == "fun"
    assert all(groups.values())
    assert all(row["group"] is None for row in rows if row["kind"] == "income")


def test_old_database_gets_groups_without_losing_data(tmp_path, monkeypatch):
    path = tmp_path / "data" / "old.db"
    path.parent.mkdir()
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE categories (
            id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL,
            archived INTEGER NOT NULL DEFAULT 0, UNIQUE (kind, name));
        INSERT INTO categories (kind, name) VALUES ('expense', 'Coffee'), ('income', 'Gifts'),
            ('expense', 'My own category');
        """
    )
    old.commit()
    old.close()
    monkeypatch.setenv("FINANCE_DB", str(path))
    db.init_db()
    db.init_db()  # a second start changes nothing
    with db.connection() as conn:
        rows = {
            (row["kind"], row["name"]): row["group"]
            for row in conn.execute('SELECT kind, name, "group" FROM categories')
        }
    assert rows == {
        ("expense", "Coffee"): "fun",
        ("income", "Gifts"): None,
        ("expense", "My own category"): None,
    }


def test_category_buttons_are_tinted_by_group_without_labels(client):
    page = client.get("/add").text
    assert "g-essentials" in page and "g-fixed" in page and "g-fun" in page
    assert "Essentials" not in page and "Fixed" not in page and "Fun" not in page


def test_home_shows_month_totals_per_currency_without_converting(client):
    today = date.today().isoformat()
    income = category_id("income", "Salary")
    client.post("/add", data=entry(kind="income", category_id=income, amount="1000", date=today))
    client.post("/add", data=entry(amount="250", date=today))
    page = client.get("/").text
    assert money(100000, "UAH") in page and money(25000, "UAH") in page
    assert "€" not in page.split('class="grid grid-4"')[1].split("Jars")[0]  # no euros yet

    client.post("/add", data=entry(amount="10", currency="EUR", date=today))
    page = client.get("/").text
    assert money(100000, "UAH") in page and money(25000, "UAH") in page  # hryvnias unchanged by the euro entry
    assert money(1000, "EUR") in page  # the euro expense on its own line


def test_home_only_counts_the_chosen_month(client):
    client.post("/add", data=entry(amount="999", date="2026-01-15"))
    client.post("/add", data=entry(amount="5", date="2026-02-15"))
    january = client.get("/?month=2026-01").text
    assert money(99900, "UAH") in january and "January 2026" in january
    assert money(500, "UAH") not in january.split("Recent")[0]
    assert client.get("/?month=nonsense").status_code == 200


def test_home_recent_opens_the_transaction(client, monkeypatch):
    monkeypatch.setattr(ui, "HIDDEN_BLOCKS", frozenset())  # bring every hidden block back for this test
    client.post("/add", data=entry())
    tx_id = all_transactions()[0]["id"]
    assert f'href="/transactions?tx={tx_id}"' in client.get("/").text


def test_transaction_details_come_before_edit(client):
    client.post("/add", data=entry(comment="latte"))
    tx_id = all_transactions()[0]["id"]
    page = client.get(f"/transactions?tx={tx_id}").text
    assert f'href="/transactions/{tx_id}/edit?' in page        # the link keeps the month and the filter
    assert f'href="/transactions/{tx_id}/delete?' in page
    assert "latte" in page
    assert client.get("/transactions?tx=999").status_code == 404


def test_theme_switch_has_light_dark_and_system(client):
    page = client.get("/").text
    for choice in ("light", "dark", "system"):
        assert f'data-theme-choice="{choice}"' in page
    # The saved choice is applied in the page head, before drawing, so there is no flash.
    assert page.index('localStorage.getItem("theme")') < page.index("</head>")


def test_add_form_keeps_save_outside_the_scrolling_columns(client):
    page = client.get("/add").text
    assert page.index('class="form-cols"') < page.index('class="form-meta"') < page.index("actions-sticky")


def test_menu_lists_only_working_screens(client):
    page = client.get("/").text
    nav = page.split('<nav class="nav"')[1].split("</nav>")[0]
    assert nav.count("<a ") == 7  # Jars and Settings are in the menu too, but only a computer shows them
    for href in ('href="/"', 'href="/add"', 'href="/year"', 'href="/analytics"', 'href="/transactions"'):
        assert href in nav


def test_add_screen_lists_categories_in_english(client):
    page = client.get("/add").text
    assert "Skin &amp; body care" in page
    assert "Salary" in page


def test_add_saves_and_confirms(client):
    response = client.post("/add", data=entry(amount="1 234,50"))
    assert response.status_code == 303

    saved = all_transactions()
    assert len(saved) == 1
    assert saved[0]["amount"] == 123450
    assert saved[0]["source"] == "site"
    assert saved[0]["comment"] == "latte"

    confirmation = client.get(response.headers["location"]).text
    assert "✓ Saved: Coffee" in confirmation
    assert money(123450, "UAH") in confirmation


def test_income_is_shown_with_plus_and_expense_with_minus(client):
    client.post("/add", data=entry())
    client.post(
        "/add",
        data=entry(kind="income", category_id=category_id("income", "Salary"), currency="EUR"),
    )
    page = client.get("/transactions").text
    assert "−" + money(8000, "UAH") in page
    assert "+" + money(8000, "EUR") in page


@pytest.mark.parametrize(
    "amount",
    ["", "0", "0.00", "-5", "abc", "1.234", "NaN", "1e5", "٣", "9" * 5000, "1000000000"],
)
def test_bad_amount_is_rejected_and_input_is_kept(client, amount):
    response = client.post("/add", data=entry(amount=amount, comment="keep me"))
    assert response.status_code == 422
    assert "✗ Enter an amount" in response.text
    assert "keep me" in response.text
    assert all_transactions() == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "transfer"),
        ("category_id", ""),
        ("category_id", "999999"),
        ("currency", "USD"),
        ("date", ""),
        ("date", "2026-13-45"),
        ("comment", "x" * 501),
    ],
)
def test_bad_fields_are_rejected(client, field, value):
    response = client.post("/add", data=entry(**{field: value}))
    assert response.status_code == 422
    assert all_transactions() == []


def test_category_of_the_other_kind_is_rejected(client):
    response = client.post("/add", data=entry(category_id=category_id("income", "Salary")))
    assert response.status_code == 422
    assert "✗ Choose an expense category." in response.text
    assert all_transactions() == []


def test_archived_category_cannot_be_used_for_new_entries_but_stays_on_old_ones(client):
    client.post("/add", data=entry())
    coffee = category_id("expense", "Coffee")
    with db.connection() as conn:
        conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (coffee,))

    assert client.post("/add", data=entry()).status_code == 422
    assert f'name="category_id" value="{coffee}"' not in client.get("/add").text

    tx_id = all_transactions()[0]["id"]
    assert f'name="category_id" value="{coffee}"' in client.get(f"/transactions/{tx_id}/edit").text
    response = client.post(f"/transactions/{tx_id}/edit", data=entry(comment="still coffee"))
    assert response.status_code == 303
    assert all_transactions()[0]["comment"] == "still coffee"


def test_edit_changes_the_entry(client):
    client.post("/add", data=entry(amount="80.50"))
    tx_id = all_transactions()[0]["id"]
    assert 'value="80.50"' in client.get(f"/transactions/{tx_id}/edit").text

    response = client.post(
        f"/transactions/{tx_id}/edit",
        data=entry(
            kind="income",
            category_id=category_id("income", "Refunds"),
            amount="12",
            currency="EUR",
            comment="returned",
            date="2026-11-01",
        ),
    )
    assert response.status_code == 303

    saved = all_transactions()
    assert len(saved) == 1
    assert (saved[0]["kind"], saved[0]["amount"], saved[0]["currency"]) == ("income", 1200, "EUR")
    assert (saved[0]["comment"], saved[0]["date"]) == ("returned", "2026-11-01")


def test_bad_edit_changes_nothing(client):
    client.post("/add", data=entry())
    tx_id = all_transactions()[0]["id"]
    response = client.post(f"/transactions/{tx_id}/edit", data=entry(amount="oops"))
    assert response.status_code == 422
    assert all_transactions()[0]["amount"] == 8000


def test_delete_needs_confirmation(client):
    client.post("/add", data=entry())
    tx_id = all_transactions()[0]["id"]

    confirm_page = client.get(f"/transactions/{tx_id}/delete")
    assert "Delete this entry?" in confirm_page.text
    assert len(all_transactions()) == 1

    response = client.post(f"/transactions/{tx_id}/delete")
    assert response.status_code == 303
    assert all_transactions() == []
    assert "✓ Deleted." in client.get(response.headers["location"]).text


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/transactions/999/edit"),
        ("post", "/transactions/999/edit"),
        ("get", "/transactions/999/delete"),
        ("post", "/transactions/999/delete"),
    ],
)
def test_missing_transaction_is_404(client, method, path):
    kwargs = {"data": entry()} if path.endswith("edit") and method == "post" else {}
    assert getattr(client, method)(path, **kwargs).status_code == 404


def test_list_is_newest_first(client):
    client.post("/add", data=entry(comment="older", date="2026-11-01"))
    client.post("/add", data=entry(comment="newer", date="2026-11-08"))
    page = client.get("/transactions").text
    assert page.index("newer") < page.index("older")


def test_comment_is_escaped(client):
    client.post("/add", data=entry(comment="<script>alert(1)</script>"))
    page = client.get("/transactions").text
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;" in page


def test_other_websites_cannot_submit_forms(client):
    response = client.post("/add", data=entry(), headers={"origin": "https://evil.example"})
    assert response.status_code == 403
    assert all_transactions() == []

    same_site = client.post("/add", data=entry(), headers={"origin": "http://testserver"})
    assert same_site.status_code == 303


def test_parse_amount():
    assert parse_amount("80") == 8000
    assert parse_amount("80.5") == 8050
    assert parse_amount("80,50") == 8050
    assert parse_amount("0.01") == 1
    assert parse_amount("999 999 999.99") == 99_999_999_999


def test_money_format():
    assert money(8000, "UAH") == "80 ₴"
    assert money(123450, "EUR") == "1 234.50 €"
    assert money(5, "UAH") == "0.05 ₴"


def nav_current(page):
    nav = page.split('<nav class="nav"')[1].split("</nav>")[0]
    return [chunk.split('href="')[1].split('"')[0] for chunk in nav.split("<a ")[1:] if 'aria-current="page"' in chunk]


def test_menu_marks_the_current_screen(client):
    tx_id = client.post("/add", data=entry()).headers["location"].split("=")[1]
    assert nav_current(client.get("/").text) == ["/"]
    assert nav_current(client.get("/year").text) == ["/year"]
    assert nav_current(client.get("/analytics").text) == ["/analytics"]
    assert nav_current(client.get("/add").text) == ["/add"]
    assert nav_current(client.get("/transactions").text) == ["/transactions"]
    assert nav_current(client.get(f"/transactions?tx={tx_id}").text) == ["/transactions"]
    assert nav_current(client.get(f"/transactions/{tx_id}/edit").text) == ["/transactions"]


def test_transaction_list_rows_glow_like_home(client):
    client.post("/add", data=entry())
    row = client.get("/transactions").text.split('class="tx ')[1].split('"')[0]
    assert row == "g-fun"


def test_static_files_carry_a_version_so_browsers_pick_up_updates(client):
    import re

    page = client.get("/").text
    css = re.search(r'href="(/static/style\.css\?v=\d+)"', page)
    js = re.search(r'src="(/static/app\.js\?v=\d+)"', page)
    assert css and js
    assert client.get(css.group(1)).status_code == 200 and client.get(js.group(1)).status_code == 200


def test_site_is_an_installable_app(client):
    manifest = client.get("/manifest.webmanifest")
    assert manifest.status_code == 200 and manifest.headers["content-type"].startswith("application/manifest+json")
    data = manifest.json()
    assert data["display"] == "standalone" and data["start_url"] == "/"
    sizes = {icon["sizes"] for icon in data["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in data["icons"]:
        assert client.get(icon["src"]).status_code == 200
    worker = client.get("/sw.js")
    assert worker.status_code == 200 and worker.headers["service-worker-allowed"] == "/"
    page = client.get("/").text
    assert 'rel="manifest"' in page and "apple-touch-icon" in page and 'name="theme-color"' in page
