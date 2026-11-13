"""All the money Aiia has, per currency: what she had at the start + everything since.

Money sent to people is an expense and money received from people is income; moves between her own accounts
and into / out of jars are neither. So, on any day:

  All money = start + income + received from people − expenses − sent to people
  In jars   = what lies in all the jars (opening + deposits − withdrawals)
  Free      = All money − In jars     (money that is not in jars but is there)

Hryvnias and euros are counted apart; the rate only gives one "about" line.
"""

import re
from datetime import date, timedelta

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


def all_money(conn, start: dict, cutoff: str | None = None) -> dict[str, int]:
    """All the money per currency on the day `cutoff` (ISO date; empty = everything recorded)."""
    totals = {code: start[code] for code in ("UAH", "EUR")}
    sql = """
        SELECT currency, SUM(CASE
            WHEN kind = 'income' THEN amount
            WHEN kind = 'expense' THEN -amount
            WHEN kind = 'transfer' AND counterpart = 'people' THEN CASE direction WHEN 'in' THEN amount ELSE -amount END
            ELSE 0 END) AS net
        FROM transactions WHERE date >= ?"""
    args = [start["date"]]
    if cutoff:
        sql += " AND date <= ?"
        args.append(cutoff)
    for row in conn.execute(sql + " GROUP BY currency", args):
        totals[row["currency"]] += row["net"]
    return totals


def jars_total(conn, cutoff: str | None = None) -> dict[str, int]:
    """What lies in all the jars (archived ones too) per currency on the day `cutoff`."""
    total = {"UAH": 0, "EUR": 0}
    for row in conn.execute("SELECT currency, SUM(opening) AS amount FROM jars GROUP BY currency"):
        total[row["currency"]] += row["amount"]
    sql = """
        SELECT currency, SUM(CASE direction WHEN 'in' THEN amount ELSE -amount END) AS amount
        FROM transactions WHERE kind = 'transfer' AND jar_id IS NOT NULL"""
    args = []
    if cutoff:
        sql += " AND date <= ?"
        args.append(cutoff)
    for row in conn.execute(sql + " GROUP BY currency", args):
        total[row["currency"]] += row["amount"]
    return total


def cutoff_for_month(first: date, today: date) -> date:
    """The day to count up to for a month: its last day, or today while the month is still running."""
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    return min(following - timedelta(days=1), today)


def standing(conn, start: dict | None, first: date, today: date) -> dict:
    """In jars, All money and Free per currency at the end of the month `first` (today while it runs).

    All money and Free are None before the starting day (or without a start): they can't be told.
    """
    cutoff = cutoff_for_month(first, today)
    iso = cutoff.isoformat()
    in_jars = jars_total(conn, iso)
    result = {"cutoff": cutoff, "in_jars": in_jars, "all_money": None, "free": None}
    if start and iso >= start["date"]:
        everything = all_money(conn, start, iso)
        result["all_money"] = everything
        result["free"] = {code: everything[code] - in_jars[code] for code in everything}
    return result


NET_SQL = """SUM(CASE
            WHEN kind = 'income' THEN amount
            WHEN kind = 'expense' THEN -amount
            WHEN kind = 'transfer' AND counterpart = 'people' THEN CASE direction WHEN 'in' THEN amount ELSE -amount END
            ELSE 0 END)"""


def left_at_month_end(conn, start: dict | None, year: int, today: date) -> dict | None:
    """What is left at the end of each month of `year`, per currency (None without a start).

    It is the All money of that day: start + income + received − expenses − sent (money in jars included; moves
    between her own accounts and into / out of jars change nothing). Up to this month it is what really happened;
    after it, the plan: the previous number + the planned income − the planned expenses of the month (a month with
    no plan keeps the number). The chain runs on from year to year, so January starts where December ended.
    A month before the starting day has no number.

    Returns {currency: {"months": [{"value", "forecast", "together"} × 12], "year": the December cell}};
    "together" is both currencies in hryvnias at her rate (None without a rate or without euros that month).
    """
    if not start:
        return None
    first = date.fromisoformat(start["date"])
    this_month = today.strftime("%Y-%m")
    actual = {
        (row["m"], row["currency"]): row["net"]
        for row in conn.execute(
            f"SELECT substr(date, 1, 7) AS m, currency, {NET_SQL} AS net FROM transactions "
            "WHERE date >= ? AND date <= ? GROUP BY m, currency",
            (start["date"], today.isoformat()),
        )
    }
    planned = {
        (row["month"], row["currency"]): row["net"]
        for row in conn.execute(
            """
            SELECT p.month, p.currency, SUM(CASE c.kind WHEN 'income' THEN p.amount ELSE -p.amount END) AS net
            FROM plans p JOIN categories c ON c.id = p.category_id GROUP BY p.month, p.currency
            """
        )
    }
    result = {}
    for code in ("UAH", "EUR"):
        value, cells = start[code], {}
        y, m = first.year, first.month
        while (y, m) <= (year, 12):
            key = f"{y}-{m:02d}"
            forecast = key > this_month
            value += (planned if forecast else actual).get((key, code), 0)
            cells[(y, m)] = {"value": value, "forecast": forecast}
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
        months = [cells.get((year, month), {"value": None, "forecast": False}) for month in range(1, 13)]
        result[code] = {"months": months, "year": months[11]}
    # Both currencies in one number (euros at her own rate), for a small line under each month: "how much in all".
    rate = start.get("rate")
    for code in ("UAH", "EUR"):
        for i, cell in enumerate(result[code]["months"]):
            uah, eur = result["UAH"]["months"][i]["value"], result["EUR"]["months"][i]["value"]
            cell["together"] = together({"UAH": uah, "EUR": eur}, rate) if uah is not None and rate and eur else None
        result[code]["year"] = result[code]["months"][11]
    return result


def together(totals: dict[str, int], rate: int | None) -> int | None:
    """Both currencies as hryvnias at her own rate. Only for one "about" line, never stored or mixed in sums."""
    if not rate or not totals["EUR"]:
        return None
    return totals["UAH"] + totals["EUR"] * rate // 100
