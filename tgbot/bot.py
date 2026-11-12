"""Telegram side of the bot (polling). The money logic lives in app/botlogic.py."""

import asyncio
import logging
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application, ApplicationHandlerStop, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
    TypeHandler, filters,
)

from app import botlogic, db, rules
from app.env import load_env
from app.main import CURRENCY_SYMBOLS

log = logging.getLogger("finance-bot")
MENU = ReplyKeyboardMarkup([["Add", "Month", "Jars"]], resize_keyboard=True)
TEXT_STEPS = {"comment", "amount", "paid"}
IDLE_MINUTES = 10


def today():
    try:
        return datetime.now(ZoneInfo(os.environ.get("BOT_TZ", "Europe/Kyiv"))).date()
    except Exception:  # no time zone data on this machine: use the server's own clock
        return datetime.now().date()


def rows(buttons: list, per_row: int) -> list[list]:
    return [buttons[i : i + per_row] for i in range(0, len(buttons), per_row)]


def skip_row(label="Skip"):
    return [Button(label, callback_data="skip")]


def cancel_row():
    return [Button("✕ Stop", callback_data="stop")]


# ---------- Guard: nobody but Aiia gets an answer ----------
async def guard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    if chat is None or chat.id != context.application.bot_data["chat_id"]:
        raise ApplicationHandlerStop


# ---------- The flow ----------
def fresh(context, **flow):
    flow["started"] = time.time()
    context.user_data["flow"] = flow


def current(context):
    flow = context.user_data.get("flow")
    if flow and time.time() - flow["started"] > IDLE_MINUTES * 60:
        context.user_data.pop("flow", None)  # abandoned half-way: forget it
        return None
    if flow:
        flow["started"] = time.time()
    return flow


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("flow", None)
    await update.effective_message.reply_text(
        "Hi, Aiia! 👋 I add entries, show the month and the jars. Pick below.", reply_markup=MENU
    )


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    fresh(context, step="type")
    buttons = [Button("Expense", callback_data="t:expense"), Button("Income", callback_data="t:income"),
               Button("Jar", callback_data="t:jar")]
    await update.effective_message.reply_text("What to add?", reply_markup=Markup([buttons, cancel_row()]))


async def cancel_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("flow", None)
    await update.effective_message.reply_text("Okay, dropped.", reply_markup=MENU)


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db.connection() as conn:
        text = botlogic.month_text(conn, today())
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def jars_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with db.connection() as conn:
        text = botlogic.jars_text(conn)
    await update.effective_message.reply_text(text, parse_mode="HTML")


# ---------- Bank entries that wait for a category ----------
def to_markup(rows):
    return Markup([[Button(label, callback_data=data) for label, data in row] for row in rows])


async def send_next_sort(message, context):
    """Show the oldest waiting entry (not one skipped in this round). Returns False when none is left."""
    skipped = context.user_data.setdefault("skipped", set())
    with db.connection() as conn:
        waiting = rules.unsorted(conn, exclude=skipped)
        ask = botlogic.sort_message(conn, waiting[0]["id"]) if waiting else None
    if not ask:
        context.user_data.pop("sorting", None)
        context.user_data.pop("skipped", None)
        await message.reply_text("Nothing left to sort. ✓", reply_markup=MENU)
        return False
    text, rows = ask
    await message.reply_text(text, reply_markup=to_markup(rows))
    return True


async def sort_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["sorting"] = True
    context.user_data["skipped"] = set()
    await send_next_sort(update.effective_message, context)


def do_sort(conn, kind: str, line_id: int, parts: list[str]):
    """Carry out a sorting button on a waiting line. Returns (summary, label, new id, has text) or None."""
    summary = botlogic.inbox_summary(conn, line_id)
    if not summary:
        return None
    text = conn.execute("SELECT comment FROM inbox WHERE id = ?", (line_id,)).fetchone()["comment"]
    if kind == "m":
        got = rules.sort_entry(conn, line_id, int(parts[2]))
        label, tx_id = (got[0], got[1]) if got else (None, None)
    elif kind == "mp":
        tx_id = rules.make_transfer(conn, line_id, "people")
    else:  # "ma" (money arrived here, or no accounts yet) or "mc" (account chosen)
        tx_id = rules.make_transfer(conn, line_id, "accounts", int(parts[2]) if kind == "mc" else None)
    if not tx_id:
        return None
    if kind != "m":
        label = botlogic.transfer_label(conn, tx_id)
    return summary, label, tx_id, bool(rules.clean_pattern(text))


async def on_sort_button(query, data: str, context):
    parts = data.split(":")
    kind, ref = parts[0], int(parts[1])
    message = query.message
    if kind == "ms":
        context.user_data.setdefault("skipped", set()).add(ref)
        with db.connection() as conn:
            summary = botlogic.inbox_summary(conn, ref)
        await query.edit_message_text(f"Later, on the site: {summary}" if summary else "That entry is gone.")
    elif kind == "ma":
        with db.connection() as conn:
            line = conn.execute("SELECT kind FROM inbox WHERE id = ?", (ref,)).fetchone()
            accounts = botlogic.account_buttons(conn, ref) if line and line["kind"] == "expense" else []
        if accounts:  # money left a card: which of my accounts did it go to?
            return await query.edit_message_text("Which account did the money go to?", reply_markup=to_markup(accounts))
        done = await sorted_and_ask(query, kind, ref, parts)
        if not done:
            return
    elif kind in ("m", "mp", "mc"):
        if not await sorted_and_ask(query, kind, ref, parts):
            return
    elif kind == "ry":
        with db.connection() as conn:
            made = botlogic.remember(conn, ref)
        if made:
            more = f" {made[1]} more sorted." if made[1] else ""
            await query.edit_message_text(f"✓ Rule saved: “{made[0]}”.{more}")
        else:
            await query.edit_message_text("Couldn't save a rule for that.")
    elif kind == "rn":
        await query.edit_message_text("Okay, not remembered.")
    if kind in ("ms", "ma", "m", "mp", "mc") and context.user_data.get("sorting"):
        await send_next_sort(message, context)


async def sorted_and_ask(query, kind, line_id, parts) -> bool:
    with db.connection() as conn:
        result = do_sort(conn, kind, line_id, parts)
    if result is None:
        await query.edit_message_text("Already sorted, or that entry is gone.")
        return False
    summary, label, tx_id, has_text = result
    await query.edit_message_text(f"✓ {summary} → {label}")
    if has_text:
        await query.message.reply_text(
            f"Remember this for next time? Anything like this will be: {label}.",
            reply_markup=Markup([[Button("Yes, remember", callback_data=f"ry:{tx_id}"),
                                  Button("No", callback_data=f"rn:{tx_id}")]]),
        )
    return True


async def ask_category(query, flow):
    with db.connection() as conn:
        cats = botlogic.expense_groups(conn) if flow["kind"] == "expense" else botlogic.income_categories(conn)
    buttons = [Button(c["name"], callback_data=f"c:{c['id']}") for c in cats]
    flow["names"] = {str(c["id"]): c["name"] for c in cats}
    flow["step"] = "category"
    await query.edit_message_text("Which category?", reply_markup=Markup([*rows(buttons, 2), cancel_row()]))


async def ask_currency(query, flow):
    flow["step"] = "currency"
    buttons = [Button(sym, callback_data=f"cur:{code}") for code, sym in CURRENCY_SYMBOLS.items()]
    await query.edit_message_text("Currency?", reply_markup=Markup([buttons, cancel_row()]))


async def on_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    await query.answer()

    if data.startswith("x:"):
        return await on_cancel_entry(query, data)
    if data.split(":")[0] in ("m", "mp", "ma", "mc", "ms", "ry", "rn"):
        return await on_sort_button(query, data, context)

    flow = current(context)
    if data == "stop":
        context.user_data.pop("flow", None)
        return await query.edit_message_text("Okay, dropped.")
    if not flow:
        return await query.edit_message_text("That took too long. Press Add to start again.")

    step = flow["step"]
    if data.startswith("t:") and step == "type":
        flow["kind"] = data[2:]
        if flow["kind"] == "jar":
            with db.connection() as conn:
                items = botlogic.active_jars(conn)
            if not items:
                context.user_data.pop("flow", None)
                return await query.edit_message_text("No jars yet. Create one on the site first.")
            flow["step"] = "jar"
            buttons = [Button(f"{j['name']} {CURRENCY_SYMBOLS[j['currency']]}", callback_data=f"j:{j['id']}") for j in items]
            flow["jars"] = {str(j["id"]): {"name": j["name"], "currency": j["currency"]} for j in items}
            return await query.edit_message_text("Which jar?", reply_markup=Markup([*rows(buttons, 2), cancel_row()]))
        return await ask_category(query, flow)

    if data.startswith("c:") and step == "category":
        flow["category_id"] = data[2:]
        flow["category"] = flow["names"].get(data[2:], "")
        flow["step"] = "comment"
        return await query.edit_message_text(
            f"{flow['category']}. What is it?", reply_markup=Markup([skip_row(), cancel_row()])
        )

    if data == "skip" and step == "comment":
        flow["comment"] = ""
        return await ask_currency(query, flow)

    if data.startswith("cur:") and step == "currency":
        flow["currency"] = data[4:]
        flow["step"] = "amount"
        return await query.edit_message_text(
            f"{flow.get('category', '')} · {CURRENCY_SYMBOLS[flow['currency']]}\nHow much?",
            reply_markup=Markup([cancel_row()]),
        )

    if data.startswith("j:") and step == "jar":
        jar = flow["jars"].get(data[2:])
        if not jar:
            return
        flow.update(jar_id=data[2:], jar_name=jar["name"], currency=jar["currency"], step="direction")
        buttons = [Button("Add money", callback_data="d:in"), Button("Withdraw", callback_data="d:out")]
        return await query.edit_message_text(jar["name"], reply_markup=Markup([buttons, cancel_row()]))

    if data.startswith("d:") and step == "direction":
        flow["direction"] = data[2:]
        if flow["direction"] == "in" and flow["currency"] == "EUR":
            flow["step"] = "paid"
            return await query.edit_message_text(
                "Paid in ₴? Type how many hryvnias you paid, or skip.",
                reply_markup=Markup([skip_row(), cancel_row()]),
            )
        flow["step"] = "amount"
        sym = CURRENCY_SYMBOLS[flow["currency"]]
        return await query.edit_message_text(f"{flow['jar_name']} · {sym}\nHow much?", reply_markup=Markup([cancel_row()]))

    if data == "skip" and step == "paid":
        flow["paid"] = ""
        flow["step"] = "amount"
        return await query.edit_message_text(f"{flow['jar_name']} · €\nHow much?", reply_markup=Markup([cancel_row()]))


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.effective_message.text or "").strip()
    flow = current(context)
    step = flow["step"] if flow else None
    reply = update.effective_message.reply_text

    if step not in TEXT_STEPS:  # nothing is being asked: the text is a menu word, or noise
        low = text.lower()
        if low == "add":
            return await add(update, context)
        if low == "month":
            return await month(update, context)
        if low == "jars":
            return await jars_command(update, context)
        return await reply("Press Add to add something.", reply_markup=MENU)

    if step == "comment":
        flow["comment"] = text
        flow["step"] = "currency"
        buttons = [Button(sym, callback_data=f"cur:{code}") for code, sym in CURRENCY_SYMBOLS.items()]
        return await reply("Currency?", reply_markup=Markup([buttons, cancel_row()]))

    if step == "paid":
        flow["paid"] = text
        flow["step"] = "amount"
        return await reply(f"{flow['jar_name']} · €\nHow much?", reply_markup=Markup([cancel_row()]))

    # step == "amount": save
    with db.connection() as conn:
        entry, problem = botlogic.save_entry(
            conn, today(),
            kind=flow["kind"], amount=text, comment=flow.get("comment", ""), category_id=flow.get("category_id"),
            currency=flow.get("currency", ""), jar_id=flow.get("jar_id"), direction=flow.get("direction", ""),
            paid=flow.get("paid", ""),
        )
    if problem:
        return await reply(f"✗ {problem}")
    context.user_data.pop("flow", None)
    made = time.time()
    message = await reply(f"✓ {entry['text']}", reply_markup=Markup([[Button("Cancel", callback_data=f"x:{entry['id']}:{int(made)}")]]))
    if entry["quote"]:
        await reply(entry["quote"], reply_markup=MENU)
    else:
        await reply("Saved.", reply_markup=MENU)
    context.application.create_task(expire_cancel(message, entry["text"]))


def edit_button():
    url = os.environ.get("SITE_URL", "").strip()
    return Markup([[Button("Edit on site", url=url)]]) if url.startswith("https://") else None


async def expire_cancel(message, text: str):
    """After an hour the Cancel button turns into "Edit on site"."""
    await asyncio.sleep(botlogic.CANCEL_WINDOW)
    try:
        await message.edit_text(f"✓ {text}\nEdit on the site if it needs changing.", reply_markup=edit_button())
    except Exception:  # already cancelled, or the message is gone
        pass


async def on_cancel_entry(query, data: str):
    try:
        _, tx_id, made = data.split(":")
        tx_id, made = int(tx_id), float(made)
    except ValueError:
        return
    with db.connection() as conn:
        result = botlogic.cancel_entry(conn, tx_id, made)
    shown = (query.message.text or "").removeprefix("✓ ").split("\n")[0]
    if result == "ok":
        await query.edit_message_text(f"✕ Cancelled: {shown}")
    elif result == "late":
        await query.edit_message_text(f"✓ {shown}\nToo late to cancel here. Edit on the site.", reply_markup=edit_button())
    elif result == "gone":
        await query.edit_message_text(f"✕ Already gone: {shown}")
    else:
        await query.message.reply_text(f"✗ {result}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    load_env()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat.lstrip("-").isdigit():
        raise SystemExit("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env (see .env.example).")
    db.init_db()
    app = Application.builder().token(token).build()
    app.bot_data["chat_id"] = int(chat)
    app.add_handler(TypeHandler(Update, guard), group=-1)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("jars", jars_command))
    app.add_handler(CommandHandler("sort", sort_command))
    app.add_handler(CommandHandler("cancel", cancel_flow))
    app.add_handler(CallbackQueryHandler(on_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.run_polling(allowed_updates=["message", "callback_query"])
