"""Monobank import, the waiting list (inbox), rules, transfers to people / between accounts, the site
pages for them and the bot's sorting buttons."""

import asyncio
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import botlogic, db, jars, mono, rules, transfers
from app.main import app
from tgbot import bot

T0 = 1_794_300_000  # 2026-11-10 ~ 10:00 in Kyiv
DAY = "2026-11-10"
NOV = (date(2026, 11, 1), date(2026, 12, 1))


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    db.init_db()
    with db.connection() as c:
        yield c


def line(i, amount, desc="Silpo", cur=980, at=T0):
    return {"id": f"s{i}", "time": at, "description": desc, "amount": amount, "currencyCode": cur}


def cat(conn, name):
    return conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()["id"]


def txs(conn):
    return conn.execute("SELECT * FROM transactions ORDER BY id").fetchall()


def inbox(conn):
    return conn.execute("SELECT * FROM inbox ORDER BY id").fetchall()


def flat(text):
    return text.replace("\u00a0", " ").replace("\u202f", " ")


# ---------- Import ----------
def test_lines_wait_in_the_inbox_and_never_duplicate(conn):
    counts = mono.import_lines(conn, "A", [line(1, -25000), line(2, 100000, "Salary Inc")])
    assert counts["new"] == 2 and counts["waiting"] == 2
    assert txs(conn) == []  # nothing is a transaction until it is sorted
    first, second = inbox(conn)
    assert (first["kind"], first["amount"], first["currency"], first["date"], first["external_id"]) == ("expense", 25000, "UAH", DAY, "A:s1")
    assert second["kind"] == "income"
    assert mono.import_lines(conn, "A", [line(1, -25000)])["new"] == 0
    assert len(inbox(conn)) == 2


def test_waiting_lines_are_not_counted_in_totals(conn):
    mono.import_lines(conn, "A", [line(1, -25000)])
    conn.commit()
    html = flat(TestClient(app).get("/?month=2026-11").text)
    assert "1</strong> bank entry to sort" in html and "not counted until sorted" in html
    assert "−250" not in html


def test_euro_kept_and_other_currencies_skipped(conn):
    counts = mono.import_lines(conn, "A", [line(1, -500, cur=978), line(2, -500, cur=840), line(3, 0)])
    assert counts["new"] == 1 and counts["skipped"] == 2
    assert inbox(conn)[0]["currency"] == "EUR"


# ---------- Rules ----------
def test_rule_sorts_new_lines_straight_into_transactions(conn):
    rules.save_rule(conn, "SILPO", cat(conn, "Food"))
    counts = mono.import_lines(conn, "A", [line(1, -25000, "Silpo Kyiv #12")])
    assert counts["sorted"] == 1 and inbox(conn) == []
    tx = txs(conn)[0]
    assert (tx["category_id"], tx["source"], tx["external_id"], tx["kind"]) == (cat(conn, "Food"), "mono", "A:s1", "expense")


def test_rule_only_fits_its_own_kind_and_longest_wins(conn):
    rules.save_rule(conn, "market", cat(conn, "Food"))
    rules.save_rule(conn, "market refund", None, "people")
    assert rules.find(conn, "Market Refund 5", "income")["transfer"] == "people"
    assert rules.find(conn, "my MARKET", "income") is None  # an expense rule never sorts income
    assert rules.find(conn, "my MARKET", "expense")["category_id"] == cat(conn, "Food")


def test_cyrillic_rule_ignores_case_and_archived_category_is_ignored(conn):
    rules.save_rule(conn, "сільпо", cat(conn, "Food"))
    assert rules.find(conn, "СІЛЬПО Київ", "expense")
    conn.execute("UPDATE categories SET archived = 1 WHERE name = 'Food'")
    assert rules.find(conn, "СІЛЬПО Київ", "expense") is None


def test_transfer_rules_to_people_and_to_an_account(conn):
    mine = conn.execute("INSERT INTO accounts (name) VALUES ('Cash')").lastrowid
    rules.save_rule(conn, "Anna", None, "people")
    rules.save_rule(conn, "To cash", None, "accounts", mine)
    mono.import_lines(conn, "A", [line(1, -100000, "Transfer to Anna"), line(2, -50000, "To cash desk")])
    first, second = txs(conn)
    assert (first["kind"], first["counterpart"], first["direction"], first["account_id"], first["category_id"]) == ("transfer", "people", "out", None, None)
    assert (second["counterpart"], second["account_id"]) == ("accounts", mine)


# ---------- Transfers between her own cards ----------
def test_same_amount_between_two_cards_is_a_transfer_into_the_receiving_account(conn):
    a = transfers.ensure_mono_account(conn, "A", "Mono black")
    b = transfers.ensure_mono_account(conn, "B", "Mono white")
    mono.import_lines(conn, "A", [line(1, -300000, "To card", at=T0)])
    counts = mono.import_lines(conn, "B", [line(2, 300000, "From card", at=T0 + 20)])
    assert counts["transfers"] == 1 and inbox(conn) == []
    out, inc = txs(conn)
    assert (out["kind"], out["counterpart"], out["direction"], out["account_id"]) == ("transfer", "accounts", "out", b)
    assert (inc["kind"], inc["counterpart"], inc["direction"], inc["account_id"]) == ("transfer", "accounts", "in", b)
    assert all(t["category_id"] is None and t["jar_id"] is None for t in txs(conn))
    assert a != b


def test_same_card_or_other_amount_is_not_paired(conn):
    mono.import_lines(conn, "A", [line(1, -300000), line(2, 300000, "Refund")])
    mono.import_lines(conn, "B", [line(3, 250000, "Else")])
    assert len(inbox(conn)) == 3 and txs(conn) == []


# ---------- Sorting ----------
def test_sort_moves_a_line_into_transactions(conn):
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo 1"), line(2, -700, "Mystery")])
    a, b = (r["id"] for r in inbox(conn))
    assert rules.sort_entry(conn, b, cat(conn, "Salary")) is None  # an income category for an expense
    name, tx_id = rules.sort_entry(conn, a, cat(conn, "Food"))
    assert name == "Food" and rules.sort_entry(conn, a, cat(conn, "Food")) is None  # gone from the inbox
    assert txs(conn)[0]["id"] == tx_id and [r["id"] for r in inbox(conn)] == [b]


def test_people_transfers_count_separately_and_reduce_free(conn):
    mono.import_lines(conn, "A", [line(1, 200000, "From mum"), line(2, -50000, "To friend"), line(3, 1000000, "Salary")])
    ids = {r["comment"]: r["id"] for r in inbox(conn)}
    rules.make_transfer(conn, ids["From mum"], "people")
    rules.make_transfer(conn, ids["To friend"], "people")
    rules.sort_entry(conn, ids["Salary"], cat(conn, "Salary"))
    flows = transfers.people_flows(conn, *NOV)
    assert flows["UAH"] == {"sent": 50000, "received": 200000} and flows["EUR"] == {"sent": 0, "received": 0}
    conn.commit()
    client = TestClient(app)
    page = flat(client.get("/?month=2026-11").text)
    assert "Sent to people" in page and "Received from people" in page
    # income 10 000 − 0 expenses − 0 jars − 500 sent + 2 000 received
    assert "11 500 ₴" in page
    titles = flat(client.get("/transactions").text)
    assert "Sent to people" in titles and "Received from people" in titles
    assert jars.month_flows(conn, *NOV) == {"UAH": 0, "EUR": 0}  # not jar money
    text = flat(botlogic.month_text(conn, date(2026, 11, 10)))
    assert "<b>Sent to people:</b> 500 ₴" in text and "<b>Received from people:</b> 2 000 ₴" in text
    assert "✓ <b>Free: 11 500 ₴</b>" in text


def test_accounts_transfer_needs_a_real_account_and_income_side_uses_its_own_card(conn):
    card = transfers.ensure_mono_account(conn, "A", "Mono black")
    cash = conn.execute("INSERT INTO accounts (name) VALUES ('Cash')").lastrowid
    gone = conn.execute("INSERT INTO accounts (name, archived) VALUES ('Old', 1)").lastrowid
    mono.import_lines(conn, "A", [line(1, -10000, "Out"), line(2, 20000, "In")])
    out_id, in_id = (r["id"] for r in inbox(conn))
    assert rules.make_transfer(conn, out_id, "accounts", gone) is None  # archived accounts can't be chosen
    tx = rules.make_transfer(conn, out_id, "accounts", cash)
    assert conn.execute("SELECT account_id FROM transactions WHERE id = ?", (tx,)).fetchone()[0] == cash
    tx = rules.make_transfer(conn, in_id, "accounts", cash)  # arrived on this card, whatever was passed
    assert conn.execute("SELECT account_id FROM transactions WHERE id = ?", (tx,)).fetchone()[0] == card


def test_plain_transfer_pages_work_and_have_no_edit(conn):
    mono.import_lines(conn, "A", [line(1, -300000, "To card")])
    rules.make_transfer(conn, inbox(conn)[0]["id"], "people")
    conn.commit()
    client = TestClient(app)
    tx_id = txs(conn)[0]["id"]
    assert client.get("/transactions").status_code == 200
    detail = client.get(f"/transactions?tx={tx_id}")
    assert detail.status_code == 200 and f"/transactions/{tx_id}/edit" not in detail.text
    assert client.get(f"/transactions/{tx_id}/edit", follow_redirects=False).status_code == 303


def test_remember_makes_rules_for_categories_and_transfers(conn):
    cash = conn.execute("INSERT INTO accounts (name) VALUES ('Cash')").lastrowid
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo 1"), line(2, -9000, "Silpo 1 again"), line(3, -700, "To Cash X"),
                                  line(4, -800, "To Cash X")])
    ids = {r["comment"]: r["id"] for r in inbox(conn)}
    _, tx = rules.sort_entry(conn, ids["Silpo 1"], cat(conn, "Food"))
    assert rules.remember(conn, tx) == ("Silpo 1", 1)  # "Silpo 1 again" matches too
    tx = rules.make_transfer(conn, ids["To Cash X"], "accounts", cash)
    assert rules.remember(conn, tx)[0] == "To Cash X"
    rule = rules.find(conn, "to cash x", "expense")
    assert (rule["transfer"], rule["account_id"]) == ("accounts", cash)
    assert inbox(conn) == []


def test_sort_message_has_categories_people_accounts_and_skip(conn):
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo"), line(2, 5000, "Back")])
    out, back = (r["id"] for r in inbox(conn))
    text, buttons = botlogic.sort_message(conn, out)
    labels = [label for row in buttons for label, _ in row]
    assert "−250" in flat(text) and "Silpo" in text
    assert "Food" in labels and "Salary" not in labels
    assert labels[-3:] == ["Sent to people", "Between my accounts", "Skip, later on site"]
    assert "Received from people" in [label for row in botlogic.sort_message(conn, back)[1] for label, _ in row]


# ---------- Migration of an older database ----------
def test_stage7_database_moves_waiting_lines_to_the_inbox(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("FINANCE_DB", str(path))
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE categories (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, name TEXT NOT NULL, "group" TEXT, archived INTEGER NOT NULL DEFAULT 0, UNIQUE (kind, name));
        INSERT INTO categories (kind, name, "group") VALUES ('expense', 'Food', 'essentials');
        CREATE TABLE jars (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, currency TEXT NOT NULL, target INTEGER, my_rate INTEGER, quote TEXT, opening INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY, date TEXT NOT NULL, kind TEXT NOT NULL, category_id INTEGER REFERENCES categories (id),
            amount INTEGER NOT NULL CHECK (amount > 0), currency TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'site', external_id TEXT UNIQUE, jar_id INTEGER, direction TEXT, paid_uah INTEGER,
            notified INTEGER NOT NULL DEFAULT 0,
            CHECK (kind <> 'transfer' OR category_id IS NULL));
        INSERT INTO transactions (id, date, kind, category_id, amount, currency, comment) VALUES (7, '2026-11-01', 'expense', 1, 500, 'UAH', 'kept');
        INSERT INTO transactions (id, date, kind, amount, currency, comment, source, external_id, notified) VALUES (8, '2026-11-02', 'expense', 900, 'UAH', 'Waiting', 'mono', 'A:1', 1);
        INSERT INTO transactions (id, date, kind, amount, currency, comment, source, external_id) VALUES (9, '2026-11-02', 'transfer', 5000, 'UAH', 'card to card', 'mono', 'A:2');
        CREATE TABLE rules (id INTEGER PRIMARY KEY, pattern TEXT NOT NULL, key TEXT NOT NULL UNIQUE, category_id INTEGER REFERENCES categories (id), is_transfer INTEGER NOT NULL DEFAULT 0, CHECK ((is_transfer = 1) = (category_id IS NULL)));
        INSERT INTO rules (pattern, key, is_transfer) VALUES ('to card', 'to card', 1);
        """
    )
    old.commit()
    old.close()
    db.init_db()
    with db.connection() as c:
        got = {r["id"]: r for r in txs(c)}
        assert set(got) == {7, 9} and got[7]["comment"] == "kept"
        assert (got[9]["kind"], got[9]["counterpart"]) == ("transfer", "accounts")
        waiting = inbox(c)[0]
        assert (waiting["comment"], waiting["external_id"], waiting["notified"]) == ("Waiting", "A:1", 1)
        assert rules.find(c, "To card 4411", "expense")["transfer"] == "accounts"  # an old transfer rule
        with pytest.raises(sqlite3.IntegrityError):  # an expense without a category is not allowed again
            c.execute("INSERT INTO transactions (date, kind, amount, currency) VALUES ('2026-11-02','expense',100,'UAH')")
        with pytest.raises(sqlite3.IntegrityError):  # a transfer must say whom it is with
            c.execute("INSERT INTO transactions (date, kind, amount, currency) VALUES ('2026-11-02','transfer',100,'UAH')")
    db.init_db()  # a second start changes nothing
    with db.connection() as c:
        assert len(txs(c)) == 2 and len(inbox(c)) == 1


# ---------- Site ----------
def test_unsorted_page_and_actions(conn):
    cash = conn.execute("INSERT INTO accounts (name) VALUES ('Cash')").lastrowid
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo 1"), line(2, -900, "Silpo 2"), line(3, -700, "Odd"), line(4, -600, "Friend"),
                                  line(5, -500, "Moved")])
    conn.commit()
    a, b, c, d, e = (r["id"] for r in inbox(conn))
    client = TestClient(app)
    page = client.get("/unsorted")
    assert page.status_code == 200 and "Silpo 1" in page.text and "Sent to people" in page.text and ">Cash<" in page.text
    assert client.post(f"/unsorted/{a}", data={"action": "sort", "category_id": ""}).status_code == 422
    assert client.post(f"/unsorted/{e}", data={"action": "accounts", "account_id": ""}).status_code == 422  # must say where
    client.post(f"/unsorted/{a}", data={"action": "sort", "category_id": str(cat(conn, "Food")), "remember": "1"}, follow_redirects=False)
    client.post(f"/unsorted/{d}", data={"action": "people"}, follow_redirects=False)
    client.post(f"/unsorted/{e}", data={"action": "accounts", "account_id": str(cash)}, follow_redirects=False)
    with db.connection() as x:
        got = {t["comment"]: t for t in txs(x)}
        assert got["Silpo 1"]["category_id"] == cat(x, "Food") and got["Friend"]["counterpart"] == "people"
        assert (got["Moved"]["counterpart"], got["Moved"]["account_id"]) == ("accounts", cash)
        assert {r["comment"] for r in inbox(x)} == {"Silpo 2", "Odd"}  # "Silpo 1" rule does not fit "Silpo 2"
    assert client.post(f"/unsorted/{a}", data={"action": "people"}, follow_redirects=False).status_code == 303  # already gone


def test_settings_accounts_and_rules(conn):
    conn.commit()
    client = TestClient(app)
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo 1")])
    conn.commit()
    assert client.post("/settings/accounts/add", data={"name": "Agricole"}, follow_redirects=False).status_code == 303
    assert client.post("/settings/accounts/add", data={"name": "agricole"}).status_code == 422  # same name
    assert client.post("/settings/accounts/add", data={"name": " "}).status_code == 422
    with db.connection() as d:
        acc = d.execute("SELECT id FROM accounts").fetchone()["id"]
    client.post(f"/settings/accounts/{acc}", data={"name": "Agricole EUR"})
    page = client.get("/settings").text
    assert "Agricole EUR" in page and f'value="account:{acc}"' in page
    client.post(f"/settings/accounts/{acc}/archive")
    assert f'value="account:{acc}"' not in client.get("/settings").text
    assert client.post("/settings/rules/add", data={"pattern": "silpo", "target": str(cat(conn, "Food"))}, follow_redirects=False).status_code == 303
    assert client.post("/settings/rules/add", data={"pattern": "", "target": "people"}).status_code == 422
    assert client.post("/settings/rules/add", data={"pattern": "x", "target": "999"}).status_code == 422
    with db.connection() as d:
        assert inbox(d) == []  # the new rule sorted the waiting line
        rid = d.execute("SELECT id FROM rules").fetchone()["id"]
    client.post(f"/settings/rules/{rid}", data={"pattern": "Moving money", "target": "people"})
    page = client.get("/settings").text
    assert "Moving money" in page and 'value="people" selected' in page
    client.post(f"/settings/rules/{rid}/delete")
    assert "Moving money" not in client.get("/settings").text


# ---------- The bot's sorting buttons ----------
class Msg:
    def __init__(self):
        self.sent = []

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        self.sent.append(text)
        return Msg()


class Query:
    def __init__(self, data):
        self.data, self.message, self.shown, self.markup = data, Msg(), None, None

    async def answer(self):
        pass

    async def edit_message_text(self, text, reply_markup=None):
        self.shown, self.markup = text, reply_markup


class Ctx:
    def __init__(self):
        self.user_data = {}
        self.application = SimpleNamespace(bot_data={"chat_id": 42}, create_task=lambda coro: coro.close())


def press(ctx, data):
    q = Query(data)
    asyncio.run(bot.on_button(SimpleNamespace(callback_query=q), ctx))
    return q


def test_bot_category_remember_and_skip(conn):
    mono.import_lines(conn, "A", [line(1, -25000, "Silpo 1"), line(2, -900, "Odd")])
    conn.commit()
    a, b = (r["id"] for r in inbox(conn))
    ctx = Ctx()
    q = press(ctx, f"m:{a}:{cat(conn, 'Food')}")
    assert flat(q.shown).startswith("✓ −250 ₴ · Silpo 1 → Food") and "Remember" in q.message.sent[0]
    assert "Already sorted" in press(ctx, f"m:{a}:{cat(conn, 'Coffee')}").shown
    with db.connection() as d:
        tx_id = txs(d)[0]["id"]
    assert "Rule saved" in press(ctx, f"ry:{tx_id}").shown
    assert "Later" in press(ctx, f"ms:{b}").shown
    with db.connection() as d:
        assert d.execute("SELECT COUNT(*) FROM rules").fetchone()[0] == 1 and [r["id"] for r in inbox(d)] == [b]


def test_bot_people_and_account_choice(conn):
    cash = conn.execute("INSERT INTO accounts (name) VALUES ('Cash')").lastrowid
    mono.import_lines(conn, "A", [line(1, -25000, "To Anna"), line(2, -9000, "To cash"), line(3, 5000, "From cash")])
    conn.commit()
    anna, tocash, fromcash = (r["id"] for r in inbox(conn))
    ctx = Ctx()
    assert "→ Sent to people" in press(ctx, f"mp:{anna}").shown
    ask = press(ctx, f"ma:{tocash}")  # money left a card: which account?
    assert ask.shown == "Which account did the money go to?"
    assert [b.callback_data for row in ask.markup.inline_keyboard for b in row] == [f"mc:{tocash}:{cash}"]
    assert "→ To Cash" in press(ctx, f"mc:{tocash}:{cash}").shown
    assert "→ To " in press(ctx, f"ma:{fromcash}").shown  # arrived here: no question
    with db.connection() as d:
        assert inbox(d) == [] and {t["counterpart"] for t in txs(d)} == {"people", "accounts"}


def test_bot_sort_command_walks_through(conn):
    mono.import_lines(conn, "A", [line(1, -25000, "One"), line(2, -900, "Two")])
    conn.commit()
    a, b = (r["id"] for r in inbox(conn))
    ctx, msg = Ctx(), Msg()
    asyncio.run(bot.sort_command(SimpleNamespace(effective_message=msg), ctx))
    assert "One" in msg.sent[0]
    q = press(ctx, f"mp:{a}")
    assert any("Two" in t for t in q.message.sent)
    q = press(ctx, f"mp:{b}")
    assert any("Nothing left" in t for t in q.message.sent) and "sorting" not in ctx.user_data
