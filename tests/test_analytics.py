from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import analytics, db, ui
from app.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "data" / "test.db"))
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def cat(name, kind="expense"):
    with db.connection() as conn:
        return conn.execute("SELECT id FROM categories WHERE kind = ? AND name = ?", (kind, name)).fetchone()["id"]


def tx(day, name, amount, currency="UAH", kind="expense"):
    with db.connection() as conn:
        conn.execute(
            "INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES (?, ?, ?, ?, ?)",
            (day, kind, cat(name, kind), amount, currency),
        )


def limit(month, name, currency, amount):
    with db.connection() as conn:
        conn.execute("INSERT INTO plans VALUES (?, ?, ?, ?)", (month, cat(name), currency, amount))


def plan(month, name, currency, amount, kind="expense"):
    with db.connection() as conn:
        conn.execute("INSERT INTO plans VALUES (?, ?, ?, ?)", (month, cat(name, kind), currency, amount))


TODAY = date(2026, 11, 10)


def run(fn, *args):
    with db.connection() as conn:
        return fn(conn, *args)


def test_percent_text():
    assert analytics.percent_text(50, 200) == "25%"
    assert analytics.percent_text(1, 1000) == "<1%"
    assert analytics.percent_text(0, 100) == "0%" and analytics.percent_text(5, 0) == "0%"


def test_where_the_money_goes_is_sorted_with_shares_and_groups(client):
    tx("2026-11-02", "Food", 60000)  # essentials
    tx("2026-11-03", "Phone", 20000)  # fixed
    tx("2026-11-04", "Coffee", 20000)  # fun
    tx("2026-11-05", "Coffee", 5000, "EUR")  # another currency: ignored
    tx("2026-11-06", "Salary", 99999, kind="income")  # income: ignored
    tx("2026-10-30", "Food", 99999)  # another month: ignored
    where = run(analytics.where_money, date(2026, 11, 1), date(2026, 12, 1), "UAH")
    assert where["total"] == 100000
    groups = {g["key"]: g for g in where["groups"]}
    assert [g["key"] for g in where["groups"]] == ["essentials", "fixed", "fun"]
    assert groups["essentials"]["h"] == 100.0 and groups["fixed"]["h"] == 33.3
    assert [(c["name"], c["pct"], c["h"]) for c in groups["essentials"]["cats"]] == [("Food", "60%", 100.0)]
    assert (groups["essentials"]["pct"], groups["fixed"]["pct"], groups["fun"]["pct"]) == ("60%", "20%", "20%")


def test_running_month_is_compared_with_the_same_days_of_last_month(client):
    tx("2026-10-03", "Food", 10000)
    tx("2026-10-20", "Food", 90000)  # after day 10: not part of the fair comparison
    tx("2026-11-05", "Food", 30000)
    d = run(analytics.dynamics, "month", date(2026, 11, 1), TODAY, "UAH")["expense"]
    assert (d["now"], d["before"], d["diff"], d["pct"], d["before_full"]) == (30000, 10000, 20000, 200, 100000)


def test_a_finished_month_is_compared_in_full(client):
    tx("2026-10-03", "Food", 10000)
    tx("2026-10-20", "Food", 90000)
    tx("2026-09-15", "Food", 50000)
    result = run(analytics.dynamics, "month", date(2026, 10, 1), TODAY, "UAH")
    d = result["expense"]
    assert result["running"] is False
    assert (d["now"], d["before"], d["diff"], d["pct"], d["before_full"]) == (100000, 50000, 50000, 100, None)


def test_no_percent_when_there_is_nothing_to_compare_with(client):
    tx("2026-11-05", "Food", 3000)
    d = run(analytics.dynamics, "month", date(2026, 11, 1), TODAY, "UAH")["expense"]
    assert d["before"] == 0 and d["pct"] is None


def test_year_to_date_against_last_year_to_date(client):
    tx("2025-02-01", "Food", 10000)
    tx("2025-12-01", "Food", 90000)  # later than 10 Nov: not part of the fair comparison
    tx("2026-03-01", "Food", 40000)
    result = run(analytics.dynamics, "year", date(2026, 1, 1), TODAY, "UAH")
    d = result["expense"]
    assert result["running"] and (d["now"], d["before"], d["before_full"]) == (40000, 10000, 100000)


def test_leap_day_does_not_break_the_year_comparison(client):
    assert run(analytics.dynamics, "year", date(2028, 1, 1), date(2028, 2, 29), "UAH")["running"] is True


def test_forecast_keeps_fixed_as_it_is_and_projects_the_rest(client):
    tx("2026-11-02", "Food", 100000)  # essentials: 10 days -> x3 for 30 days
    tx("2026-11-03", "Coffee", 20000)  # fun
    tx("2026-11-04", "Phone", 40000)  # fixed: counted as it is
    limit("2026-11", "Food", "UAH", 500000)
    limit("2026-11", "Phone", "UAH", 50000)
    f = run(analytics.forecast, date(2026, 11, 1), TODAY, "UAH")
    assert (f["spent"], f["fixed"], f["flexible"], f["day"], f["days"]) == (160000, 40000, 120000, 10, 30)
    assert f["per_day"] == 12000
    assert f["projected"] == 300000 + 60000 + 40000
    b = f["budget"]
    assert (b["limit"], b["projected"], b["state"]) == (550000, 340000, "ok")
    assert b["unlimited"] == 60000  # the coffee projection, which has no limit


def test_forecast_states_and_no_limits(client):
    tx("2026-11-02", "Food", 100000)
    assert run(analytics.forecast, date(2026, 11, 1), TODAY, "UAH")["budget"] is None
    limit("2026-11", "Food", "UAH", 250000)  # projected 3 000 > 2 500
    b = run(analytics.forecast, date(2026, 11, 1), TODAY, "UAH")["budget"]
    assert (b["state"], b["left"]) == ("over", -50000)
    assert run(analytics.forecast, date(2026, 11, 1), TODAY, "EUR")["projected"] == 0


def test_forecast_on_the_first_day_does_not_divide_by_zero(client):
    tx("2026-11-01", "Food", 1000)
    f = run(analytics.forecast, date(2026, 11, 1), date(2026, 11, 1), "UAH")
    assert f["projected"] == 30000


def test_chart_columns_and_labels(client):
    c = analytics.chart([0, 100000, 50000] + [0] * 9, plan=[80000] * 12, selected=2)
    cols = c["cols"]
    assert c["peak"] == 100000 and len(cols) == 12 and c["has_plan"]
    assert [col["name"] for col in cols[:3]] == ["Jan", "Feb", "Mar"]
    assert cols[0]["bar"] == 0 and cols[0]["label"] == ""  # nothing: no bar, no number
    assert cols[1]["bar"] == 100 and cols[2]["bar"] == 50 and cols[1]["plan_h"] == 80
    assert cols[1]["label"] == "1k" and cols[2]["label"] == "500"
    assert cols[1]["selected"] and not cols[0]["selected"]
    assert not analytics.chart([1] * 12)["has_plan"]


def test_compact_amounts():
    assert [analytics.compact(v) for v in (0, 99, 85000, 120000, 123400, 1500000, 99999999, 150000000)] == [
        "", "", "850", "1.2k", "1.2k", "15k", "1000k", "1.5M"]


def test_forecast_chart_has_cumulative_line_and_limit(client):
    tx("2026-11-02", "Food", 100000)
    tx("2026-11-04", "Food", 50000)
    limit("2026-11", "Food", "UAH", 500000)
    c = run(analytics.forecast, date(2026, 11, 1), TODAY, "UAH")["chart"]
    assert c["fact"].startswith(f"{float(c['left'])},{c['base']} ") and len(c["fact"].split()) == 11  # day 0..10
    assert c["limit_y"] is not None and c["limit_y"] < c["base"]
    assert c["now"]["x"] < c["end"]["x"] and c["end"]["y"] < c["now"]["y"]
    assert [t["label"] for t in c["ticks"]] == [1, 5, 10, 15, 20, 25, 30]


def test_plan_chart_uses_the_year_plan(client):
    plan("2026-03", "Food", "UAH", 100000)
    plan("2026-03", "Salary", "UAH", 2500000, "income")
    tx("2026-03-05", "Food", 80000)
    data = run(analytics.build, "month", date(2026, 3, 1), TODAY, "UAH")
    assert data["versus"]["expense"]["cols"][2]["plan"] == 100000
    assert data["versus"]["expense"]["cols"][2]["value"] == 80000
    assert data["versus"]["income"]["cols"][2]["plan"] == 2500000
    assert data["versus"]["expense"]["cols"][2]["selected"]


def test_forecast_only_for_the_running_month(client):
    assert run(analytics.build, "month", date(2026, 11, 1), TODAY, "UAH")["forecast"] is not None
    assert run(analytics.build, "month", date(2026, 10, 1), TODAY, "UAH")["forecast"] is None
    assert run(analytics.build, "year", date(2026, 1, 1), TODAY, "UAH")["forecast"] is None


def test_page_renders_both_currencies_and_all_sections(client, monkeypatch):
    monkeypatch.setattr(ui, "HIDDEN_BLOCKS", frozenset())  # bring every hidden block back for this test
    today = date.today()
    tx(f"{today:%Y-%m}-01", "Food", 12300)
    page = client.get("/analytics").text
    assert page.count('class="cur-view"') == 2
    for text in ("Where the money goes", "Forecast to the end of the month", "Plan ↔ actual", "Expenses by month", "Income by month"):
        assert text in page
    assert "Fixed counted as is" in page and "Jan" in page and "data-wm-group" in page
    assert "<svg" in page and "<script src" in page and "chart.js" not in page.lower()


def test_year_page_has_no_forecast_and_odd_urls_are_safe(client):
    assert "Forecast to the end" not in client.get("/analytics?period=year").text
    for url in ("/analytics?month=junk", "/analytics?month=2099-01", "/analytics?period=year&year=2099", "/analytics?period=x"):
        assert client.get(url).status_code == 200


def test_period_switch_and_arrows(client):
    page = client.get("/analytics?month=2026-03").text
    assert 'href="/analytics?month=2026-02"' in page and 'href="/analytics?month=2026-04"' in page
    assert 'href="/analytics?period=year&amp;year=2026"' in page
    year = client.get("/analytics?period=year&year=2025").text
    assert 'href="/analytics?period=year&amp;year=2024"' in year and 'href="/analytics?month=2025-12"' in year
