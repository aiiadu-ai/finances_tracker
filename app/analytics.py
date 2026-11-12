"""Analytics: where the money goes, how it changes, plan against reality, and a forecast.

Only counting and chart geometry here; the page draws it. Money is whole kopiykas / cents and
hryvnias and euros are never mixed or converted.
"""

from calendar import monthrange
from datetime import date, timedelta

from . import monthly

CURRENCIES = ("UAH", "EUR")
GROUP_LABELS = monthly.GROUP_LABELS
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def add_months(first: date, n: int) -> date:
    index = first.year * 12 + first.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def percent_text(part: int, total: int) -> str:
    if not total or not part:
        return "0%"
    value = part * 100 / total
    return "<1%" if value < 1 else f"{int(value + 0.5)}%"


def compact(amount: int) -> str:
    """A short amount for a label above a column: 850, 1.2k, 12k, 1.5M (no currency sign)."""
    whole = amount // 100
    if not whole:
        return ""
    if whole < 1000:
        return str(whole)
    if whole < 10_000:
        return f"{whole / 1000:.1f}".rstrip("0").rstrip(".") + "k"
    if whole < 1_000_000:
        return f"{round(whole / 1000)}k"
    return f"{whole / 1_000_000:.1f}".rstrip("0").rstrip(".") + "M"


# ---------- Queries ----------
def category_totals(conn, start: date, end: date, currency: str):
    return conn.execute(
        """
        SELECT c.id, c.name, c."group" AS grp, SUM(t.amount) AS total
        FROM transactions t JOIN categories c ON c.id = t.category_id
        WHERE t.kind = 'expense' AND t.currency = ? AND t.date >= ? AND t.date < ?
        GROUP BY c.id ORDER BY total DESC, c.name
        """,
        (currency, start.isoformat(), end.isoformat()),
    ).fetchall()


def kind_total(conn, kind: str, start: date, end: date, currency: str) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM transactions "
        "WHERE kind = ? AND currency = ? AND date >= ? AND date < ?",
        (kind, currency, start.isoformat(), end.isoformat()),
    ).fetchone()[0]


def monthly_actual(conn, kind: str, year: int, currency: str) -> list[int]:
    out = [0] * 12
    for row in conn.execute(
        """
        SELECT CAST(strftime('%m', date) AS INTEGER) AS m, SUM(amount) AS total FROM transactions
        WHERE kind = ? AND currency = ? AND date >= ? AND date < ? GROUP BY m
        """,
        (kind, currency, f"{year}-01-01", f"{year + 1}-01-01"),
    ):
        out[row["m"] - 1] = row["total"]
    return out


def monthly_plan(conn, kind: str, year: int, currency: str) -> list[int]:
    out = [0] * 12
    for row in conn.execute(
        """
        SELECT CAST(substr(p.month, 6, 2) AS INTEGER) AS m, SUM(p.amount) AS total
        FROM plans p JOIN categories c ON c.id = p.category_id
        WHERE c.kind = ? AND p.currency = ? AND p.month >= ? AND p.month < ? GROUP BY m
        """,
        (kind, currency, f"{year}-01", f"{year + 1}-01"),
    ):
        out[row["m"] - 1] = row["total"]
    return out


# ---------- 1. Where the money goes ----------
def _height(value: int, biggest: int) -> float:
    """A column's height in percent of the tallest one (a thin sliver rather than nothing)."""
    return 0 if not value or not biggest else max(2.0, round(value * 100 / biggest, 1))


def where_money(conn, start: date, end: date, currency: str) -> dict:
    """Three group columns; each group holds its own categories (biggest first)."""
    rows = category_totals(conn, start, end, currency)
    total = sum(r["total"] for r in rows)
    parts = {key: sum(r["total"] for r in rows if r["grp"] == key) for key in GROUP_LABELS}
    biggest_group = max(parts.values())
    groups = []
    for key, label in GROUP_LABELS.items():
        mine = [r for r in rows if r["grp"] == key]
        biggest = mine[0]["total"] if mine else 0
        groups.append(
            {
                "key": key,
                "label": label,
                "total": parts[key],
                "pct": percent_text(parts[key], total),
                "h": _height(parts[key], biggest_group),
                "cats": [
                    {
                        "name": r["name"],
                        "total": r["total"],
                        "pct": percent_text(r["total"], total),
                        "h": _height(r["total"], biggest),
                    }
                    for r in mine
                ],
            }
        )
    return {"total": total, "groups": groups}


# ---------- 2. Dynamics ----------
def _same_day_before(day: date, years: int = 0) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 Feb
        return day.replace(year=day.year - years, day=28)


def dynamics(conn, period: str, selected: date, today: date, currency: str) -> dict:
    """This period against the previous one, per kind.

    The running month (or year) is compared with the same stretch of the previous one,
    so "so far" is never set against a whole month.
    """
    if period == "month":
        start, end = selected, add_months(selected, 1)
        prev_start, prev_end = add_months(selected, -1), selected
        running = selected == today.replace(day=1)
        if running:
            end = today + timedelta(days=1)
            days = min(today.day, monthrange(prev_start.year, prev_start.month)[1])
            prev_end_like = prev_start + timedelta(days=days)
    else:
        start, end = date(selected.year, 1, 1), date(selected.year + 1, 1, 1)
        prev_start, prev_end = date(selected.year - 1, 1, 1), start
        running = selected.year == today.year
        if running:
            end = today + timedelta(days=1)
            prev_end_like = _same_day_before(today, 1) + timedelta(days=1)
    result = {"running": running}
    for kind in ("expense", "income"):
        now = kind_total(conn, kind, start, end, currency)
        before = kind_total(conn, kind, prev_start, prev_end_like if running else prev_end, currency)
        diff = now - before
        result[kind] = {
            "now": now,
            "before": before,
            "diff": diff,
            "pct": int(abs(diff) * 100 / before + 0.5) * (1 if diff >= 0 else -1) if before else None,
            "before_full": kind_total(conn, kind, prev_start, prev_end, currency) if running else None,
        }
    return result


# ---------- Charts ----------
def chart(values: list[int], plan: list[int] | None = None, selected: int | None = None) -> dict:
    """12 month columns (actual), with optional wider pale "shadow" columns (plan).

    Heights are percents of the tallest value, so the page can draw them as plain boxes.
    """
    peak = max(values + (plan or []) + [0]) or 1
    cols = [
        {
            "name": MONTHS[i],
            "value": value,
            "label": compact(value),
            "bar": _height(value, peak),
            "plan": plan[i] if plan else None,
            "plan_h": _height(plan[i], peak) if plan else 0,
            "selected": selected == i + 1,
        }
        for i, value in enumerate(values)
    ]
    return {"cols": cols, "peak": peak, "has_plan": bool(plan and any(plan))}


def daily_totals(conn, first: date, end: date, currency: str) -> dict[int, int]:
    return {
        row["d"]: row["total"]
        for row in conn.execute(
            """
            SELECT CAST(strftime('%d', date) AS INTEGER) AS d, SUM(amount) AS total FROM transactions
            WHERE kind = 'expense' AND currency = ? AND date >= ? AND date < ? GROUP BY d
            """,
            (currency, first.isoformat(), end.isoformat()),
        )
    }


def line_chart(per_day: dict[int, int], day: int, days: int, projected: int, limit: int | None) -> dict:
    """Geometry for the forecast: spent so far (solid), the rest of the month (dashed), the limit."""
    width, height, left, right, top, bottom = 340, 190, 8, 12, 14, 24
    peak = max(projected, limit or 0, sum(per_day.values()), 1) * 1.08
    inner_w, inner_h = width - left - right, height - top - bottom

    def x(d):
        return round(left + d * inner_w / days, 1)

    def y(value):
        return round(top + inner_h - value * inner_h / peak, 1)

    running, points = 0, [(0, 0)]
    for d in range(1, day + 1):
        running += per_day.get(d, 0)
        points.append((d, running))
    ticks = [1, *range(5, days + 1, 5)]
    if days - ticks[-1] >= 3:
        ticks.append(days)
    return {
        "width": width,
        "height": height,
        "base": y(0),
        "left": left,
        "right": width - right,
        "fact": " ".join(f"{x(d)},{y(v)}" for d, v in points),
        "now": {"x": x(day), "y": y(running)},
        "end": {"x": x(days), "y": y(projected)},
        "limit_y": y(limit) if limit else None,
        "ticks": [{"x": x(d), "label": d, "today": d == day} for d in ticks],
        "today_x": x(day),
        "label_y": height - 7,
    }


# ---------- 4. Forecast ----------
def forecast(conn, first: date, today: date, currency: str) -> dict:
    """Month-end forecast for the running month.

    Fixed is taken as it is (its bills are once-a-month); Essentials and Fun are projected
    at the current pace. Compared with the limits of the categories that have one.
    """
    days_in_month = monthrange(first.year, first.month)[1]
    day = today.day
    end = today + timedelta(days=1)
    limits = monthly.month_limits(conn, first.strftime("%Y-%m"))
    limited = {cat for (cat, code) in limits if code == currency}

    def project(spent, grp):
        # An estimate: rounded to whole hryvnias / euros (kopiykas would only pretend to be exact).
        value = spent if grp == "fixed" else spent * days_in_month // day
        return (value + 50) // 100 * 100

    spent = fixed = flexible = projected = projected_limited = 0
    for row in category_totals(conn, first, end, currency):
        spent += row["total"]
        if row["grp"] == "fixed":
            fixed += row["total"]
        else:
            flexible += row["total"]
        projected += project(row["total"], row["grp"])
        if row["id"] in limited:
            projected_limited += project(row["total"], row["grp"])

    limit = sum(v for (cat, code), v in limits.items() if code == currency)
    budget = None
    if limit:
        budget = {
            "limit": limit,
            "projected": projected_limited,
            "state": monthly.state(projected_limited, limit),
            "fill": min(100, monthly.percent(projected_limited, limit)),
            "percent": monthly.percent(projected_limited, limit),
            "left": limit - projected_limited,
            "unlimited": projected - projected_limited,
        }
    return {
        "chart": line_chart(daily_totals(conn, first, end, currency), day, days_in_month, projected, limit or None),
        "spent": spent,
        "fixed": fixed,
        "flexible": flexible,
        "per_day": flexible // day,
        "projected": projected,
        "day": day,
        "days": days_in_month,
        "budget": budget,
    }


# ---------- The whole page, for one currency ----------
def build(conn, period: str, selected: date, today: date, currency: str) -> dict:
    if period == "month":
        start, end = selected, add_months(selected, 1)
        year, picked = selected.year, selected.month
    else:
        start, end = date(selected.year, 1, 1), date(selected.year + 1, 1, 1)
        year, picked = selected.year, None
    trend = {k: chart(monthly_actual(conn, k, year, currency), selected=picked) for k in ("expense", "income")}
    versus = {
        k: chart(monthly_actual(conn, k, year, currency), monthly_plan(conn, k, year, currency), picked)
        for k in ("expense", "income")
    }
    running_month = period == "month" and selected == today.replace(day=1)
    return {
        "where": where_money(conn, start, end, currency),
        "dynamics": dynamics(conn, period, selected, today, currency),
        "trend": trend,
        "versus": versus,
        "forecast": forecast(conn, selected, today, currency) if running_month else None,
    }
