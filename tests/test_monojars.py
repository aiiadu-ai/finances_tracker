"""Monobank jars: site jars made from them, deposits and withdrawals taken from the jar statement,
matched with the card line so money is counted once."""

import sys
import time
from datetime import date

import pytest

from app import db, jars, mono
from monosync import sync

T0 = 1_794_300_000  # 2026-11-10, Kyiv morning
NOV = (date(2026, 11, 1), date(2026, 12, 1))


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    db.init_db()
    with db.connection() as c:
        yield c


def mj(jid="J1", title="Trip", cur=980, balance=100000, goal=1500000):
    return {"id": jid, "title": title, "currencyCode": cur, "balance": balance, "goal": goal}


def site_jar(conn, mono_id="J1"):
    return dict(conn.execute("SELECT * FROM jars WHERE mono_id = ?", (mono_id,)).fetchone())


def card_line(i, amount, desc="Card line", cur=980, at=T0 + 30):
    return {"id": f"c{i}", "time": at, "description": desc, "amount": amount, "currencyCode": cur}


def jar_line(i, amount, desc="З картки *1234 - Поповнення рахунку банки", cur=980, at=T0 + 30):
    return {"id": f"j{i}", "time": at, "description": desc, "amount": amount, "currencyCode": cur}


# ---------- Making site jars ----------
def test_a_site_jar_is_made_from_each_mono_jar(conn):
    assert mono.sync_jars(conn, [mj(), mj("J2", "Rainy day", 978, 90000, 0), mj("J3", "Dollars", 840, 5000)], T0) == []
    trip, rainy = site_jar(conn, "J1"), site_jar(conn, "J2")
    assert (trip["name"], trip["currency"], trip["target"], trip["opening"], trip["shared"]) == ("Trip", "UAH", 1500000, 100000, 0)
    assert (rainy["name"], rainy["currency"], rainy["target"], rainy["opening"]) == ("Rainy day", "EUR", None, 90000)
    assert conn.execute("SELECT COUNT(*) FROM jars").fetchone()[0] == 2  # dollars are not tracked
    assert jars.get_jar(conn, trip["id"])["balance"] == 100000  # starts equal to the bank


def test_second_look_changes_nothing_until_the_balance_moves(conn):
    mono.sync_jars(conn, [mj()], T0)
    assert mono.sync_jars(conn, [mj()], T0 + 600) == []
    changed = mono.sync_jars(conn, [mj(balance=105000)], T0 + 600)
    assert [pair[0]["mono_id"] for pair in changed] == ["J1"] and conn.execute("SELECT COUNT(*) FROM jars").fetchone()[0] == 1


def test_a_jar_aiia_already_made_is_linked_not_duplicated(conn):
    conn.execute("INSERT INTO jars (name, currency, opening, quote) VALUES ('trip', 'UAH', 777, 'Mine')")
    mono.sync_jars(conn, [mj()], T0)
    rows = conn.execute("SELECT name, opening, quote, mono_id FROM jars").fetchall()
    assert len(rows) == 1 and (rows[0]["opening"], rows[0]["quote"], rows[0]["mono_id"]) == (777, "Mine", "J1")


def test_a_name_that_is_taken_gets_a_suffix(conn):
    conn.execute("INSERT INTO jars (name, currency) VALUES ('Trip', 'EUR')")  # same name, other currency: not the same jar
    mono.sync_jars(conn, [mj()], T0)
    assert site_jar(conn)["name"] == "Trip (Mono)"


# ---------- Deposits and withdrawals ----------
def test_a_deposit_is_matched_with_the_card_line_and_counted_once(conn):
    mono.sync_jars(conn, [mj()], T0)
    mono.import_lines(conn, "CARD", [card_line(1, -5000)])
    counts = mono.import_jar_lines(conn, site_jar(conn), [jar_line(1, 5000)])
    assert counts == {"paired": 1, "alone": 0, "skipped": 0}
    tx = conn.execute("SELECT * FROM transactions").fetchone()
    assert (tx["kind"], tx["jar_id"], tx["direction"], tx["amount"], tx["external_id"], tx["category_id"]) == ("transfer", site_jar(conn)["id"], "in", 5000, "CARD:c1", None)
    assert conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 0
    assert jars.get_jar(conn, site_jar(conn)["id"])["balance"] == 105000  # the bank's number
    assert jars.month_flows(conn, *NOV)["UAH"] == 5000  # counts as money put into jars
    again = mono.import_jar_lines(conn, site_jar(conn), [jar_line(1, 5000)])
    assert again["skipped"] == 1 and conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1


def test_a_withdrawal_is_matched_with_the_card_income(conn):
    mono.sync_jars(conn, [mj()], T0)
    mono.import_lines(conn, "CARD", [card_line(1, 3000)])
    mono.import_jar_lines(conn, site_jar(conn), [jar_line(1, -3000, "На картку")])
    tx = conn.execute("SELECT * FROM transactions").fetchone()
    assert (tx["direction"], tx["amount"], tx["external_id"]) == ("out", 3000, "CARD:c1")
    assert jars.get_jar(conn, site_jar(conn)["id"])["balance"] == 97000


def test_a_jar_line_with_no_card_line_is_saved_on_its_own(conn):
    mono.sync_jars(conn, [mj()], T0)
    counts = mono.import_jar_lines(conn, site_jar(conn), [jar_line(2, 1234, "Відсотки")])
    assert counts["alone"] == 1
    tx = conn.execute("SELECT * FROM transactions").fetchone()
    assert (tx["jar_id"], tx["direction"], tx["amount"], tx["external_id"]) == (site_jar(conn)["id"], "in", 1234, "J1:j2")
    assert jars.get_jar(conn, site_jar(conn)["id"])["balance"] == 101234


def test_old_lines_and_other_currencies_are_skipped(conn):
    mono.sync_jars(conn, [mj()], T0)
    counts = mono.import_jar_lines(conn, site_jar(conn), [jar_line(1, 5000, at=T0 - 10), jar_line(2, 5000, cur=978)])
    assert counts["skipped"] == 2 and conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 0


def test_a_euro_deposit_matches_the_euro_card_only(conn):
    mono.sync_jars(conn, [mj("J2", "Rainy day", 978, 90000, 0)], T0)
    mono.import_lines(conn, "UAHCARD", [card_line(1, -5000)])             # hryvnias: not the same money
    mono.import_lines(conn, "EURCARD", [card_line(2, -5000, cur=978)])
    mono.import_jar_lines(conn, site_jar(conn, "J2"), [jar_line(1, 5000, cur=978)])
    assert [r["external_id"] for r in conn.execute("SELECT external_id FROM transactions")] == ["EURCARD:c2"]
    assert [r["external_id"] for r in inbox_rows(conn)] == ["UAHCARD:c1"]


def inbox_rows(conn):
    return conn.execute("SELECT external_id FROM inbox").fetchall()


# ---------- The whole run, with a pretend bank ----------
def test_a_run_makes_the_jar_then_follows_its_deposits(conn, monkeypatch):
    clock = {"now": T0}
    monkeypatch.setattr(sync.time, "time", lambda: clock["now"])
    monkeypatch.setenv("MONOBANK_TOKEN", "tok")
    monkeypatch.setenv("MONOBANK_ACCOUNTS", "CARD1")
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(sync, "make_pause", lambda: (lambda: None))
    monkeypatch.setattr(sys, "argv", ["monosync"])
    bank = {"balance": 100000, "calls": []}

    def fake_get(client, token, path):
        bank["calls"].append(path)
        if path == "/personal/client-info":
            return {"accounts": [{"id": "CARD1", "type": "black", "currencyCode": 980, "maskedPan": ["abcdef******0000"]}],
                    "jars": [mj(balance=bank["balance"])]}
        account = path.split("/")[3]
        if account == "CARD1":
            return [card_line(1, -5000)] if clock["now"] > T0 else []
        return [jar_line(1, 5000)] if clock["now"] > T0 else []

    monkeypatch.setattr(sync, "get", fake_get)
    conn.commit()
    sync.main()  # first run: the jar appears, nothing to read from it yet
    with db.connection() as c:
        assert site_jar(c)["opening"] == 100000 and c.execute("SELECT name, currency, kind, balance FROM accounts WHERE mono_id = 'CARD1'").fetchone()["name"] == "Mono Black ·0000 ₴"
    assert not any("/J1/" in call for call in bank["calls"])

    clock["now"] = T0 + 600
    bank["balance"] = 105000  # a deposit of 50 ₴ happened
    sync.main()
    with db.connection() as c:
        tx = c.execute("SELECT * FROM transactions").fetchone()
        assert (tx["jar_id"], tx["direction"], tx["amount"], tx["external_id"]) == (site_jar(c)["id"], "in", 5000, "CARD1:c1")
        assert c.execute("SELECT COUNT(*) FROM inbox").fetchone()[0] == 0
        assert jars.get_jar(c, site_jar(c)["id"])["balance"] == 105000

    bank["calls"].clear()
    clock["now"] = T0 + 1200
    sync.main()  # nothing changed in the jar: its statement is not even asked for
    assert not any("/J1/" in call for call in bank["calls"])
    with db.connection() as c:
        assert c.execute("SELECT COUNT(*) FROM transactions").fetchone()[0] == 1
