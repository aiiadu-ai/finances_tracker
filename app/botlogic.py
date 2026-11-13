"""What the Telegram bot does with the database. No Telegram code here, so it can be tested alone.

It uses the same checks as the website (`validate`), so a jar can't go below zero and amounts are
read the same way. Hryvnias and euros are never mixed.
"""

import html
import time
from datetime import date

from . import db, jars, monthly, rules, transfers, wealth
from .main import CURRENCY_SYMBOLS, TX_SELECT, load_categories, money, validate

CANCEL_WINDOW = 3600  # seconds: "Cancel" deletes the entry for one hour, then only the site edits it


def expense_groups(conn) -> list[dict]:
    """Expense categories for the buttons (already in group order)."""
    return [dict(r) for r in load_categories(conn)["expense"]]


def income_categories(conn) -> list[dict]:
    return [dict(r) for r in load_categories(conn)["income"]]


def active_jars(conn) -> list[dict]:
    return jars.list_jars(conn)


def save_entry(conn, today: date, *, kind: str, amount: str, comment: str = "", category_id=None,
               currency: str = "", jar_id=None, direction: str = "", paid: str = "") -> tuple[dict | None, str]:
    """Write one entry. Returns (entry, "") or (None, what is wrong). entry has id, title, text, quote."""
    form = {
        "kind": kind,
        "category_id": str(category_id or ""),
        "amount": amount.strip(),
        "currency": currency,
        "comment": comment.strip(),
        "date": today.isoformat(),
        "jar_id": str(jar_id or ""),
        "direction": direction,
        "paid": paid.strip(),
    }
    clean, errors = validate(conn, form)
    if errors:
        return None, next(iter(errors.values()))
    cursor = conn.execute(
        """
        INSERT INTO transactions (date, kind, category_id, amount, currency, comment, source, jar_id, direction, paid_uah)
        VALUES (:date, :kind, :category_id, :amount, :currency, :comment, 'telegram', :jar_id, :direction, :paid_uah)
        """,
        clean,
    )
    low = jars.negative(conn, [clean["jar_id"]])
    if low:
        conn.rollback()
        return None, f"There is not that much in {low}."
    row = conn.execute(f"{TX_SELECT} WHERE t.id = ?", (cursor.lastrowid,)).fetchone()
    parts = [row["title"], money(row["amount"], row["currency"])]
    if row["paid_uah"]:
        parts.append(f"paid {money(row['paid_uah'], 'UAH')}")
    if row["comment"]:
        parts.append(row["comment"])
    quote = jars.pick_quote(conn, row["jar_id"]) if row["direction"] == "in" else None
    return {"id": row["id"], "title": row["title"], "text": " · ".join(parts), "quote": quote}, ""


def cancel_entry(conn, tx_id: int, made_at: float, now: float | None = None) -> str:
    """Delete an entry the bot made, within an hour. Returns "ok", "late", "gone" or a reason."""
    now = time.time() if now is None else now
    row = conn.execute("SELECT id, source, jar_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    if row is None or row["source"] != "telegram":
        return "gone"
    if now - made_at > CANCEL_WINDOW:
        return "late"
    conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
    low = jars.negative(conn, [row["jar_id"]])
    if low:
        conn.rollback()
        return f"Can't cancel: {low} would drop below zero."
    return "ok"


def month_text(conn, today: date) -> str:
    """The short month summary for /month: per currency the month's income and expenses (money sent to people is
    an expense, money received from people is income), what lies in the jars, and Free: the money that is
    not in jars but is there (see app/wealth.py)."""
    first = today.replace(day=1)
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    sums = {
        (r["kind"], r["currency"]): r["total"]
        for r in conn.execute(
            """
            SELECT kind, currency, SUM(amount) AS total FROM transactions
            WHERE date >= ? AND date < ? AND kind IN ('income', 'expense') GROUP BY kind, currency
            """,
            (first.isoformat(), following.isoformat()),
        )
    }
    people = transfers.people_flows(conn, first, following)
    standing = wealth.standing(conn, wealth.get_start(conn), first, today)
    names = {"UAH": "₴ hryvnas", "EUR": "€ euros"}
    blocks = []
    for code in monthly.CURRENCIES:
        income = sums.get(("income", code), 0) + people[code]["received"]
        expense = sums.get(("expense", code), 0) + people[code]["sent"]
        in_jars = standing["in_jars"][code]
        free = standing["free"][code] if standing["free"] else None
        if code == "EUR" and not (income or expense or in_jars or free):
            continue  # euros show up only when there are some
        if free is None:
            last = "Free: set your starting money on the site (Settings → Money)"
        else:
            last = f"{'✓' if free >= 0 else '✗'} <b>Free: {money(free, code)}</b>"
        blocks.append(
            "\n".join(
                [
                    names[code],
                    "",
                    f"<b>Income:</b> {money(income, code)}",
                    f"<b>Expenses:</b> {money(expense, code)}",
                    "",
                    f"<b>In jars:</b> {money(in_jars, code)}",
                    "━━━━━━━━━━",
                    last,
                ]
            )
        )
    return f"<b>{first:%B %Y}</b>\n" + "\n\n".join(blocks)


def jars_text(conn) -> str:
    """For /jars (HTML): only the jar's name is bold."""
    items = jars.list_jars(conn)
    if not items:
        return "No jars yet. Create one on the site."
    lines = []
    for jar in items:
        line = f"<b>{html.escape(jar['name'])}</b>: {money(jar['balance'], jar['currency'])}"
        if jar["target"]:
            line += f" of {money(jar['target'], jar['currency'])} · " + (
                "✓ goal reached" if jar["reached"] else f"{jar['pct']}%"
            )
        lines.append(line)
    return "\n".join(lines)


# ---------- Bank lines that wait for a category (the inbox) ----------
def sort_message(conn, inbox_id: int) -> tuple[str, list[list[tuple[str, str]]]] | None:
    """The text and buttons (label, callback data) that ask for a category. None if the line is gone."""
    line = conn.execute("SELECT id, date, kind, amount, currency, comment FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if line is None:
        return None
    cats = load_categories(conn)[line["kind"]]
    sign = "+" if line["kind"] == "income" else "−"
    what = line["comment"] or "(no description)"
    text = f"Mono · {sign}{money(line['amount'], line['currency'])} · {what}\nWhich category?"
    buttons = [(c["name"], f"m:{line['id']}:{c['id']}") for c in cats]
    rows = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    people = "Received from people" if line["kind"] == "income" else "Sent to people"
    rows.append([(people, f"mp:{line['id']}")])
    rows.append([("Between my accounts", f"ma:{line['id']}")])
    rows.append([("Skip, later on site", f"ms:{line['id']}")])
    return text, rows


def account_buttons(conn, inbox_id: int) -> list[list[tuple[str, str]]]:
    """Buttons for 'which account did the money go to?'."""
    buttons = [(a["name"], f"mc:{inbox_id}:{a['id']}") for a in transfers.list_accounts(conn)]
    return [buttons[i : i + 2] for i in range(0, len(buttons), 2)]


def inbox_summary(conn, inbox_id: int) -> str:
    row = conn.execute("SELECT kind, amount, currency, comment FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        return ""
    sign = "+" if row["kind"] == "income" else "−"
    return f"{sign}{money(row['amount'], row['currency'])} · {row['comment'] or '(no description)'}"


def transfer_label(conn, tx_id: int) -> str:
    row = conn.execute(f"{TX_SELECT} WHERE t.id = ?", (tx_id,)).fetchone()
    return row["title"] if row else ""


def remember(conn, tx_id: int) -> tuple[str, int] | None:
    return rules.remember(conn, tx_id)


def make_db() -> None:
    db.init_db()
