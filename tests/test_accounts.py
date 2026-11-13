"""Accounts: Monobank cards (what the bank says) and cash (counted), the Accounts tab, and 'where it came from'."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import botlogic, db, mono, rules, transfers
from app.main import app, updated_at
from monosync import sync

T0 = 1_794_300_000  # 2026-11-10 morning (Kyiv)


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("OWNER_LOGIN", raising=False)
    db.init_db()
    with db.connection() as c:
        yield c


def cat(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def card(conn, mono_id="C1", name="Mono Black ·0648 ₴", currency="UAH", balance=None):
    acc = transfers.ensure_mono_account(conn, mono_id, name, currency)
    if balance is not None:
        transfers.set_card_balance(conn, mono_id, balance, T0)
    return acc


def line(i, amount, desc="x", cur=980, at=T0):
    return {"id": f"s{i}", "time": at, "description": desc, "amount": amount, "currencyCode": cur}


# ---------- The cash accounts ----------
def test_cash_accounts_exist_from_the_start(conn):
    rows = conn.execute("SELECT name, kind, currency FROM accounts ORDER BY id").fetchall()
    assert [(r["name"], r["kind"], r["currency"]) for r in rows] == [("Cash ₴", "cash", "UAH"), ("Cash €", "cash", "EUR")]
    assert transfers.cash_account(conn, "UAH") != transfers.cash_account(conn, "EUR")
    db.init_db()  # a second start makes no more
    assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 2


def test_an_older_database_gets_the_new_columns_cash_and_its_entries_counted(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("FINANCE_DB", str(path))
    db.init_db()
    with sqlite3.connect(path) as raw:  # rebuild the account table the way an earlier version had it
        raw.executescript(
            """
            PRAGMA foreign_keys = OFF;
            DROP TABLE accounts;
            CREATE TABLE accounts (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, mono_id TEXT UNIQUE, archived INTEGER NOT NULL DEFAULT 0);
            INSERT INTO accounts (id, name, mono_id) VALUES (1, 'Cash', NULL), (2, 'Mono Black', 'C1');
            """
        )
        food = raw.execute("SELECT id FROM categories WHERE name = 'Food'").fetchone()[0]
        raw.execute("INSERT INTO transactions (date, kind, category_id, amount, currency, source) VALUES ('2026-11-02','expense',?,500,'UAH','site')", (food,))
        raw.execute("INSERT INTO transactions (date, kind, category_id, amount, currency, source) VALUES ('2026-11-02','expense',?,700,'EUR','telegram')", (food,))
        raw.execute("INSERT INTO transactions (date, kind, category_id, amount, currency, source, external_id) VALUES ('2026-11-02','expense',?,900,'UAH','mono','C1:1')", (food,))
        raw.execute("ALTER TABLE transactions DROP COLUMN from_account_id")
    db.init_db()
    with db.connection() as c:
        accounts = {r["name"]: dict(r) for r in c.execute("SELECT * FROM accounts")}
        assert (accounts["Cash"]["kind"], accounts["Cash"]["currency"]) == ("cash", "UAH")   # the one she had becomes the ₴ cash
        assert accounts["Mono Black"]["kind"] == "card" and accounts["Cash €"]["currency"] == "EUR"
        got = {r["amount"]: r["account_id"] for r in c.execute("SELECT amount, account_id FROM transactions")}
        assert got == {500: accounts["Cash"]["id"], 700: accounts["Cash €"]["id"], 900: None}  # by hand = cash; the bank's line is not
        assert "from_account_id" in {r["name"] for r in c.execute("PRAGMA table_info(transactions)")}
    db.init_db()


# ---------- Entries made by hand are cash ----------
def test_entries_from_the_site_and_the_bot_go_to_cash_of_their_currency(conn):
    conn.commit()
    client = TestClient(app)
    food = cat(conn, "Food")
    client.post("/add", data={"kind": "expense", "category_id": food, "amount": "80", "currency": "UAH", "date": "2026-11-05"})
    client.post("/add", data={"kind": "expense", "category_id": food, "amount": "5", "currency": "EUR", "date": "2026-11-05"})
    with db.connection() as c:
        got = {r["currency"]: r["account_id"] for r in c.execute("SELECT currency, account_id FROM transactions")}
        assert got == {"UAH": transfers.cash_account(c, "UAH"), "EUR": transfers.cash_account(c, "EUR")}
        entry, problem = botlogic.save_entry(c, date(2026, 11, 6), kind="expense", category_id=food, currency="EUR", amount="3")
        assert not problem
        assert c.execute("SELECT account_id FROM transactions WHERE id = ?", (entry["id"],)).fetchone()[0] == transfers.cash_account(c, "EUR")
        tx_id = c.execute("SELECT id FROM transactions WHERE currency = 'UAH'").fetchone()["id"]
    client.post(f"/transactions/{tx_id}/edit", data={"kind": "expense", "category_id": food, "amount": "80", "currency": "EUR", "date": "2026-11-05"})
    with db.connection() as c:
        assert c.execute("SELECT account_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()[0] == transfers.cash_account(c, "EUR")  # moved with its currency


# ---------- What lies in cash ----------
def test_cash_is_its_opening_plus_what_was_recorded_on_it(conn):
    cash = transfers.cash_account(conn, "UAH")
    conn.execute("UPDATE accounts SET opening = 1000000, opening_date = '2026-11-11' WHERE id = ?", (cash,))
    food, salary = cat(conn, "Food"), cat(conn, "Salary")
    jar = conn.execute("INSERT INTO jars (name, currency) VALUES ('Trip', 'UAH')").lastrowid
    other = card(conn)

    def tx(day, kind, amount, **fields):
        cols = {"date": day, "kind": kind, "amount": amount, "currency": "UAH", **fields}
        conn.execute(f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values()))

    tx("2026-11-05", "expense", 90000, category_id=food, account_id=cash)           # before the opening day: not counted
    tx("2026-11-12", "expense", 30000, category_id=food, account_id=cash)           # −300
    tx("2026-11-13", "income", 50000, category_id=salary, account_id=cash)           # +500
    tx("2026-11-14", "transfer", 200000, counterpart="accounts", direction="out", account_id=cash)       # drawn from a card: +2 000
    tx("2026-11-15", "transfer", 100000, counterpart="accounts", direction="in", account_id=other, from_account_id=cash)  # put on a card: −1 000
    tx("2026-11-16", "transfer", 40000, jar_id=jar, direction="in", account_id=cash)  # into a jar by hand: −400
    tx("2026-11-17", "transfer", 10000, jar_id=jar, direction="out", account_id=cash)  # back out: +100
    tx("2026-11-18", "expense", 77700, category_id=food, account_id=other)          # paid with a card: not cash
    account = dict(conn.execute("SELECT * FROM accounts WHERE id = ?", (cash,)).fetchone())
    assert transfers.hand_balance(conn, account) == 1000000 - 30000 + 50000 + 200000 - 100000 - 40000 + 10000


# ---------- Cards: what the bank says ----------
def test_a_card_shows_what_the_bank_says_without_its_credit_limit(conn):
    info = {"accounts": [
        {"id": "C1", "type": "black", "currencyCode": 980, "balance": 1234500, "creditLimit": 0, "maskedPan": ["abcdef******0000"]},
        {"id": "C2", "type": "white", "currencyCode": 978, "balance": 50000, "creditLimit": 20000, "maskedPan": ["abcdef******1111"]},
        {"id": "C3", "type": "black", "currencyCode": 840, "balance": 99, "maskedPan": []},
    ]}
    sync.add_card_accounts(info, ["C1", "C2", "C3"], T0)
    sync.add_card_accounts({"accounts": [{**info["accounts"][0], "balance": 1500000}]}, ["C1"], T0 + 600)  # the next run
    rows = {r["mono_id"]: r for r in conn.execute("SELECT * FROM accounts WHERE mono_id IS NOT NULL")}
    assert (rows["C1"]["currency"], rows["C1"]["kind"], rows["C1"]["balance"], rows["C1"]["balance_at"]) == ("UAH", "card", 1500000, T0 + 600)
    assert (rows["C2"]["currency"], rows["C2"]["balance"]) == ("EUR", 30000)     # 500 € − the 200 € limit
    assert rows["C3"]["currency"] is None                                          # dollars: not tracked
    shown = transfers.accounts_overview(conn)
    assert [r["name"] for r in shown["UAH"]] == ["Mono Black ·0000 ₴", "Cash ₴"] and shown["UAH"][0]["balance"] == 1500000
    assert [r["name"] for r in shown["EUR"]][0].startswith("Mono White")
    assert [r["name"] for rows in shown.values() for r in rows].count("Mono Black") == 0  # the dollar card is not shown


# ---------- The Accounts tab and Settings ----------
def test_the_accounts_tab_lists_cards_and_cash_per_currency(conn):
    card(conn, balance=1234500)
    card(conn, "C2", "Mono Euro ·1111 €", "EUR", balance=45000)
    cash = transfers.cash_account(conn, "UAH")
    conn.execute("UPDATE accounts SET opening = 1000000, opening_date = '2026-01-01' WHERE id = ?", (cash,))
    conn.commit()
    page = TestClient(app).get("/").text.replace(" ", " ").replace(" ", " ")
    panel = page.split('data-panel="accounts"')[1].split('data-panel="categories"')[0]
    assert "Mono Black ·0648 ₴" in panel and "12 345 ₴" in panel and "bank · updated" in panel
    assert "Cash ₴" in panel and "10 000 ₴" in panel and "counted by you" in panel
    assert 'data-tab="accounts"' in page
    assert "Total" not in panel and "All money" not in panel  # a look at the accounts, not a sum


def test_settings_set_the_opening_of_cash_but_not_of_a_card(conn):
    card(conn, balance=100)
    conn.commit()
    client = TestClient(app)
    page = client.get("/settings").text
    assert page.count('name="opening"') == 2  # the two cash accounts; the card has none
    cash = transfers.cash_account(conn, "UAH")
    done = client.post(f"/settings/accounts/{cash}", data={"name": "Cash ₴", "opening": "10 000", "opening_date": "2026-11-11"}, follow_redirects=False)
    assert done.status_code == 303
    with db.connection() as c:
        row = c.execute("SELECT opening, opening_date FROM accounts WHERE id = ?", (cash,)).fetchone()
        assert (row["opening"], row["opening_date"]) == (1000000, "2026-11-11")
    bad = client.post(f"/settings/accounts/{cash}", data={"name": "Cash ₴", "opening": "lots", "opening_date": "2026-11-11"})
    assert bad.status_code == 422 and 'data-open="accounts"' in bad.text
    with db.connection() as c:
        card_id = c.execute("SELECT id FROM accounts WHERE mono_id = 'C1'").fetchone()["id"]
    client.post(f"/settings/accounts/{card_id}", data={"name": "My black", "opening": "999", "opening_date": "2020-01-01"})
    with db.connection() as c:
        row = c.execute("SELECT name, opening, opening_date FROM accounts WHERE id = ?", (card_id,)).fetchone()
        assert (row["name"], row["opening"], row["opening_date"]) == ("My black", 0, None)  # a card keeps no opening
    added = client.post("/settings/accounts/add", data={"name": "Safe", "currency": "EUR", "kind": "cash"}, follow_redirects=False)
    assert added.status_code == 303 and client.post("/settings/accounts/add", data={"name": "X", "currency": "USD"}).status_code == 422


def test_the_time_the_bank_was_asked_reads_short(conn):
    assert updated_at(None) == "" and updated_at(0) == ""
    assert ":" in updated_at(T0) and len(updated_at(T0)) >= 5


# ---------- Where an arrival came from ----------
def test_a_line_that_arrived_on_a_card_can_say_it_came_from_cash(conn):
    cash = transfers.cash_account(conn, "UAH")
    cash_eur = transfers.cash_account(conn, "EUR")
    card(conn, "C1")
    mono.import_lines(conn, "C1", [line(1, 200000, "Cash in")])
    (waiting,) = rules.unsorted(conn)
    assert rules.make_transfer(conn, waiting["id"], "accounts", None, cash_eur) is None    # a euro account can't send hryvnias
    assert rules.unsorted(conn)                                                            # and the line is still waiting
    assert rules.make_transfer(conn, waiting["id"], "accounts", None, cash) is not None
    assert not rules.unsorted(conn)


def test_the_unsorted_page_asks_where_it_went_or_came_from_by_currency(conn):
    card(conn, "C1")
    mono.import_lines(conn, "C1", [line(1, -90000, "ATM"), line(2, 200000, "Cash in"), line(3, -500, "Euro shop", cur=978)])
    conn.commit()
    client = TestClient(app)
    page = client.get("/unsorted").text
    assert "If between my accounts: went to…" in page and "If between my accounts: came from…" in page
    with db.connection() as c:
        ids = {r["comment"]: r["id"] for r in c.execute("SELECT id, comment FROM inbox")}
        cash, cash_eur = transfers.cash_account(c, "UAH"), transfers.cash_account(c, "EUR")
    atm = page.split(f'/unsorted/{ids["ATM"]}"')[1].split("</form>")[0]
    assert f'value="{cash}"' in atm and f'value="{cash_eur}"' not in atm                   # only ₴ accounts, and not the card itself
    assert client.post(f"/unsorted/{ids['ATM']}", data={"action": "accounts", "account_id": ""}).status_code == 422   # where did it go?
    assert client.post(f"/unsorted/{ids['ATM']}", data={"action": "accounts", "account_id": str(cash_eur)}).status_code == 422  # wrong currency
    client.post(f"/unsorted/{ids['ATM']}", data={"action": "accounts", "account_id": str(cash)}, follow_redirects=False)
    client.post(f"/unsorted/{ids['Cash in']}", data={"action": "accounts", "account_id": str(cash)}, follow_redirects=False)
    with db.connection() as c:
        out = c.execute("SELECT * FROM transactions WHERE external_id = 'C1:s1'").fetchone()
        came = c.execute("SELECT * FROM transactions WHERE external_id = 'C1:s2'").fetchone()
        assert (out["account_id"], out["from_account_id"], out["direction"]) == (cash, None, "out")
        card_id = c.execute("SELECT id FROM accounts WHERE mono_id = 'C1'").fetchone()["id"]
        assert (came["account_id"], came["from_account_id"], came["direction"]) == (card_id, cash, "in")
        # drawn 900, put on 2 000: cash changed by +900 − 2 000
        account = dict(c.execute("SELECT * FROM accounts WHERE id = ?", (cash,)).fetchone())
        assert transfers.hand_balance(c, account) == 90000 - 200000
    other = client.post(f"/unsorted/{ids['Euro shop']}", data={"action": "accounts", "account_id": ""}, follow_redirects=False)
    assert other.status_code in (303, 422)


def test_an_arrival_without_a_source_is_allowed(conn):
    card(conn, "C1")
    mono.import_lines(conn, "C1", [line(1, 200000, "Salary-ish")])
    conn.commit()
    (waiting,) = rules.unsorted(conn)
    done = TestClient(app).post(f"/unsorted/{waiting['id']}", data={"action": "accounts", "account_id": ""}, follow_redirects=False)
    assert done.status_code == 303


def test_transfers_read_to_or_from_in_the_list(conn):
    cash = transfers.cash_account(conn, "UAH")
    card(conn, "C1")
    mono.import_lines(conn, "C1", [line(1, -90000, "ATM"), line(2, 200000, "Cash in")])
    atm, came = (r["id"] for r in conn.execute("SELECT id FROM inbox ORDER BY id"))
    rules.make_transfer(conn, atm, "accounts", cash)
    rules.make_transfer(conn, came, "accounts", None, cash)
    conn.commit()
    page = TestClient(app).get("/transactions").text
    assert "To Cash ₴" in page and "From Cash ₴" in page


# ---------- The bot ----------
def test_account_buttons_follow_the_currency_and_skip_the_lines_own_card(conn):
    card(conn, "C1")
    card(conn, "C2", "Mono White ·1111 ₴", "UAH")
    card(conn, "E1", "Mono Euro ·2222 €", "EUR")
    mono.import_lines(conn, "C1", [line(1, -1000, "out"), line(2, 1000, "in", at=T0 + 4 * 86400)])
    out, arrived = (r["id"] for r in conn.execute("SELECT id FROM inbox ORDER BY id"))
    names = lambda rows: [label for row in rows for label, _ in row]
    assert names(botlogic.account_buttons(conn, out)) == ["Cash ₴", "Mono White ·1111 ₴"]               # no Cash €, no Euro card, not C1 itself
    assert names(botlogic.account_buttons(conn, arrived)) == ["Cash ₴", "Mono White ·1111 ₴", "Not one of my accounts"]
    assert all(data.startswith("mc:") for row in botlogic.account_buttons(conn, out) for _, data in row)
    assert all(data.startswith("mf:") for row in botlogic.account_buttons(conn, arrived) for _, data in row)
