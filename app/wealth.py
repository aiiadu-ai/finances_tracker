"""All the money Aiia has right now, per currency: what she had at the start + everything since.

start + income − expenses − sent to people + received from people (moves between her own accounts and
jars change nothing). Hryvnias and euros are counted apart; the rate only gives one "about" line.
"""

import re
from datetime import date

KEYS = ("start_date", "start_UAH", "start_EUR", "rate")
MAX_RATE = 100_000  # hundredths of a hryvnia per euro


def get_start(conn) -> dict | None:
    got = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta WHERE key IN (?, ?, ?, ?)", KEYS)}
    if "start_date" not in got:
        return None
    return {
        "date": got["start_date"],
        "UAH": int(got.get("start_UAH", 0)),
        "EUR": int(got.get("start_EUR", 0)),
        "rate": int(got["rate"]) if got.get("rate") else None,
    }


def save_start(conn, day: str, uah: int, eur: int, rate: int | None) -> None:
    values = {"start_date": day, "start_UAH": str(uah), "start_EUR": str(eur)}
    for key, value in values.items():
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
    if rate:
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('rate', ?)", (str(rate),))
    else:
        conn.execute("DELETE FROM meta WHERE key = 'rate'")


def parse_money(raw: str) -> int:
    """Kopiykas / cents from typed text; empty or 0 is zero. Raises ValueError if it is not an amount."""
    cleaned = re.sub(r"\s", "", raw or "")
    if not cleaned:
        return 0
    if not re.fullmatch(r"[0-9]+([.,][0-9]{1,2})?", cleaned):
        raise ValueError("not an amount")
    whole, _, fraction = cleaned.replace(",", ".").partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if amount > 99_999_999_999:
        raise ValueError("too large")
    return amount


def parse_rate(raw: str) -> int | None:
    """Hundredths of a hryvnia per euro ("50" -> 5000). Empty = no rate."""
    value = parse_money(raw)
    if value > MAX_RATE:
        raise ValueError("too large")
    return value or None


def all_money(conn, start: dict) -> dict[str, int]:
    totals = {code: start[code] for code in ("UAH", "EUR")}
    for row in conn.execute(
        """
        SELECT currency, SUM(CASE
            WHEN kind = 'income' THEN amount
            WHEN kind = 'expense' THEN -amount
            WHEN kind = 'transfer' AND counterpart = 'people' THEN CASE direction WHEN 'in' THEN amount ELSE -amount END
            ELSE 0 END) AS net
        FROM transactions WHERE date >= ? GROUP BY currency
        """,
        (start["date"],),
    ):
        totals[row["currency"]] += row["net"]
    return totals


def together(totals: dict[str, int], rate: int | None) -> int | None:
    """Both currencies as hryvnias at her own rate. Only for one "about" line, never stored or mixed in sums."""
    if not rate or not totals["EUR"]:
        return None
    return totals["UAH"] + totals["EUR"] * rate // 100
