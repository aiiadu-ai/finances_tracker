"""Monobank statement lines -> entries. No network here (see monosync/); it only reads lines and writes rows.

Plus is income, minus is expense, in the card's own currency (₴ or €; other currencies are skipped,
nothing is ever converted). Each line has the bank's own id, so a line is never saved twice.
A line goes through Aiia's rules; if none fits it waits in the inbox, where nothing counts it yet.
If the same amount leaves one of her cards and arrives on another, both are transfers between accounts.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from . import jars as jar_module, rules, transfers

CURRENCY_CODES = {980: "UAH", 978: "EUR"}
TZ = ZoneInfo("Europe/Kyiv")


def external_id(account: str, line_id: str) -> str:
    return f"{account}:{line_id}"


def find_partner(conn, account: str, kind: str, amount: int, currency: str, day: str):
    """A waiting line from another card that is the other half of this transfer (same amount, a day apart at most)."""
    other = "income" if kind == "expense" else "expense"
    return conn.execute(
        """
        SELECT id, external_id FROM inbox
        WHERE kind = ? AND amount = ? AND currency = ? AND external_id NOT LIKE ?
              AND abs(julianday(date) - julianday(?)) <= 1
        ORDER BY id LIMIT 1
        """,
        (other, amount, currency, f"{account}:%", day),
    ).fetchone()


def import_lines(conn, account: str, lines: list[dict]) -> dict:
    """Save new statement lines. Returns counts: new, sorted, transfers, waiting, skipped."""
    counts = {"new": 0, "sorted": 0, "transfers": 0, "waiting": 0, "skipped": 0}
    for line in sorted(lines, key=lambda x: x["time"]):
        currency = CURRENCY_CODES.get(line.get("currencyCode"))
        signed = line.get("amount", 0)
        if currency is None or signed == 0:
            counts["skipped"] += 1
            continue
        ext = external_id(account, line["id"])
        if conn.execute("SELECT 1 FROM transactions WHERE external_id = ? UNION SELECT 1 FROM inbox WHERE external_id = ?", (ext, ext)).fetchone():
            continue
        kind, amount = ("income", signed) if signed > 0 else ("expense", -signed)
        day = datetime.fromtimestamp(line["time"], TZ).date().isoformat()
        description = " ".join((line.get("description") or line.get("comment") or "").split())[:500]
        inbox_id = conn.execute(
            "INSERT INTO inbox (date, kind, amount, currency, comment, external_id) VALUES (?, ?, ?, ?, ?, ?)",
            (day, kind, amount, currency, description, ext),
        ).lastrowid
        counts["new"] += 1

        partner = find_partner(conn, account, kind, amount, currency, day)
        if partner:  # both halves arrive in the account that received the money
            income_side = ext if kind == "income" else partner["external_id"]
            destination = transfers.account_of_line(conn, income_side)
            rules.make_transfer(conn, partner["id"], "accounts", destination)
            rules.make_transfer(conn, inbox_id, "accounts", destination)
            counts["transfers"] += 1
            continue
        rule = rules.find(conn, description, kind)
        if rule and rules.apply_rule(conn, inbox_id, rule):
            counts["transfers" if rule["is_transfer"] else "sorted"] += 1
        else:
            counts["waiting"] += 1
    return counts


def last_read(conn, account: str) -> int | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (f"mono_last:{account}",)).fetchone()
    return int(row["value"]) if row else None


def set_last_read(conn, account: str, when: int) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (f"mono_last:{account}", str(when)))


# ---------- Monobank jars ("банки") ----------
def get_meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def set_meta(conn, key: str, value) -> None:
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, str(value)))


def free_name(conn, title: str) -> str:
    base = " ".join(title.split())[: jar_module.MAX_NAME] or "Monobank jar"
    name, n = base, 1
    while conn.execute("SELECT 1 FROM jars WHERE lower(name) = lower(?)", (name,)).fetchone():
        n += 1
        name = f"{base[: jar_module.MAX_NAME - 7]} (Mono{'' if n == 2 else f' {n - 1}'})"
    return name


def sync_jars(conn, mono_jars: list[dict], now: int) -> list[tuple[dict, dict]]:
    """Make a site jar for every Monobank jar that has none (name, goal and what is in it now come from the
    bank; a jar with the same name and currency that Aiia already made is linked instead). Returns the
    (site jar, Monobank jar) pairs whose balance changed since last time: their statements must be read."""
    to_read = []
    for mj in mono_jars:
        currency = CURRENCY_CODES.get(mj.get("currencyCode"))
        if currency is None:  # not hryvnias or euros: not tracked
            continue
        mono_id, balance = mj["id"], int(mj.get("balance") or 0)
        row = conn.execute("SELECT * FROM jars WHERE mono_id = ?", (mono_id,)).fetchone()
        if row is None:
            title = " ".join((mj.get("title") or "").split())
            same = conn.execute(
                "SELECT id FROM jars WHERE lower(name) = lower(?) AND currency = ? AND mono_id IS NULL", (title, currency)
            ).fetchone()
            if same:
                conn.execute("UPDATE jars SET mono_id = ? WHERE id = ?", (mono_id, same["id"]))
            else:
                goal = int(mj.get("goal") or 0)
                conn.execute(
                    "INSERT INTO jars (name, currency, target, opening, mono_id) VALUES (?, ?, ?, ?, ?)",
                    (free_name(conn, title), currency, goal if goal > 0 else None, max(balance, 0), mono_id),
                )
            # What is in the jar now is already counted; only lines from this moment on are added.
            set_meta(conn, f"mono_jar_since:{mono_id}", now)
            set_meta(conn, f"mono_jar_last:{mono_id}", now)
            set_meta(conn, f"mono_jar_balance:{mono_id}", balance)
            continue
        known = get_meta(conn, f"mono_jar_balance:{mono_id}")
        if known is None or int(known) != balance:
            to_read.append((dict(row), mj))
    return to_read


def find_card_partner(conn, direction: str, amount: int, currency: str, day: str):
    """A waiting card line that is the other half of this jar line (same amount, a day apart at most)."""
    other = "expense" if direction == "in" else "income"
    return conn.execute(
        """
        SELECT id FROM inbox WHERE kind = ? AND amount = ? AND currency = ? AND abs(julianday(date) - julianday(?)) <= 1
        ORDER BY id LIMIT 1
        """,
        (other, amount, currency, day),
    ).fetchone()


def import_jar_lines(conn, jar: dict, lines: list[dict]) -> dict:
    """Jar statement lines -> deposits and withdrawals of the site jar.

    A jar line that matches a waiting card line (same amount, same day) turns that card line into the
    deposit / withdrawal, so the money is counted once. A jar line with no card line (money from somewhere
    else, interest) is saved on its own so the jar always matches the bank.
    """
    counts = {"paired": 0, "alone": 0, "skipped": 0}
    since = int(get_meta(conn, f"mono_jar_since:{jar['mono_id']}") or 0)
    for line in sorted(lines, key=lambda x: x["time"]):
        signed = line.get("amount", 0)
        ext = external_id(jar["mono_id"], line["id"])
        old = conn.execute(
            "SELECT 1 FROM transactions WHERE external_id = ? UNION SELECT 1 FROM seen_lines WHERE external_id = ?", (ext, ext)
        ).fetchone()
        if signed == 0 or line["time"] < since or old or CURRENCY_CODES.get(line.get("currencyCode")) != jar["currency"]:
            counts["skipped"] += 1
            continue
        direction, amount = ("in", signed) if signed > 0 else ("out", -signed)
        day = datetime.fromtimestamp(line["time"], TZ).date().isoformat()
        partner = find_card_partner(conn, direction, amount, jar["currency"], day)
        if partner and rules.make_jar_transfer(conn, partner["id"], jar["id"]):
            conn.execute("INSERT OR IGNORE INTO seen_lines (external_id) VALUES (?)", (ext,))
            counts["paired"] += 1
            continue
        description = " ".join((line.get("description") or line.get("comment") or "").split())[:500]
        conn.execute(
            """
            INSERT INTO transactions (date, kind, amount, currency, comment, source, external_id, jar_id, direction)
            VALUES (?, 'transfer', ?, ?, ?, 'mono', ?, ?, ?)
            """,
            (day, amount, jar["currency"], description, ext, jar["id"], direction),
        )
        counts["alone"] += 1
    return counts


def finish_jar(conn, mono_id: str, balance: int, now: int) -> None:
    set_meta(conn, f"mono_jar_balance:{mono_id}", int(balance or 0))
    set_meta(conn, f"mono_jar_last:{mono_id}", now)
