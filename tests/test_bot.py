"""The bot's money logic (without Telegram) and its chat guard."""

import time
from datetime import date

import pytest

from app import botlogic, db, jars

TODAY = date(2026, 11, 10)


def flat(text):
    return text.replace("\u00a0", " ").replace("\u202f", " ")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    db.init_db()
    with db.connection() as c:
        yield c


def cat_id(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def make_jar(conn, name="Trip", currency="EUR", opening=0):
    return conn.execute("INSERT INTO jars (name, currency, opening) VALUES (?, ?, ?)", (name, currency, opening)).lastrowid


def test_expense_is_saved_as_telegram(conn):
    entry, problem = botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Coffee"),
                                         currency="UAH", amount="80", comment="latte")
    assert not problem and flat(entry["text"]) == "Coffee · 80 ₴ · latte" and entry["quote"] is None
    row = conn.execute("SELECT * FROM transactions").fetchone()
    assert (row["source"], row["amount"], row["date"]) == ("telegram", 8000, "2026-11-10")


def test_comma_amount_and_bad_amount(conn):
    ok, _ = botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount="12,5")
    assert conn.execute("SELECT amount FROM transactions WHERE id = ?", (ok["id"],)).fetchone()[0] == 1250
    for bad in ("abc", "0", "-5", "12+8"):
        entry, problem = botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount=bad)
        assert entry is None and problem
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_jar_deposit_gives_quote_and_euro_paid(conn):
    jar = make_jar(conn)
    entry, problem = botlogic.save_entry(conn, TODAY, kind="jar", jar_id=jar, direction="in", amount="100", paid="4200")
    assert not problem and entry["quote"]
    text = flat(entry["text"])
    assert "Trip" in text and "100" in text and "paid 4 200" in text
    assert jars.get_jar(conn, jar)["balance"] == 10000


def test_withdraw_more_than_jar_is_refused(conn):
    jar = make_jar(conn, currency="UAH", opening=5000)
    entry, problem = botlogic.save_entry(conn, TODAY, kind="jar", jar_id=jar, direction="out", amount="100")
    assert entry is None and "not that much" in problem
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_cancel_within_hour_deletes_then_too_late(conn):
    entry, _ = botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount="5")
    now = time.time()
    assert botlogic.cancel_entry(conn, entry["id"], now - 4000, now=now) == "late"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
    assert botlogic.cancel_entry(conn, entry["id"], now - 60, now=now) == "ok"
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0
    assert botlogic.cancel_entry(conn, entry["id"], now - 60, now=now) == "gone"


def test_cancel_only_touches_bot_entries(conn):
    cur = conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency) VALUES ('2026-11-10','expense',?,100,'UAH')",
                       (cat_id(conn, "Food"),))
    assert botlogic.cancel_entry(conn, cur.lastrowid, time.time()) == "gone"


def test_cancel_deposit_that_a_withdrawal_needs_is_blocked(conn):
    jar = make_jar(conn, currency="UAH")
    dep, _ = botlogic.save_entry(conn, TODAY, kind="jar", jar_id=jar, direction="in", amount="100")
    botlogic.save_entry(conn, TODAY, kind="jar", jar_id=jar, direction="out", amount="60")
    conn.commit()  # each real action has its own connection; here keep the setup
    assert "below zero" in botlogic.cancel_entry(conn, dep["id"], time.time())
    assert conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 2


def start_money(conn, uah=0, eur=0, day="2026-11-01"):
    from app import wealth
    wealth.save_start(conn, day, uah, eur, None)


def test_month_and_jars_text(conn):
    start_money(conn, uah=500000)  # 5 000 ₴ on the 1st
    botlogic.save_entry(conn, TODAY, kind="income", category_id=cat_id(conn, "Salary"), currency="UAH", amount="1000")
    botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount="300")
    jar = conn.execute("INSERT INTO jars (name, currency, target, opening) VALUES ('R&D <Home>','UAH',100000, 50000)").lastrowid
    botlogic.save_entry(conn, TODAY, kind="jar", jar_id=jar, direction="in", amount="200")
    text = flat(botlogic.month_text(conn, TODAY))
    # All money 5 000 + 1 000 − 300 = 5 700; in jars 500 + 200 = 700; Free 5 000
    assert text == (
        "<b>November 2026</b>\n"
        "₴ hryvnas\n\n"
        "<b>Income:</b> 1 000 ₴\n<b>Expenses:</b> 300 ₴\n\n"
        "<b>In jars:</b> 700 ₴\n━━━━━━━━━━\n✓ <b>Free: 5 000 ₴</b>"
    )
    assert flat(botlogic.jars_text(conn)) == "<b>R&amp;D &lt;Home&gt;</b>: 700 ₴ of 1 000 ₴ · 70%"


def test_month_negative_free_gets_cross(conn):
    start_money(conn, uah=10000)  # 100 ₴
    botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount="365")
    text = flat(botlogic.month_text(conn, TODAY))
    assert "<b>Expenses:</b> 365 ₴" in text and "✗ <b>Free: −265 ₴</b>" in text and "€" not in text
    assert "Balance" not in text


def test_month_without_a_start_asks_for_it(conn):
    botlogic.save_entry(conn, TODAY, kind="expense", category_id=cat_id(conn, "Food"), currency="UAH", amount="365")
    text = flat(botlogic.month_text(conn, TODAY))
    assert "<b>Expenses:</b> 365 ₴" in text and "Free: set your starting money" in text


def test_people_count_as_expense_and_income_and_own_cards_do_not(conn):
    start_money(conn, uah=100000)
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-05','transfer',20000,'UAH','out','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-06','transfer',50000,'UAH','in','people')")
    conn.execute("INSERT INTO transactions (date, kind, amount, currency, direction, counterpart) VALUES ('2026-11-07','transfer',70000,'UAH','out','accounts')")
    text = flat(botlogic.month_text(conn, TODAY))
    assert "<b>Income:</b> 500 ₴" in text and "<b>Expenses:</b> 200 ₴" in text  # from / to people only
    assert "✓ <b>Free: 1 300 ₴</b>" in text  # 1 000 + 500 − 200; the move between own cards changes nothing


def test_no_jars_text(conn):
    assert "No jars" in flat(botlogic.jars_text(conn))
