"""'Left at end' on Year: what is left at the end of each month (app/wealth.py: left_at_month_end)."""

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


def values(result, code="UAH"):
    return [m["value"] for m in result[code]["months"]]


TODAY = date(2026, 2, 15)  # February 2026 is the running month


def test_what_happened_up_to_this_month_then_the_plan(conn):
    wealth.save_start(conn, "2026-01-01", 1000000, 0, None)           # 10 000 ₴
    tx(conn, "2026-01-05", "income", 500000, "Salary")
    tx(conn, "2026-01-10", "expense", 200000, "Food")                  # Jan: 10 000 + 5 000 − 2 000 = 13 000
    tx(conn, "2026-02-03", "expense", 100000, "Food")                  # Feb so far: 12 000
    tx(conn, "2026-02-20", "expense", 999999, "Food")                  # dated after today: not counted
    plan(conn, "2026-03", "Salary", 300000)
    plan(conn, "2026-03", "Food", 100000)                              # Mar: +3 000 − 1 000 → 14 000 (≈)
    got = wealth.left_at_month_end(conn, wealth.get_start(conn), 2026, TODAY)
    assert values(got)[:5] == [1300000, 1200000, 1400000, 1400000, 1400000]  # April and the rest: no plan, the number stays
    assert [m["forecast"] for m in got["UAH"]["months"]][:4] == [False, False, True, True]
    assert got["UAH"]["year"] == got["UAH"]["months"][11] and got["UAH"]["year"]["value"] == 1400000


def test_january_continues_from_december_of_the_year_before(conn):
    wealth.save_start(conn, "2026-01-01", 1000000, 0, None)
    plan(conn, "2026-12", "Salary", 200000)
    plan(conn, "2027-01", "Salary", 100000)
    plan(conn, "2027-01", "Food", 30000)
    start = wealth.get_start(conn)
    this_year = wealth.left_at_month_end(conn, start, 2026, TODAY)
    next_year = wealth.left_at_month_end(conn, start, 2027, TODAY)
    assert this_year["UAH"]["year"]["value"] == 1200000
    assert next_year["UAH"]["months"][0]["value"] == 1200000 + 70000 and next_year["UAH"]["months"][0]["forecast"]
    assert wealth.left_at_month_end(conn, start, 2030, TODAY)["UAH"]["months"][0]["value"] == 1270000  # and on and on


def test_months_before_the_starting_day_have_no_number(conn):
    wealth.save_start(conn, "2026-03-10", 500000, 0, None)
    tx(conn, "2026-03-05", "expense", 100000, "Food")                  # before the start: not counted
    tx(conn, "2026-03-12", "expense", 40000, "Food")
    got = wealth.left_at_month_end(conn, wealth.get_start(conn), 2026, date(2026, 3, 20))
    assert values(got)[:4] == [None, None, 460000, 460000]
    assert wealth.left_at_month_end(conn, wealth.get_start(conn), 2025, date(2026, 3, 20))["UAH"]["year"]["value"] is None


def test_without_a_start_there_is_nothing_to_show(conn):
    assert wealth.left_at_month_end(conn, None, 2026, TODAY) is None


def test_people_count_but_own_cards_and_jars_do_not(conn):
    wealth.save_start(conn, "2026-02-01", 100000, 0, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 0)").lastrowid
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-02-02','transfer',20000,'UAH','out','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-02-03','transfer',50000,'UAH','in','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-02-04','transfer',70000,'UAH','out','accounts')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, jar_id, direction) VALUES ('2026-02-05','transfer',30000,'UAH',?,'in')", (jar,))
    got = wealth.left_at_month_end(conn, wealth.get_start(conn), 2026, TODAY)
    assert got["UAH"]["months"][1]["value"] == 130000  # 1 000 − 200 + 500: the jar money is still hers


def test_euros_and_hryvnias_stay_apart(conn):
    wealth.save_start(conn, "2026-02-01", 100000, 40000, None)
    tx(conn, "2026-02-02", "expense", 5000, "Food", "EUR")
    plan(conn, "2026-03", "Salary", 70000, "UAH")
    got = wealth.left_at_month_end(conn, wealth.get_start(conn), 2026, TODAY)
    assert got["EUR"]["months"][1]["value"] == 35000 and got["EUR"]["months"][2]["value"] == 35000
    assert got["UAH"]["months"][1]["value"] == 100000 and got["UAH"]["months"][2]["value"] == 170000


def test_the_running_month_matches_all_money_on_home(conn):
    wealth.save_start(conn, "2026-01-01", 1000000, 0, None)
    tx(conn, "2026-01-10", "expense", 150000, "Food")
    tx(conn, "2026-02-04", "income", 400000, "Salary")
    start = wealth.get_start(conn)
    got = wealth.left_at_month_end(conn, start, 2026, TODAY)
    assert got["UAH"]["months"][1]["value"] == wealth.all_money(conn, start, TODAY.isoformat())["UAH"]


# ---------- On the page ----------
def page_for(conn, year=2026):
    conn.commit()
    return TestClient(app).get(f"/year?year={year}").text.replace(" ", " ").replace(" ", " ")


def test_the_row_is_on_the_page_with_forecasts_and_dashes(conn):
    wealth.save_start(conn, f"{date.today().year}-03-01", 1000000, 0, None)
    plan(conn, f"{date.today().year}-12", "Salary", 100000)
    page = page_for(conn, date.today().year)
    row = page.split('class="yr-sum yr-left"')[1].split("</tr>")[0]
    assert "Left at end" in row and 'title="Left at the end of the month"' in row
    assert 'class="c-none">—' in row           # January and February come before the start
    if date.today().month < 12:
        assert 'class="c-left fc">≈' in row     # months still to come come from the plan
    assert "Left at end: what you have at the end of each month" in page


def test_without_a_start_the_page_says_how_to_get_the_row(conn):
    page = page_for(conn)
    assert "yr-left" not in page.split("<tbody>")[1] and "set your starting money" in page


def test_the_row_stays_in_every_view_and_next_year_continues(conn):
    year = date.today().year
    wealth.save_start(conn, f"{year}-01-01", 1000000, 0, None)
    plan(conn, f"{year + 1}-01", "Salary", 250000)
    page = page_for(conn, year + 1)
    row = page.split('class="yr-sum yr-left"')[1].split("</tr>")[0]
    assert "≈12 500" in row or "≈ 12 500" in row or "12 500" in row  # 10 000 carried over + 2 500
    assert "c-plan" not in row and "c-actual" not in row and "c-diff" not in row  # not tied to a view: always shown
