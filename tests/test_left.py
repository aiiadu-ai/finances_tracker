"""'Left at end' on Year: the plan alone, what really happened (with a forecast), and the difference."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, wealth
from app.main import app


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("OWNER_LOGIN", raising=False)
    db.init_db()
    with db.connection() as c:
        yield c


def cat(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def tx(conn, day, kind, amount, name, currency="UAH"):
    conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES (?, ?, ?, ?, ?)", (day, kind, cat(conn, name), amount, currency))


def plan(conn, month, name, amount, currency="UAH"):
    conn.execute("INSERT INTO plans VALUES (?, ?, ?, ?)", (month, cat(conn, name), currency, amount))


def cells(result, name, code="UAH"):
    return [m["value"] for m in result[code][name]["months"]]


TODAY = date(2026, 11, 17)  # November 2026 is the running month


def scenario(conn, salary_in_november=True):
    """Start 10 000 ₴ on 1 Oct. Plan: Oct +5 000 −2 000; Nov +5 000 −2 000 −500 (coffee); Dec +5 000 −2 000.
    Happened: Oct +5 000 −2 500; Nov (to the 17th) +5 000 −1 200 (food) −600 (coffee, over its plan)."""
    wealth.save_start(conn, "2026-10-01", 1000000, 0, None)
    for month, coffee in (("2026-10", 0), ("2026-11", 50000), ("2026-12", 0)):
        plan(conn, month, "Salary", 500000)
        plan(conn, month, "Food", 200000)
        if coffee:
            plan(conn, month, "Coffee", coffee)
    tx(conn, "2026-10-01", "income", 500000, "Salary")
    tx(conn, "2026-10-15", "expense", 250000, "Food")
    if salary_in_november:
        tx(conn, "2026-11-01", "income", 500000, "Salary")
    tx(conn, "2026-11-05", "expense", 120000, "Food")
    tx(conn, "2026-11-09", "expense", 60000, "Coffee")
    return wealth.get_start(conn)


def left(conn, start, year=2026, today=TODAY):
    return wealth.left_at_month_end(conn, start, year, today)


# ---------- 1. Plan: the plan alone ----------
def test_plan_mode_is_the_start_money_plus_the_planned_balance_month_by_month(conn):
    got = left(conn, scenario(conn))
    plan_row = cells(got, "plan")
    assert plan_row[8:12] == [None, 1300000, 1550000, 1850000]   # Sep (before the start) —, Oct 13 000, Nov 15 500, Dec 18 500
    assert plan_row[0] is None and got["UAH"]["plan"]["year"]["value"] == 1850000
    assert not any(c["forecast"] for c in got["UAH"]["plan"]["months"])  # it is a plan, not a forecast


def test_plan_mode_ignores_what_really_happened(conn):
    start = scenario(conn)
    tx(conn, "2026-11-12", "expense", 9999900, "Food")   # a huge unplanned spending changes nothing in the plan view
    assert cells(left(conn, start), "plan")[9:12] == [1300000, 1550000, 1850000]


# ---------- 2. Actual: what happened, and the forecast for months to come ----------
def test_actual_mode_is_real_up_to_this_month_so_far(conn):
    got = left(conn, scenario(conn))
    actual = got["UAH"]["actual"]["months"]
    assert actual[8]["value"] is None
    assert (actual[9]["value"], actual[9]["forecast"]) == (1250000, False)    # Oct: 10 000 + 5 000 − 2 500
    assert (actual[10]["value"], actual[10]["forecast"]) == (1570000, False)  # Nov so far: 12 500 + 5 000 − 1 200 − 600 — not a forecast


# ---------- 3. The forecast: this month's rest of the plan, then the plan ----------
def test_what_the_plan_still_expects_this_month_is_counted_per_category(conn):
    scenario(conn)
    uah = wealth.remaining_plan(conn, TODAY)["UAH"]
    expense = {r["name"]: (r["plan"], r["done"], r["left"]) for r in uah["expense"]}
    assert expense == {"Food": (200000, 120000, 80000), "Coffee": (50000, 60000, 0)}   # food: 800 left; coffee is over its plan: nothing more
    assert [(r["name"], r["left"]) for r in uah["income"]] == [("Salary", 0)]            # the salary has come
    assert uah["net"] == -80000
    assert wealth.remaining_plan(conn, TODAY)["EUR"]["net"] == 0


def test_december_forecast_includes_the_rest_of_novembers_plan(conn):
    got = left(conn, scenario(conn))
    december = got["UAH"]["actual"]["months"][11]
    # November ends with 15 700 − 800 (food still to be spent) = 14 900; December adds its plan +3 000
    assert (december["value"], december["forecast"]) == (1790000, True)
    assert december["value"] != 1570000 + 300000   # the old number: it forgot the rest of November's planned spending


def test_planned_income_not_yet_received_is_expected_too(conn):
    got = left(conn, scenario(conn, salary_in_november=False))   # the November salary has not come yet
    # so far 10 500 ... 12 500 + 0 − 1 200 − 600 = 10 700; still expected: +5 000 salary, −800 food, coffee over → 14 900
    assert got["UAH"]["actual"]["months"][10]["value"] == 1070000
    assert got["UAH"]["actual"]["months"][11]["value"] == 1070000 + 500000 - 80000 + 300000


def test_a_category_over_its_plan_expects_nothing_more_and_never_gives_money_back(conn):
    scenario(conn)
    tx(conn, "2026-11-10", "expense", 500000, "Food")   # food is now far over its 2 000 plan
    food = [r for r in wealth.remaining_plan(conn, TODAY)["UAH"]["expense"] if r["name"] == "Food"][0]
    assert food["left"] == 0                              # not negative: an overspend is not refunded by the forecast


def test_the_forecast_goes_on_from_year_to_year(conn):
    start = scenario(conn)
    plan(conn, "2027-01", "Salary", 100000)
    plan(conn, "2027-01", "Food", 30000)
    next_year = left(conn, start, 2027)["UAH"]
    assert next_year["actual"]["months"][0] == {"value": 1790000 + 70000, "forecast": True, "together": None}
    assert next_year["plan"]["months"][0]["value"] == 1850000 + 70000
    assert next_year["actual"]["months"][5]["value"] == 1860000      # no plan from February: the number stays


# ---------- Difference ----------
def test_difference_is_actual_minus_plan_for_the_months_that_happened(conn):
    got = left(conn, scenario(conn))
    diff = cells(got, "diff")
    assert diff[8] is None and diff[9] == 1250000 - 1300000 and diff[10] == 1570000 - 1550000
    assert diff[11] is None                                           # December has not happened


# ---------- The same rules as before ----------
def test_months_before_the_starting_day_have_no_number_in_any_view(conn):
    wealth.save_start(conn, "2026-11-10", 500000, 0, None)
    tx(conn, "2026-11-05", "expense", 100000, "Food")                  # before the start: not counted
    tx(conn, "2026-11-12", "expense", 40000, "Food")
    got = left(conn, wealth.get_start(conn), 2026, date(2026, 11, 20))
    for name in ("plan", "actual", "diff"):
        assert cells(got, name)[:10] == [None] * 10
    assert cells(got, "actual")[10] == 460000
    assert wealth.left_at_month_end(conn, wealth.get_start(conn), 2025, date(2026, 11, 20))["UAH"]["actual"]["year"]["value"] is None


def test_without_a_start_there_is_nothing_to_show(conn):
    assert wealth.left_at_month_end(conn, None, 2026, TODAY) is None


def test_people_count_but_own_cards_and_jars_do_not(conn):
    wealth.save_start(conn, "2026-11-01", 100000, 0, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 0)").lastrowid
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-02','transfer',20000,'UAH','out','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-03','transfer',50000,'UAH','in','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-04','transfer',70000,'UAH','out','accounts')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, jar_id, direction) VALUES ('2026-11-05','transfer',30000,'UAH',?,'in')", (jar,))
    assert left(conn, wealth.get_start(conn))["UAH"]["actual"]["months"][10]["value"] == 130000   # 1 000 − 200 + 500


def test_euros_and_hryvnias_stay_apart_also_in_the_forecast(conn):
    wealth.save_start(conn, "2026-11-01", 100000, 40000, None)
    tx(conn, "2026-11-02", "expense", 5000, "Food", "EUR")
    plan(conn, "2026-11", "Food", 20000, "EUR")      # 200 € planned, 50 € spent: 150 € still expected
    plan(conn, "2026-12", "Salary", 70000, "UAH")
    got = left(conn, wealth.get_start(conn))
    assert cells(got, "actual", "EUR")[10:12] == [35000, 20000]             # 350 € now; the month ends at 350 − 150 = 200 €, December plans no euros
    assert cells(got, "actual")[10:12] == [100000, 170000]


def test_the_running_month_matches_all_money_on_home(conn):
    wealth.save_start(conn, "2026-01-01", 1000000, 0, None)
    tx(conn, "2026-01-10", "expense", 150000, "Food")
    tx(conn, "2026-11-04", "income", 400000, "Salary")
    start = wealth.get_start(conn)
    assert left(conn, start)["UAH"]["actual"]["months"][10]["value"] == wealth.all_money(conn, start, TODAY.isoformat())["UAH"]


# ---------- Rounding down to hundreds, and euros + hryvnias in one small number ----------
def test_floor100_rounds_down_to_whole_hundreds():
    from app.main import floor100
    assert floor100(1234567) == 1230000 and floor100(1230000) == 1230000 and floor100(0) == 0 and floor100(9999) == 0
    assert floor100(-5000) == -10000 and floor100(None) is None


def test_together_adds_euros_at_her_rate_for_plan_and_actual_but_not_for_the_difference(conn):
    wealth.save_start(conn, "2026-02-01", 1000000, 45000, 5000)         # 10 000 ₴, 450 €, rate 50
    plan(conn, "2026-03", "Salary", 100000)
    got = left(conn, wealth.get_start(conn), 2026, date(2026, 2, 15))
    assert got["UAH"]["actual"]["months"][1]["together"] == 1000000 + 2250000
    assert got["EUR"]["plan"]["months"][2]["together"] == 1100000 + 2250000
    assert got["UAH"]["diff"]["months"][1]["together"] is None and got["UAH"]["two"]
    wealth.save_start(conn, "2026-02-01", 1000000, 45000, None)         # no rate
    assert left(conn, wealth.get_start(conn), 2026, date(2026, 2, 15))["UAH"]["two"] is False
    wealth.save_start(conn, "2026-02-01", 1000000, 0, 5000)             # no euros
    assert left(conn, wealth.get_start(conn), 2026, date(2026, 2, 15))["UAH"]["actual"]["months"][1]["together"] is None


# ---------- On the page ----------
def page_for(conn, year):
    conn.commit()
    return TestClient(app).get(f"/year?year={year}").text.replace(" ", " ").replace(" ", " ")


def left_row(page):
    return page.split('class="yr-sum yr-left')[1].split("</tr>")[0]


def test_the_row_carries_all_three_views_and_the_legend_explains_them(conn):
    year = date.today().year
    wealth.save_start(conn, f"{year}-01-01", 1234567, 45000, 5000)
    plan(conn, f"{year}-12", "Salary", 100000)
    page = page_for(conn, year)
    row = left_row(page)
    assert row.count('class="c-plan"') == 13 and row.count('class="c-actual"') == 13 and row.count('class="c-diff"') == 13   # 12 months + the year
    assert "12 300" in row and "12 345" not in row                      # rounded down to hundreds
    assert 'class="c-sub"' in row and "yr-left-two" in page
    for word in ("<strong>Plan:</strong>", "<strong>Actual:</strong>", "<strong>Difference:</strong>", "never below zero", "Actual minus Plan"):
        assert word in page


def test_forecast_months_are_marked_in_actual_but_the_plan_view_has_no_marks(conn):
    year = date.today().year
    wealth.save_start(conn, f"{year}-01-01", 1000000, 0, None)
    plan(conn, f"{year}-12", "Salary", 100000)
    row = left_row(page_for(conn, year))
    months = row.split('<td class="yr-cell')[1:]                       # the 12 month cells
    plan_parts = [c.split('<span class="c-actual">')[0] for c in months]
    actual_parts = [c.split('<span class="c-actual">')[1].split('<span class="c-diff">')[0] for c in months]
    if date.today().month < 12:
        assert any("c-left fc" in part for part in actual_parts)          # months to come: a forecast, marked ≈
    assert all("≈" not in part and " fc" not in part for part in plan_parts)   # the plan view has no forecast marks


def test_without_a_start_the_page_says_how_to_get_the_row(conn):
    page = page_for(conn, date.today().year)
    assert "yr-left" not in page.split("<tbody>")[1] and "set your starting money" in page


def test_next_year_continues_and_the_difference_has_signs(conn):
    year = date.today().year
    wealth.save_start(conn, f"{year}-01-01", 1000000, 0, None)
    plan(conn, f"{year + 1}-01", "Salary", 250000)
    row = left_row(page_for(conn, year + 1))
    assert "12 500" in row                                                 # 10 000 carried over + 2 500
    assert row.count('class="c-diff"') == 13
