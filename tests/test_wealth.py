"""All money, In jars and Free at the end of a month (app/wealth.py)."""

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


def money_tx(conn, day, kind, amount, name, currency="UAH"):
    conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES (?, ?, ?, ?, ?)",
                 (day, kind, cat(conn, name), amount, currency))


def jar_tx(conn, jar, day, direction, amount, currency="UAH"):
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, jar_id, direction) VALUES (?, 'transfer', ?, ?, ?, ?)",
                 (day, amount, currency, jar, direction))


def people_tx(conn, day, direction, amount):
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES (?, 'transfer', ?, 'UAH', ?, 'people')",
                 (day, amount, direction))


def test_the_day_to_count_up_to():
    assert wealth.cutoff_for_month(date(2026, 10, 1), date(2026, 11, 12)) == date(2026, 10, 31)  # a finished month: its last day
    assert wealth.cutoff_for_month(date(2026, 11, 1), date(2026, 11, 12)) == date(2026, 11, 12)  # a running month: today
    assert wealth.cutoff_for_month(date(2026, 12, 1), date(2026, 12, 31)) == date(2026, 12, 31)
    assert wealth.cutoff_for_month(date(2027, 1, 1), date(2026, 12, 31)) == date(2026, 12, 31)  # a month to come: today


def test_free_is_all_money_minus_what_lies_in_jars(conn):
    wealth.save_start(conn, "2026-11-01", 1000000, 0, None)  # 10 000 ₴, 2 000 of it already in a jar
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 200000)").lastrowid
    money_tx(conn, "2026-11-02", "income", 500000, "Salary")
    money_tx(conn, "2026-11-03", "expense", 100000, "Food")
    jar_tx(conn, jar, "2026-11-04", "in", 300000)  # to the jar: still hers, but no longer free
    got = wealth.standing(conn, wealth.get_start(conn), date(2026, 11, 1), date(2026, 11, 12))
    assert got["all_money"]["UAH"] == 1400000      # 10 000 + 5 000 − 1 000: the jar changes nothing
    assert got["in_jars"]["UAH"] == 500000         # 2 000 + 3 000
    assert got["free"]["UAH"] == 900000
    assert got["all_money"]["EUR"] == 0 and got["free"]["EUR"] == 0


def test_a_withdrawal_frees_money_again(conn):
    wealth.save_start(conn, "2026-11-01", 1000000, 0, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 200000)").lastrowid
    jar_tx(conn, jar, "2026-11-04", "out", 150000)
    got = wealth.standing(conn, wealth.get_start(conn), date(2026, 11, 1), date(2026, 11, 12))
    assert got["in_jars"]["UAH"] == 50000 and got["free"]["UAH"] == 950000 and got["all_money"]["UAH"] == 1000000


def test_people_are_expense_and_income_and_own_accounts_are_nothing(conn):
    wealth.save_start(conn, "2026-11-01", 100000, 0, None)
    people_tx(conn, "2026-11-02", "out", 20000)
    people_tx(conn, "2026-11-03", "in", 50000)
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-04','transfer',70000,'UAH','out','accounts')")
    assert wealth.all_money(conn, wealth.get_start(conn))["UAH"] == 130000  # 1 000 − 200 + 500


def test_a_past_month_is_counted_up_to_its_last_day(conn):
    wealth.save_start(conn, "2026-10-01", 1000000, 0, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 0)").lastrowid
    money_tx(conn, "2026-10-15", "income", 300000, "Salary")
    money_tx(conn, "2026-11-02", "expense", 400000, "Food")      # after October ended
    jar_tx(conn, jar, "2026-10-20", "in", 100000)
    jar_tx(conn, jar, "2026-11-03", "in", 200000)                # after October ended
    start = wealth.get_start(conn)
    october = wealth.standing(conn, start, date(2026, 10, 1), date(2026, 11, 12))
    assert (october["all_money"]["UAH"], october["in_jars"]["UAH"], october["free"]["UAH"]) == (1300000, 100000, 1200000)
    november = wealth.standing(conn, start, date(2026, 11, 1), date(2026, 11, 12))
    assert (november["all_money"]["UAH"], november["in_jars"]["UAH"], november["free"]["UAH"]) == (900000, 300000, 600000)


def test_before_the_start_day_or_without_a_start_free_is_unknown(conn):
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 5000)").lastrowid
    none = wealth.standing(conn, None, date(2026, 11, 1), date(2026, 11, 12))
    assert none["all_money"] is None and none["free"] is None and none["in_jars"]["UAH"] == 5000
    wealth.save_start(conn, "2026-11-10", 100000, 0, None)
    before = wealth.standing(conn, wealth.get_start(conn), date(2026, 10, 1), date(2026, 11, 12))
    assert before["all_money"] is None and before["free"] is None
    assert jar


def test_euros_and_hryvnias_are_never_mixed(conn):
    wealth.save_start(conn, "2026-11-01", 100000, 45000, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Rainy', 'EUR', 10000)").lastrowid
    money_tx(conn, "2026-11-02", "expense", 5000, "Food", "EUR")
    got = wealth.standing(conn, wealth.get_start(conn), date(2026, 11, 1), date(2026, 11, 12))
    assert got["all_money"] == {"UAH": 100000, "EUR": 40000} and got["in_jars"] == {"UAH": 0, "EUR": 10000}
    assert got["free"] == {"UAH": 100000, "EUR": 30000} and jar


def test_home_shows_free_and_in_jars_as_they_stand(conn):
    today = date.today()
    wealth.save_start(conn, today.replace(day=1).isoformat(), 500000, 0, None)
    jar = conn.execute("INSERT INTO jars (name, currency, opening) VALUES ('Trip', 'UAH', 100000)").lastrowid
    money_tx(conn, today.replace(day=1).isoformat(), "expense", 30000, "Food")
    conn.commit()
    page = TestClient(app).get("/").text.replace(" ", " ").replace(" ", " ")
    block = page.split('class="grid grid-4"')[1].split("</div>\n      {% if")[0][:2500]
    assert "In jars" in block and "1 000 ₴" in block  # what lies in the jar
    assert "Free" in block and "3 700 ₴" in block     # 5 000 − 300 − 1 000
    assert "4 700 ₴" in page                           # all money: 5 000 − 300
    assert jar


def test_home_for_a_finished_month_shows_that_months_end(conn):
    wealth.save_start(conn, "2026-10-01", 1000000, 0, None)
    money_tx(conn, "2026-10-10", "expense", 100000, "Food")
    money_tx(conn, "2026-11-05", "expense", 400000, "Food")
    conn.commit()
    page = TestClient(app).get("/?month=2026-10").text.replace(" ", " ").replace(" ", " ")
    assert "9 000 ₴" in page and "5 000 ₴" not in page.split('aria-label="Free"')[1][:300]  # October ended with 9 000
