"""Network side of the Monobank import.

  python -m monosync             read the cards named in .env and save new lines
  python -m monosync --accounts  list your accounts, to choose which ones to put in .env
  python -m monosync --jars      list your Monobank jars (name, currency, balance, goal)

Monobank allows one statement request per 60 seconds and at most 31 days per request, so the
cards are read one after another with a pause. A card that failed is simply tried again next time.
"""

import os
import sys
import time
from datetime import datetime

import httpx

from app import botlogic, db, mono, transfers
from app.env import load_env

API = "https://api.monobank.ua"
PAUSE = 61  # seconds between two statement requests
CHUNK = 31 * 24 * 3600 - 60
MAX_LINES = 500  # Monobank returns at most this many lines per request
MANY = 5  # more new waiting lines than this: one summary message instead of one message each


class Stop(Exception):
    """The bank asked us to slow down (or is down): leave the rest for the next run."""


def get(client: httpx.Client, token: str, path: str):
    response = client.get(API + path, headers={"X-Token": token}, timeout=30)
    if response.status_code == 429:
        raise Stop("rate limit")
    response.raise_for_status()
    return response.json()


def read_account(client, token: str, account: str, start: int, end: int, pause) -> list[dict]:
    """Every line of one card between two moments (split into allowed pieces, newest page first)."""
    lines, piece_start = [], start
    while piece_start < end:
        piece_end = min(end, piece_start + CHUNK)
        upper = piece_end
        while True:
            pause()
            page = get(client, token, f"/personal/statement/{account}/{piece_start}/{upper}")
            lines += page
            if len(page) < MAX_LINES:
                break
            oldest = min(item["time"] for item in page)
            if oldest >= upper:
                break
            upper = oldest
        piece_start = piece_end
    return lines


def make_pause():
    state = {"last": 0.0}

    def pause():
        wait = PAUSE - (time.time() - state["last"])
        if state["last"] and wait > 0:
            time.sleep(wait)
        state["last"] = time.time()

    return pause


CURRENCY_NAMES = {980: "UAH", 978: "EUR", 840: "USD"}


def money_text(minor) -> str:
    return "-" if minor is None else f"{minor / 100:,.2f}".replace(",", " ")


def list_accounts(token: str, client=None) -> None:
    if client is None:
        with httpx.Client() as own:
            return list_accounts(token, own)
    info = get(client, token, "/personal/client-info")
    print("Accounts (put the ids you want, separated by commas, in MONOBANK_ACCOUNTS):")
    for acc in info.get("accounts", []):
        cur = CURRENCY_NAMES.get(acc.get("currencyCode"), acc.get("currencyCode"))
        card = ", ".join(acc.get("maskedPan") or []) or "-"
        print(f"  {acc['id']}   {acc.get('type', '')}   {cur}   {card}")


def list_jars(token: str, client=None) -> None:
    """The jars ("банки") of the Monobank account: name, currency, what is in it, the goal."""
    if client is None:
        with httpx.Client() as own:
            return list_jars(token, own)
    info = get(client, token, "/personal/client-info")
    jars = info.get("jars", [])
    if not jars:
        print("No jars in Monobank.")
        return
    print("Monobank jars (id, currency, balance, goal, name):")
    for jar in jars:
        cur = CURRENCY_NAMES.get(jar.get("currencyCode"), jar.get("currencyCode"))
        print(f"  {jar['id']}   {cur}   {money_text(jar.get('balance'))}   {money_text(jar.get('goal'))}   {jar.get('title', '')}")


def telegram(client, token: str, chat: str, text: str, rows=None) -> None:
    body = {"chat_id": chat, "text": text}
    if rows:
        body["reply_markup"] = {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}
    client.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body, timeout=30).raise_for_status()


def notify(client) -> None:
    """Tell Aiia in Telegram about new entries that wait for a category."""
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        return
    with db.connection() as conn:
        waiting = [r["id"] for r in conn.execute("SELECT id FROM inbox WHERE notified = 0 ORDER BY date, id")]
        if not waiting:
            return
        if len(waiting) > MANY:
            telegram(client, token, chat, f"{len(waiting)} new bank entries need a category. Send /sort here, or sort them on the site.")
        else:
            for line_id in waiting:
                text, rows = botlogic.sort_message(conn, line_id)
                telegram(client, token, chat, text, rows)
                conn.execute("UPDATE inbox SET notified = 1 WHERE id = ?", (line_id,))
                conn.commit()
        conn.executemany("UPDATE inbox SET notified = 1 WHERE id = ?", [(i,) for i in waiting])


def add_card_accounts(info: dict, accounts: list[str]) -> None:
    """Make an account for every card named in .env that has none yet (named by its card number)."""
    cards = {acc["id"]: acc for acc in info.get("accounts", [])}
    with db.connection() as conn:
        for account in accounts:
            if conn.execute("SELECT 1 FROM accounts WHERE mono_id = ?", (account,)).fetchone():
                continue
            acc = cards.get(account, {})
            cur = {980: "₴", 978: "€"}.get(acc.get("currencyCode"), "")
            card = (acc.get("maskedPan") or [""])[0][-4:]
            name = " ".join(x for x in ("Mono", acc.get("type", "").capitalize(), f"·{card}" if card else "", cur) if x)
            transfers.ensure_mono_account(conn, account, name or f"Mono {account[:4]}")


def log(*parts) -> None:
    print(datetime.now().isoformat(timespec="seconds"), *parts, flush=True)


def main() -> None:
    load_env()
    token = os.environ.get("MONOBANK_TOKEN", "")
    if not token:
        raise SystemExit("Set MONOBANK_TOKEN in .env (see .env.example).")
    if "--accounts" in sys.argv:
        return list_accounts(token)
    if "--jars" in sys.argv:
        return list_jars(token)
    accounts = [a.strip() for a in os.environ.get("MONOBANK_ACCOUNTS", "").split(",") if a.strip()]
    if not accounts:
        raise SystemExit("Set MONOBANK_ACCOUNTS in .env. Run: python -m monosync --accounts")
    db.init_db()
    pause = make_pause()
    now = int(time.time())
    first_of_month = int(datetime.now(mono.TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0).timestamp())
    shown = sys.stdout.isatty()  # run by hand: say what is going on (cron stays quiet)
    totals = {"cards": 0, "new": 0}
    with httpx.Client() as client:
        try:
            if shown:
                print(f"Reading {len(accounts)} card(s). Monobank allows one request a minute, so this takes a few minutes...", flush=True)
            pause()
            info = get(client, token, "/personal/client-info")
            add_card_accounts(info, accounts)
            with db.connection() as conn:  # new Monobank jars become site jars; which jars changed since last time
                jars_to_read = mono.sync_jars(conn, info.get("jars", []), now)
            for account in accounts:
                with db.connection() as conn:
                    last = mono.last_read(conn, account)
                start = (last - 3600) if last else first_of_month  # an hour back: late lines still arrive
                lines = read_account(client, token, account, start, now, pause)
                with db.connection() as conn:
                    counts = mono.import_lines(conn, account, lines)
                    mono.set_last_read(conn, account, now)
                totals["cards"] += 1
                totals["new"] += counts["new"]
                if counts["new"] or shown:
                    log(account[:6], counts)
            # Jars last, so a deposit's card line is already waiting when its jar line arrives.
            for jar, mj in jars_to_read:
                with db.connection() as conn:
                    last = int(mono.get_meta(conn, f"mono_jar_last:{jar['mono_id']}") or now)
                lines = read_account(client, token, jar["mono_id"], last - 3600, now, pause)
                with db.connection() as conn:
                    counts = mono.import_jar_lines(conn, jar, lines)
                    mono.finish_jar(conn, jar["mono_id"], mj.get("balance"), now)
                if counts["paired"] or counts["alone"] or shown:
                    log("jar", jar["name"], counts)
        except Stop as stop:
            print(datetime.now().isoformat(timespec="seconds"), "bank asked to wait:", stop, flush=True)
        except httpx.HTTPError as error:
            print(datetime.now().isoformat(timespec="seconds"), "network problem:", error.__class__.__name__, flush=True)
        try:
            notify(client)
        except httpx.HTTPError as error:
            print(datetime.now().isoformat(timespec="seconds"), "Telegram problem:", error.__class__.__name__, flush=True)
    if shown:
        with db.connection() as conn:
            waiting = conn.execute("SELECT COUNT(*) FROM inbox").fetchone()[0]
        print(f"Done. Cards read: {totals['cards']} of {len(accounts)}. New lines: {totals['new']}. Waiting to sort: {waiting}.")
