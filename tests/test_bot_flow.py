"""The bot's conversation, driven with stand-ins for Telegram (no network, no token)."""

import asyncio
from types import SimpleNamespace

import pytest
from telegram.ext import ApplicationHandlerStop

from app import db
from tgbot import bot


class Msg:
    def __init__(self, text=""):
        self.text, self.sent, self.menus = text, [], []

    async def reply_text(self, text, reply_markup=None, parse_mode=None):
        self.sent.append(text)
        self.menus.append(reply_markup)
        return Msg(text)

    async def edit_text(self, text, reply_markup=None):
        self.text = text


class Query:
    def __init__(self, data, message):
        self.data, self.message, self.shown = data, message, None

    async def answer(self):
        pass

    async def edit_message_text(self, text, reply_markup=None):
        self.shown = text


class Ctx:
    def __init__(self):
        self.user_data = {}
        self.application = SimpleNamespace(bot_data={"chat_id": 42}, create_task=lambda coro: coro.close())


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    db.init_db()
    return Ctx()


def press(ctx, data, msg=None):
    q = Query(data, msg or Msg())
    asyncio.run(bot.on_button(SimpleNamespace(callback_query=q), ctx))
    return q


def say(ctx, text):
    msg = Msg(text)
    asyncio.run(bot.on_text(SimpleNamespace(effective_message=msg), ctx))
    return msg


def start(ctx):
    asyncio.run(bot.add(SimpleNamespace(effective_message=Msg()), ctx))


def rows(sql, *args):
    with db.connection() as c:
        return c.execute(sql, args).fetchall()


def test_expense_flow_then_cancel(env):
    start(env)
    press(env, "t:expense")
    cid = rows("SELECT id FROM categories WHERE name = 'Coffee'")[0]["id"]
    press(env, f"c:{cid}")
    say(env, "latte")
    press(env, "cur:UAH")
    done = say(env, "80")
    assert done.sent[0].replace(" ", " ") == "✓ Coffee · 80 ₴ · latte"
    tx = rows("SELECT * FROM transactions")[0]
    assert (tx["source"], tx["amount"], tx["comment"]) == ("telegram", 8000, "latte")
    q = press(env, f"x:{tx['id']}:{int(__import__('time').time())}", Msg("✓ Coffee · 80 ₴ · latte"))
    assert q.shown.startswith("✕ Cancelled") and not rows("SELECT * FROM transactions")


def test_skip_comment_and_bad_amount_keeps_asking(env):
    start(env)
    press(env, "t:income")
    cid = rows("SELECT id FROM categories WHERE name = 'Salary'")[0]["id"]
    press(env, f"c:{cid}")
    press(env, "skip")
    press(env, "cur:EUR")
    assert say(env, "abc").sent[0].startswith("✗")
    assert say(env, "1500").sent[0].startswith("✓")
    assert rows("SELECT currency, amount FROM transactions")[0]["amount"] == 150000


def test_jar_flow_euro_paid_and_quote(env):
    with db.connection() as c:
        c.execute("INSERT INTO jars (name, currency) VALUES ('Trip', 'EUR')")
    jid = rows("SELECT id FROM jars")[0]["id"]
    start(env)
    press(env, "t:jar")
    press(env, f"j:{jid}")
    press(env, "d:in")
    say(env, "4200")  # paid in hryvnias
    done = say(env, "100")
    assert done.sent[0].startswith("✓ To Trip") and len(done.sent) == 2  # entry + a quote
    tx = rows("SELECT * FROM transactions")[0]
    assert (tx["kind"], tx["direction"], tx["amount"], tx["paid_uah"]) == ("transfer", "in", 10000, 420000)


def test_withdraw_too_much_then_smaller_works(env):
    with db.connection() as c:
        c.execute("INSERT INTO jars (name, currency, opening) VALUES ('Home', 'UAH', 1000)")
    jid = rows("SELECT id FROM jars")[0]["id"]
    start(env)
    press(env, "t:jar")
    press(env, f"j:{jid}")
    press(env, "d:out")
    assert "not that much" in say(env, "500").sent[0]
    assert not rows("SELECT * FROM transactions")
    assert say(env, "4").sent[0].startswith("✓ From Home")


def test_stranger_gets_nothing(env):
    update = SimpleNamespace(effective_chat=SimpleNamespace(id=7))
    with pytest.raises(ApplicationHandlerStop):
        asyncio.run(bot.guard(update, env))
    asyncio.run(bot.guard(SimpleNamespace(effective_chat=SimpleNamespace(id=42)), env))


def test_idle_flow_is_forgotten(env):
    start(env)
    env.user_data["flow"]["started"] -= 11 * 60
    assert "Press Add" in say(env, "80").sent[0]


def test_start_greets_and_shows_menu(env):
    msg = Msg()
    asyncio.run(bot.start(SimpleNamespace(effective_message=msg), env))
    assert msg.sent and msg.sent[0].startswith("Hi") and msg.menus[-1] is bot.MENU


def test_first_question_wording(env):
    msg = Msg()
    asyncio.run(bot.add(SimpleNamespace(effective_message=msg), env))
    assert msg.sent == ["What to add?"]
