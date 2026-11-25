from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, fx, monthly
from app.main import app


def flat(text):
    return text.replace("\u00a0", " ").replace("\u202f", " ")


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
    assert page.count("No plan for November 2026 yet.") == 2 and page.count("so there is no limit") == 2 and 'href="/year?year=2026"' in page


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
    # Overview, jars and accounts: one currency each. Categories (and the half-circle): ₴ and € together, in ₴.
    overview = lambda part: part.split('class="grid grid-4"')[1].split('data-panel="jars"')[0]  # Income, Expenses, In jars, Free
    assert "₴" in overview(uah) and "€" not in overview(uah)
    assert "€" in overview(eur) and "₴" not in overview(eur)
    cats = lambda part: part.split('data-panel="categories"')[1].split('aria-label="Categories"')[1].split("</ul>")[0]
    assert cats(uah) == cats(eur) and "₴" in cats(uah) and "€" not in cats(uah)
    for part in (uah, eur):
        for block in ("Income", "Expenses", "In jars", "Free", "left this month", "Essentials", "Food"):
            assert block in part
        for gone in ("Balance", "Recent"):  # hidden for now (see app/ui.py)
            assert gone not in part
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


def test_categories_count_hryvnias_and_euros_together_at_the_nbu_rate(client):
    """AI: 1 200 ₴ + 200 ₴ + 10 € (at 50 ₴) against a 3 000 ₴ plan and a 20 € plan (= 1 000 ₴)."""
    with db.connection() as conn:
        for key, value in (("nbu_rate", "5000"), ("nbu_rate_day", date.today().isoformat()), ("rate", "4000")):
            conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    set_limit("2026-11", "AI", "UAH", 300000)
    set_limit("2026-11", "AI", "EUR", 2000)
    spend("AI", 120000)
    spend("AI", 20000)
    spend("AI", 1000, "EUR")
    with db.connection() as conn:
        row = next(r for r in monthly.month_together(conn, date(2026, 11, 1), date(2026, 12, 1))["rows"] if r["name"] == "AI")
    assert row["line"]["spent"] == 140000 + 50000 and row["line"]["limit"] == 300000 + 100000
    assert row["line"]["currency"] == "UAH" and row["line"]["state"] == "ok"
    page = client.get("/?month=2026-11").text
    assert "All in ₴ · € at NBU rate 50" in page


def test_without_a_fresh_nbu_rate_her_own_rate_is_used_and_without_any_euros_are_left_out(client):
    spend("AI", 10000)
    spend("AI", 1000, "EUR")
    with db.connection() as conn:
        assert fx.current_rate(conn) is None
        assert monthly.spent_by_category(conn, date(2026, 11, 1), date(2026, 12, 1), fx.TOGETHER) == {cat("AI"): 10000}
    with db.connection() as conn:
        conn.execute("INSERT INTO meta (key, value) VALUES ('nbu_rate', '5000'), ('nbu_rate_day', '2026-01-01'), ('rate', '4000')")
    with db.connection() as conn:
        assert fx.current_rate(conn)["source"] == "your rate"  # the NBU one is too old
        assert monthly.spent_by_category(conn, date(2026, 11, 1), date(2026, 12, 1), fx.TOGETHER) == {cat("AI"): 10000 + 40000}
        assert monthly.spent_by_category(conn, date(2026, 11, 1), date(2026, 12, 1), "EUR") == {cat("AI"): 1000}


def income_cat(name="Salary"):
    with db.connection() as conn:
        return conn.execute("SELECT id FROM categories WHERE kind = 'income' AND name = ?", (name,)).fetchone()["id"]


def earn(amount, day="2026-11-03"):
    with db.connection() as conn:
        conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES (?, 'income', ?, ?, 'UAH')", (day, income_cat(), amount))


def people(direction, amount, day="2026-11-07"):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO transactions (date, kind, amount, currency, counterpart, direction) VALUES (?, 'transfer', ?, 'UAH', 'people', ?)",
            (day, amount, direction),
        )


def reality():
    with db.connection() as conn:
        return monthly.month_reality(conn, date(2026, 11, 1), date(2026, 12, 1))


def test_reality_without_extra_is_over_like_the_plan(client):
    """Plan 20 000, spent 25 000, nothing beyond the plan: ✗ over by 5 000."""
    set_limit("2026-11", "Food", "UAH", 2000000)
    spend("Food", 2500000)
    r = reality()
    assert (r["spent"], r["limit"], r["extra"], r["left"], r["state"]) == (2500000, 2000000, 0, -500000, "over")
    page = flat(client.get("/?month=2026-11").text)
    assert "✗ Over by 5 000" in page


def test_reality_extra_from_income_over_plan_and_people_lifts_the_limit(client):
    """The same month with 5 000 extra (3 000 income over its plan + 2 000 from people): spent = limit, so not over."""
    set_limit("2026-11", "Food", "UAH", 2000000)
    with db.connection() as conn:
        conn.execute("INSERT INTO plans (month, category_id, currency, amount) VALUES ('2026-11', ?, 'UAH', 1000000)", (income_cat(),))
    spend("Food", 2500000)
    earn(1300000)
    people("in", 200000)
    r = reality()
    assert (r["over_income"], r["received"], r["extra"], r["limit"], r["left"]) == (300000, 200000, 500000, 2500000, 0)
    assert r["state"] != "over"
    page = flat(client.get("/?month=2026-11").text)
    assert "+ 5 000 ₴ beyond the plan" in page and "income over plan" not in page and "Limit = planned" not in page
    assert 'data-spend-choice="reality"' in page and 'data-spend-view="reality"' in page


def test_reality_counts_all_spending_and_sent_to_people_and_income_under_plan_gives_nothing(client):
    set_limit("2026-11", "Food", "UAH", 100000)
    spend("Food", 50000)
    spend("Coffee", 20000)  # no plan: still spent in Reality
    people("out", 10000)
    with db.connection() as conn:
        conn.execute("INSERT INTO plans (month, category_id, currency, amount) VALUES ('2026-11', ?, 'UAH', 500000)", (income_cat(),))
    earn(300000)  # under the income plan: no extra
    r = reality()
    assert (r["spent"], r["extra"], r["limit"], r["left"], r["state"]) == (80000, 0, 100000, 20000, "close")
    with db.connection() as conn:  # the category bars stay on the plan
        food = next(x for x in monthly.month_together(conn, date(2026, 11, 1), date(2026, 12, 1))["rows"] if x["name"] == "Food")
    assert (food["line"]["spent"], food["line"]["limit"]) == (50000, 100000)


def test_reality_needs_some_limit(client):
    spend("Food", 50000)
    assert reality() is None
    assert "so there is no limit" in client.get("/?month=2026-11").text
