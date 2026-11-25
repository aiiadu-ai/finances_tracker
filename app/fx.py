"""Hryvnias and euros together, in ₴, for the categories: Home → Categories, Analytics and the Year's
Actual / Difference views. Everything else (Income, Expenses, In jars, Free, accounts) stays per currency.

The rate is the NBU's euro rate for today. `monosync` (cron) reads it from the NBU once an hour and keeps it
in `meta`, so the site itself never goes to the internet. When the NBU rate is missing or older than
FRESH_DAYS, Aiia's own rate from Settings → Money is used. With no rate at all, euros are left out.

A rate is whole kopiykas per euro (49.61 ₴ -> 4961), like the rate in Settings.
"""

from datetime import date, datetime, timedelta

NBU_URL = "https://bank.gov.ua/NBUStatService/v1/statdirectory/exchange?valcode=EUR&json"
FRESH_DAYS = 3  # an NBU rate older than this is not used any more
REFRESH_SECONDS = 3600  # monosync asks the NBU at most once an hour
TOGETHER = "ALL"  # the "currency" of a view that counts everything in hryvnias


def _meta(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def current_rate(conn, today: date | None = None) -> dict | None:
    """{"value": kopiykas per €, "source": "NBU" or "your rate", "day": date of the NBU rate or None}."""
    today = today or date.today()
    nbu, day = _meta(conn, "nbu_rate"), _meta(conn, "nbu_rate_day")
    if nbu and day:
        nbu_day = date.fromisoformat(day)
        if today - nbu_day <= timedelta(days=FRESH_DAYS):
            return {"value": int(nbu), "source": "NBU", "day": nbu_day}
    own = _meta(conn, "rate")
    if own:
        return {"value": int(own), "source": "your rate", "day": None}
    return None


def to_uah(amount: int, currency: str, rate: int | None) -> int | None:
    """An amount in kopiykas: hryvnias as they are, euros at the rate (None for euros without a rate)."""
    if currency == "UAH":
        return amount
    if currency == "EUR" and rate:
        return (amount * rate + 50) // 100
    return None


def install(conn) -> None:
    """SQL function `pick(amount, currency, wanted)`: the amount when the currency is the wanted one;
    for wanted = 'ALL', the amount in hryvnias (euros at the current rate). Otherwise NULL (SUM skips it)."""
    cache = {}

    def rate():
        if "rate" not in cache:
            try:
                found = current_rate(conn)
            except Exception:  # no meta table yet (a brand new database)
                found = None
            cache["rate"] = found["value"] if found else None
        return cache["rate"]

    def pick(amount, currency, wanted):
        if amount is None:
            return None
        if wanted == TOGETHER:
            return to_uah(amount, currency, rate())
        return amount if currency == wanted else None

    conn.create_function("pick", 3, pick, deterministic=True)


def parse_nbu(data) -> tuple[int, date]:
    """The NBU answer ([{"rate": 49.606, "exchangedate": "25.11.2026", "cc": "EUR"}]) -> (kopiykas, day)."""
    item = next(x for x in data if x.get("cc") == "EUR")
    value = int(round(float(item["rate"]) * 100))
    if value <= 0:
        raise ValueError("bad rate")
    return value, datetime.strptime(item["exchangedate"], "%d.%m.%Y").date()


def refresh_nbu(conn, client, now: int) -> bool:
    """Read today's NBU euro rate into `meta` (at most once an hour). True when a new one was saved."""
    last = _meta(conn, "nbu_rate_at")
    if last and now - int(last) < REFRESH_SECONDS:
        return False
    response = client.get(NBU_URL, timeout=15)
    response.raise_for_status()
    value, day = parse_nbu(response.json())
    for key, text in (("nbu_rate", str(value)), ("nbu_rate_day", day.isoformat()), ("nbu_rate_at", str(now))):
        conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, text))
    return True
