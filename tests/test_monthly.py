from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, monthly
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def cat(name):
    with db.connection() as conn:
        return conn.execute(
            "SELECT id FROM categories WHERE kind = 'expense' AND name = ?", (name,)
        ).fetchone()["id"]


def set_limit(month, name, currency, amount):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO plans (month, category_id, currency, amount) VALUES (?, ?, ?, ?)",
            (month, cat(name), currency, amount),
        )


def spend(name, amount, currency="UAH", day="2026-11-05"):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO transactions (date, kind, category_id, amount, currency) "
            "VALUES (?, 'expense', ?, ?, ?)",
            (day, cat(name), amount, currency),
        )


def summary():
    with db.connection() as conn:
        return monthly.month_summary(conn, date(2026, 11, 1), date(2026, 12, 1))


def line(result, name, currency="UAH"):
    row = next(r for r in result["rows"] if r["name"] == name)
    return next(l for l in row["lines"] if l["currency"] == currency)


@pytest.mark.parametrize(
    "spent, expected",
    [(0, "ok"), (7999, "ok"), (8000, "close"), (10000, "close"), (10001, "over")],
)
def test_state_thresholds(spent, expected):
    assert monthly.state(spent, 10000) == expected


def test_category_line_has_state_and_left(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    spend("Food", 85000)
    food = line(summary(), "Food")
    assert (food["spent"], food["limit"], food["state"], food["left"], food["pct"]) == (
        85000, 100000, "close", 15000, 85,
    )


def test_over_limit_bar_is_capped_but_left_is_negative(client):
    set_limit("2026-11", "Food", "UAH", 10000)
    spend("Food", 15000)
    food = line(summary(), "Food")
    assert food["state"] == "over" and food["pct"] == 100 and food["left"] == -5000


def test_category_without_limit_is_listed_with_spent_only(client):
    spend("Coffee", 8000)
    coffee = line(summary(), "Coffee")
    assert coffee["spent"] == 8000 and coffee["limit"] is None and "state" not in coffee
    # Even a category with nothing at all is listed.
    assert line(summary(), "Tax")["spent"] == 0


def test_arc_counts_only_limited_categories_and_reports_the_rest(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    set_limit("2026-11", "Phone", "UAH", 20000)
    spend("Food", 30000)
    spend("Coffee", 5000)  # no limit: not part of "left", reported separately
    [arc] = summary()["arcs"]
    assert arc["limit"] == 120000 and arc["spent"] == 30000 and arc["left"] == 90000
    assert arc["percent"] == 25 and arc["unlimited"] == 5000


def test_currencies_are_never_mixed(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    set_limit("2026-11", "Food", "EUR", 10000)
    spend("Food", 5000, "EUR")
    arcs = {a["currency"]: a for a in summary()["arcs"]}
    assert arcs["UAH"]["spent"] == 0 and arcs["EUR"]["spent"] == 5000
    # A euro expense in a category that only has a hryvnia limit has no limit in euros.
    spend("Pets", 100, "EUR")
    assert line(summary(), "Pets", "EUR")["limit"] is None


def test_other_months_are_ignored(client):
    set_limit("2026-10", "Food", "UAH", 100000)
    spend("Food", 5000, day="2026-10-30")
    assert summary()["arcs"] == [] and line(summary(), "Food")["spent"] == 0


def test_group_totals(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    spend("Food", 1000)
    spend("Health", 2000)
    spend("Coffee", 500)
    groups = {g["key"]: g["lines"][0] for g in summary()["groups"]}
    assert groups["essentials"]["spent"] == 3000 and groups["essentials"]["limit"] == 100000
    assert groups["fun"]["spent"] == 500 and groups["fun"]["limit"] is None


def test_transfers_and_income_are_not_spending(client):
    with db.connection() as conn:
        conn.execute("INSERT INTO transactions (date, kind, amount, currency, counterpart) VALUES ('2026-11-05', 'transfer', 9999, 'UAH', 'accounts')")
    assert all(not l["spent"] for r in summary()["rows"] for l in r["lines"])


def test_home_shows_arc_and_words(client):
    set_limit("2026-11", "Food", "UAH", 10000)
    spend("Food", 12000)
    page = client.get("/?month=2026-11").text
    assert "over this month" in page and "spent 120%" in page
    assert "✗ Over by" in page and "Essentials" in page


def test_home_without_a_plan_points_to_the_year_plan(client):
    page = client.get("/?month=2026-11").text
    assert "No ₴ plan for November 2026 yet" in page and "No € plan for November 2026 yet" in page and 'href="/year?year=2026"' in page


def view(page, code):
    """The part of Home that belongs to one currency."""
    return page.split(f'class="cur-view" data-cur="{code}"')[1].split('class="cur-view"')[0]


def test_home_has_a_separate_full_set_of_numbers_per_currency(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    set_limit("2026-11", "Food", "EUR", 10000)
    spend("Food", 30000)
    spend("Food", 2500, "EUR")
    page = client.get("/?month=2026-11").text
    uah, eur = view(page, "UAH"), view(page, "EUR")
    assert "₴" in uah and "€" not in uah
    assert "€" in eur and "₴" not in eur
    for part in (uah, eur):
        for block in ("Balance", "Income", "Expenses", "left this month", "Essentials", "Food", "Recent"):
            assert block in part
    assert 'data-cur-choice="UAH"' in page and 'data-cur-choice="EUR"' in page


def test_currency_choice_is_remembered_before_the_page_is_drawn(client):
    page = client.get("/").text
    assert page.index('localStorage.getItem("currency")') < page.index("</head>")


def test_big_numbers_never_wrap(client):
    css = open("app/static/style.css").read()
    assert "white-space: nowrap" in css.split(".stat p")[1].split("}")[0]
    assert "data-fit" in client.get("/").text


def test_the_limit_of_a_category_is_its_planned_amount_not_a_second_number(client):
    set_limit("2026-11", "Food", "UAH", 100000)  # the plan from the year page
    with db.connection() as conn:
        salary = conn.execute("SELECT id FROM categories WHERE name = 'Salary'").fetchone()["id"]
        conn.execute("INSERT INTO plans (month, category_id, currency, amount) VALUES ('2026-11', ?, 'UAH', 900000)", (salary,))
        assert monthly.month_limits(conn, "2026-11") == {(cat("Food"), "UAH"): 100000}  # an income plan is no limit
    spend("Food", 85000)
    assert line(summary(), "Food")["state"] == "close" and line(summary(), "Food")["left"] == 15000
    page = client.get("/?month=2026-11").text
    assert "Within" not in page and "Close" in page


def test_budgets_page_and_menu_item_are_gone(client):
    old = client.get("/budgets", follow_redirects=False)
    assert old.status_code == 303 and old.headers["location"] == "/year"
    assert 'href="/budgets"' not in client.get("/").text
    assert 'href="/analytics"' in client.get("/").text
    assert db.db_path().exists()
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'budgets'").fetchone() is None


def test_old_budgets_move_into_the_plan_and_the_table_goes(tmp_path, monkeypatch):
    import sqlite3
    path = tmp_path / "old.db"
    monkeypatch.setenv("FINANCE_DB", str(path))
    db.init_db()
    with sqlite3.connect(path) as raw:  # a database from before: it still has a budgets table
        raw.executescript(
            """
            CREATE TABLE budgets (month TEXT NOT NULL, category_id INTEGER NOT NULL, currency TEXT NOT NULL, amount INTEGER NOT NULL,
                                  PRIMARY KEY (month, category_id, currency));
            """
        )
        food = raw.execute("SELECT id FROM categories WHERE name = 'Food'").fetchone()[0]
        coffee = raw.execute("SELECT id FROM categories WHERE name = 'Coffee'").fetchone()[0]
        salary = raw.execute("SELECT id FROM categories WHERE name = 'Salary'").fetchone()[0]
        raw.execute("INSERT INTO plans VALUES ('2026-11', ?, 'UAH', 700000)", (food,))     # a plan is already there: it stays
        raw.executemany("INSERT INTO budgets VALUES (?, ?, ?, ?)", [
            ("2026-11", food, "UAH", 500000),      # conflicts with the plan: the plan wins
            ("2026-11", coffee, "UAH", 30000),     # no plan yet: copied
            ("2026-12", coffee, "EUR", 2000),      # copied
            ("2026-11", salary, "UAH", 1),         # an income category is no limit: not copied
        ])
    db.init_db()
    with db.connection() as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'budgets'").fetchone() is None
        got = {(r["month"], r["category_id"], r["currency"]): r["amount"] for r in conn.execute("SELECT * FROM plans")}
        assert got == {("2026-11", food, "UAH"): 700000, ("2026-11", coffee, "UAH"): 30000, ("2026-12", coffee, "EUR"): 2000}
    db.init_db()  # a second start changes nothing
