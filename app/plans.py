"""The year plan against reality. No web code here, just counting.

Money is whole kopiykas / cents; hryvnias and euros are never mixed or converted.
"""

from datetime import date

CURRENCIES = ("UAH", "EUR")
CLOSE_PERCENT = 80


def judge(kind: str, plan: int | None, actual: int):
    """(difference, state) for one cell, or (None, None) when there is no plan.

    Expenses: difference = plan - actual (what is left); more than the plan is 'over'.
    Income:   difference = actual - plan (how far ahead); under the plan is 'over' too,
              i.e. it gets the same ✗ as "this is not going as planned".
    States: 'ok' (✓), 'close' (!, within 20% of the plan) and 'over' (✗).
    """
    if not plan:
        return None, None
    if kind == "expense":
        diff = plan - actual
        if actual > plan:
            return diff, "over"
        return diff, "close" if actual * 100 >= plan * CLOSE_PERCENT else "ok"
    diff = actual - plan
    if actual >= plan:
        return diff, "ok"
    return diff, "close" if actual * 100 >= plan * CLOSE_PERCENT else "over"


def year_categories(conn, year: int):
    """Categories on the Year page: active ones, plus archived ones that hold a plan or a transaction."""
    first, following = f"{year}-01", f"{year + 1}-01"
    used = {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT category_id FROM plans WHERE month >= ? AND month < ?", (first, following)
        )
    } | {
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT category_id FROM transactions "
            "WHERE category_id IS NOT NULL AND date >= ? AND date < ?",
            (f"{year}-01-01", f"{year + 1}-01-01"),
        )
    }
    rows = conn.execute(
        """
        SELECT id, kind, name, "group" AS grp, archived FROM categories
        ORDER BY CASE kind WHEN 'expense' THEN 0 ELSE 1 END,
                 CASE "group" WHEN 'essentials' THEN 0 WHEN 'fixed' THEN 1 WHEN 'fun' THEN 2 ELSE 3 END, id
        """
    ).fetchall()
    return [row for row in rows if not row["archived"] or row["id"] in used]


def month_plans(conn, year: int) -> dict:
    """{(category_id, month 1-12, currency): amount} for the whole year."""
    rows = conn.execute(
        "SELECT category_id, month, currency, amount FROM plans WHERE month >= ? AND month < ?",
        (f"{year}-01", f"{year + 1}-01"),
    ).fetchall()
    return {(r["category_id"], int(r["month"][5:]), r["currency"]): r["amount"] for r in rows}


def _cell(kind, plan, actual, future):
    diff, state = (None, None) if future else judge(kind, plan, actual)
    return {"plan": plan, "actual": actual, "diff": diff, "state": state}


def grid(conn, year: int, currency: str, today: date, categories=None) -> dict:
    """Rows (a category with 12 monthly cells and a year total) and total rows, for one currency."""
    categories = categories if categories is not None else year_categories(conn, year)
    plan = {(cat, m): v for (cat, m, code), v in month_plans(conn, year).items() if code == currency}
    actual = {}
    for r in conn.execute(
        """
        SELECT category_id, CAST(strftime('%m', date) AS INTEGER) AS m, SUM(amount) AS total
        FROM transactions
        WHERE kind IN ('income', 'expense') AND currency = ? AND date >= ? AND date < ?
        GROUP BY category_id, m
        """,
        (currency, f"{year}-01-01", f"{year + 1}-01-01"),
    ):
        actual[(r["category_id"], r["m"])] = r["total"]

    def future(m):  # months after the current one have no "reality" yet
        return (year, m) > (today.year, today.month)

    rows = []
    for cat in categories:
        cells = [
            _cell(cat["kind"], plan.get((cat["id"], m)), actual.get((cat["id"], m), 0), future(m))
            for m in range(1, 13)
        ]
        plan_sum = sum(c["plan"] or 0 for c in cells)
        # "Year" compares the plan up to the current month with what has happened so far.
        to_date = sum(c["plan"] or 0 for m, c in enumerate(cells, 1) if not future(m))
        actual_sum = sum(c["actual"] for c in cells)
        total = _cell(cat["kind"], to_date or None, actual_sum, False)
        total["plan"] = plan_sum or None  # shown: the whole-year plan
        rows.append(
            {
                "id": cat["id"],
                "kind": cat["kind"],
                "name": cat["name"],
                "grp": cat["grp"],
                "total": total,
                "cells": cells,
            }
        )

    def total_row(kind):
        cells = []
        for m in range(1, 13):
            p = sum(r["cells"][m - 1]["plan"] or 0 for r in rows if r["kind"] == kind)
            a = sum(r["cells"][m - 1]["actual"] for r in rows if r["kind"] == kind)
            cells.append(_cell(kind, p or None, a, future(m)))
        p = sum(c["plan"] or 0 for c in cells)
        to_date = sum(c["plan"] or 0 for m, c in enumerate(cells, 1) if not future(m))
        a = sum(c["actual"] for c in cells)
        total = _cell(kind, to_date or None, a, False)
        total["plan"], total["plan_to_date"] = p or None, to_date
        return {"cells": cells, "total": total}

    income, expense = total_row("income"), total_row("expense")

    def balance_cell(i, e, future_month):
        p = (i["plan"] or 0) - (e["plan"] or 0)
        a = i["actual"] - e["actual"]
        planned = bool(i["plan"] or e["plan"])
        diff = a - p if planned and not future_month else None
        return {
            "plan": p if planned else None,
            "actual": a,
            "diff": diff,
            "state": None if diff is None else ("ok" if diff >= 0 else "over"),
        }

    # The balance "Year" cell works on plan-to-date as well (see above).
    def to_date_view(t):
        return {**t, "plan": t["plan_to_date"] or None}

    balance_total = balance_cell(to_date_view(income["total"]), to_date_view(expense["total"]), False)
    balance_total["plan"] = (income["total"]["plan"] or 0) - (expense["total"]["plan"] or 0) if (income["total"]["plan"] or expense["total"]["plan"]) else None
    balance = {
        "cells": [
            balance_cell(income["cells"][m - 1], expense["cells"][m - 1], future(m)) for m in range(1, 13)
        ],
        "total": balance_total,
    }
    return {
        "rows": rows,
        "totals": {"income": income, "expense": expense, "balance": balance},
        "has_plan": bool(plan),
    }
