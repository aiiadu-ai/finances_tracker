from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, plans
from app.main import app, cell_key


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def cat(name, kind="expense"):
    with db.connection() as conn:
        return conn.execute("SELECT id FROM categories WHERE kind = ? AND name = ?", (kind, name)).fetchone()["id"]


def plan(month, name, currency, amount, kind="expense"):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO plans (month, category_id, currency, amount) VALUES (?, ?, ?, ?)",
            (month, cat(name, kind), currency, amount),
        )


def tx(day, name, amount, currency="UAH", kind="expense"):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES (?, ?, ?, ?, ?)",
            (day, kind, cat(name, kind), amount, currency),
        )


TODAY = date(2026, 6, 15)


def grid(currency="UAH", year=2026):
    with db.connection() as conn:
        return plans.grid(conn, year, currency, TODAY)


def row(g, name, kind="expense"):
    return next(r for r in g["rows"] if r["name"] == name and r["kind"] == kind)


@pytest.mark.parametrize(
    "kind, plan_, actual, expected",
    [
        ("expense", 10000, 5000, (5000, "ok")),
        ("expense", 10000, 8000, (2000, "close")),
        ("expense", 10000, 10000, (0, "close")),
        ("expense", 10000, 12000, (-2000, "over")),
        ("income", 10000, 12000, (2000, "ok")),
        ("income", 10000, 10000, (0, "ok")),
        ("income", 10000, 9000, (-1000, "close")),
        ("income", 10000, 5000, (-5000, "over")),
        ("expense", None, 5000, (None, None)),
    ],
)
def test_judge(kind, plan_, actual, expected):
    assert plans.judge(kind, plan_, actual) == expected


def test_cells_year_total_and_future_months(client):
    plan("2026-05", "Food", "UAH", 100000)
    plan("2026-06", "Food", "UAH", 100000)
    plan("2026-07", "Food", "UAH", 100000)
    tx("2026-05-10", "Food", 120000)
    tx("2026-06-03", "Food", 30000)
    food = row(grid(), "Food")
    may, june, july = food["cells"][4], food["cells"][5], food["cells"][6]
    assert (may["diff"], may["state"]) == (-20000, "over")
    assert (june["diff"], june["state"]) == (70000, "ok")  # the current month counts
    assert july["plan"] == 100000 and july["diff"] is None  # not yet come: no difference
    assert food["total"]["plan"] == 300000 and food["total"]["actual"] == 150000


def test_unplanned_spending_has_no_difference_but_counts_in_totals(client):
    tx("2026-05-10", "Coffee", 4000)
    coffee = row(grid(), "Coffee")
    assert coffee["cells"][4]["diff"] is None and coffee["cells"][4]["actual"] == 4000
    assert grid()["totals"]["expense"]["cells"][4]["actual"] == 4000


def test_income_expense_and_balance_rows(client):
    plan("2026-05", "Salary", "UAH", 5000000, "income")
    plan("2026-05", "Food", "UAH", 1000000)
    tx("2026-05-01", "Salary", 5200000, kind="income")
    tx("2026-05-10", "Food", 900000)
    totals = grid()["totals"]
    assert totals["income"]["cells"][4]["actual"] == 5200000
    assert totals["expense"]["cells"][4]["diff"] == 100000
    may = totals["balance"]["cells"][4]
    assert may["plan"] == 4000000 and may["actual"] == 4300000
    assert may["diff"] == 300000 and may["state"] == "ok"


def test_currencies_are_separate(client):
    plan("2026-05", "Food", "UAH", 100000)
    plan("2026-05", "Food", "EUR", 5000)
    tx("2026-05-10", "Food", 2500, "EUR")
    assert row(grid("UAH"), "Food")["cells"][4]["actual"] == 0
    eur = row(grid("EUR"), "Food")["cells"][4]
    assert eur["plan"] == 5000 and eur["actual"] == 2500


def test_transfers_are_not_counted(client):
    with db.connection() as conn:
        conn.execute("INSERT INTO transactions (date, kind, amount, currency, counterpart) VALUES ('2026-05-02', 'transfer', 9999, 'UAH', 'people')")
    assert grid()["totals"]["expense"]["total"]["actual"] == 0
    assert grid()["totals"]["income"]["total"]["actual"] == 0


def test_other_years_are_ignored(client):
    plan("2025-05", "Food", "UAH", 100000)
    tx("2025-05-10", "Food", 5000)
    assert row(grid(year=2026), "Food")["total"]["plan"] is None


def test_year_page_has_both_currencies_modes_and_a_row_per_category(client):
    page = client.get("/year?year=2026").text
    assert page.count('class="cur-view"') == 2 and 'data-cur="UAH"' in page and 'data-cur="EUR"' in page
    for mode in ("plan", "actual", "diff"):
        assert f'data-mode-choice="{mode}"' in page
    assert page.count("Skin &amp; body care") >= 2  # a row in each currency
    assert page.count('class="yr-tag g-essentials"') == 12  # 6 essentials x 2 currencies
    assert "Salary" in page and "Balance" in page and "Save plan" in page


def test_saving_the_plan_replaces_the_year(client):
    food, salary = cat("Food"), cat("Salary", "income")
    plan("2026-03", "Pets", "UAH", 1000)  # will be replaced
    plan("2025-03", "Pets", "UAH", 1000)  # another year: untouched
    data = {
        "year": "2026",
        cell_key(food, 1, "UAH"): "5 000",
        cell_key(food, 2, "EUR"): "50,5",
        cell_key(salary, 12, "UAH"): "40000",
        cell_key(food, 3, "UAH"): "0",
    }
    assert client.post("/year", data=data).status_code == 303
    with db.connection() as conn:
        saved = plans.month_plans(conn, 2026)
        assert saved == {(food, 1, "UAH"): 500000, (food, 2, "EUR"): 5050, (salary, 12, "UAH"): 4000000}
        assert plans.month_plans(conn, 2025) == {(cat("Pets"), 3, "UAH"): 1000}


def test_bad_amount_saves_nothing_and_keeps_input(client):
    plan("2026-03", "Pets", "UAH", 1000)
    food = cat("Food")
    response = client.post("/year", data={"year": "2026", cell_key(food, 1, "UAH"): "abc", cell_key(food, 2, "UAH"): "300"})
    assert response.status_code == 422
    assert 'value="abc"' in response.text and 'value="300"' in response.text and "Food · Jan · ₴" in response.text
    with db.connection() as conn:
        assert list(plans.month_plans(conn, 2026)) == [(cat("Pets"), 3, "UAH")]


def test_bad_year_is_rejected(client):
    assert client.post("/year", data={"year": "nope"}).status_code == 400
    assert client.post("/year/copy", data={"year": "nope"}).status_code == 400
    assert client.get("/year?year=nope").status_code == 200  # falls back to this year


def test_copy_from_last_year_asks_before_replacing(client):
    plan("2025-03", "Food", "UAH", 1000)
    plan("2025-04", "Food", "EUR", 50)
    assert client.post("/year/copy", data={"year": "2026"}).status_code == 303
    with db.connection() as conn:
        assert plans.month_plans(conn, 2026) == {(cat("Food"), 3, "UAH"): 1000, (cat("Food"), 4, "EUR"): 50}
    plan("2026-05", "Pets", "UAH", 7)
    page = client.post("/year/copy", data={"year": "2026"})
    assert page.status_code == 200 and "already has a plan" in page.text
    with db.connection() as conn:
        assert len(plans.month_plans(conn, 2026)) == 3
    client.post("/year/copy", data={"year": "2026", "confirm": "1"})
    with db.connection() as conn:
        assert len(plans.month_plans(conn, 2026)) == 2


def test_copy_with_nothing_to_copy_says_so(client):
    page = client.post("/year/copy", data={"year": "2026"})
    assert page.status_code == 200 and "no plan for 2025" in page.text


def test_year_column_compares_plan_to_date_not_the_whole_year(client):
    for m in range(1, 13):
        plan(f"2026-{m:02d}", "Salary", "UAH", 100000, "income")
    for m in range(1, 7):
        tx(f"2026-{m:02d}-01", "Salary", 100000, kind="income")
    salary = row(grid(), "Salary", "income")
    # Plan shown is the whole year; the difference is about the first 6 months only: nothing is "short".
    assert salary["total"]["plan"] == 1200000 and salary["total"]["actual"] == 600000
    assert (salary["total"]["diff"], salary["total"]["state"]) == (0, "ok")
    inc = grid()["totals"]["income"]["total"]
    assert (inc["plan"], inc["diff"], inc["state"]) == (1200000, 0, "ok")
    assert grid()["totals"]["balance"]["total"]["state"] == "ok"
