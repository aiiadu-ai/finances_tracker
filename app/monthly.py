"""The month against the plan: the sums behind the Home screen. No web code here, just counting.

The limit of a category for a month IS its planned amount from the year plan (table `plans`); there is no separate budget.

Money is whole kopiykas / cents. Hryvnias and euros are never added together or converted.
"""

CURRENCIES = ("UAH", "EUR")
GROUP_LABELS = {"essentials": "Essentials", "fixed": "Fixed", "fun": "Fun"}
CLOSE_PERCENT = 80  # from this share of the limit the state becomes "close"


def state(spent: int, limit: int) -> str:
    """'ok' under 80% of the limit, 'close' from 80% up to the limit, 'over' above it."""
    if spent > limit:
        return "over"
    if spent * 100 >= limit * CLOSE_PERCENT:
        return "close"
    return "ok"


def percent(spent: int, limit: int) -> int:
    return spent * 100 // limit


def month_limits(conn, month: str) -> dict:
    """{(category_id, currency): planned amount} of the expense categories for one month ('2026-11')."""
    rows = conn.execute(
        """
        SELECT p.category_id, p.currency, p.amount FROM plans p JOIN categories c ON c.id = p.category_id
        WHERE p.month = ? AND c.kind = 'expense'
        """,
        (month,),
    ).fetchall()
    return {(row["category_id"], row["currency"]): row["amount"] for row in rows}


def month_summary(conn, first, following) -> dict:
    """Everything Home needs about the month's plan [first, following)."""
    limits = month_limits(conn, first.strftime("%Y-%m"))
    spent_rows = conn.execute(
        """
        SELECT category_id, currency, SUM(amount) AS total FROM transactions
        WHERE kind = 'expense' AND date >= ? AND date < ?
        GROUP BY category_id, currency
        """,
        (first.isoformat(), following.isoformat()),
    ).fetchall()
    spent = {(row["category_id"], row["currency"]): row["total"] for row in spent_rows}

    # Archived categories stay visible only if they have money in this month.
    used = {cat for cat, _ in spent} | {cat for cat, _ in limits}
    categories = [
        row
        for row in conn.execute(
            """
            SELECT id, name, "group" AS grp, archived FROM categories
            WHERE kind = 'expense'
            ORDER BY CASE "group" WHEN 'essentials' THEN 0 WHEN 'fixed' THEN 1 ELSE 2 END, id
            """
        )
        if not row["archived"] or row["id"] in used
    ]

    rows = []
    for cat in categories:
        lines = []
        for code in CURRENCIES:
            s, limit = spent.get((cat["id"], code), 0), limits.get((cat["id"], code))
            if s or limit:
                line = {"currency": code, "spent": s, "limit": limit}
                if limit:
                    line.update(
                        state=state(s, limit), pct=min(100, percent(s, limit)), left=limit - s
                    )
                lines.append(line)
        if not lines:
            lines = [{"currency": "UAH", "spent": 0, "limit": None}]
        rows.append({"id": cat["id"], "name": cat["name"], "grp": cat["grp"], "lines": lines})

    # The half-circle: the plan against what was spent in categories that have a limit.
    arcs = []
    for code in CURRENCIES:
        total_limit = sum(v for (_, c), v in limits.items() if c == code)
        if not total_limit:
            continue
        limited_spent = sum(spent.get((cat, c), 0) for (cat, c) in limits if c == code)
        all_spent = sum(v for (_, c), v in spent.items() if c == code)
        left = total_limit - limited_spent
        arcs.append(
            {
                "currency": code,
                "limit": total_limit,
                "spent": limited_spent,
                "left": left,
                "percent": percent(limited_spent, total_limit),
                "fill": min(100, percent(limited_spent, total_limit)),
                "state": state(limited_spent, total_limit),
                "unlimited": all_spent - limited_spent,
            }
        )

    groups = []
    for key, label in GROUP_LABELS.items():
        members = [row["id"] for row in rows if row["grp"] == key]
        lines = []
        for code in CURRENCIES:
            s = sum(spent.get((cat, code), 0) for cat in members)
            limit = sum(limits.get((cat, code), 0) for cat in members)
            if s or limit:
                lines.append({"currency": code, "spent": s, "limit": limit or None})
        groups.append({"key": key, "label": label, "lines": lines or [{"currency": "UAH", "spent": 0, "limit": None}]})

    return {"rows": rows, "arcs": arcs, "groups": groups, "has_limits": bool(limits)}


def currency_view(summary: dict, code: str) -> dict:
    """The same summary, cut down to one currency (no mixing, no converting)."""
    empty = {"currency": code, "spent": 0, "limit": None}

    def pick(lines):
        return next((line for line in lines if line["currency"] == code), empty)

    return {
        "rows": [{**row, "line": pick(row["lines"])} for row in summary["rows"]],
        "arc": next((arc for arc in summary["arcs"] if arc["currency"] == code), None),
        "groups": [{**group, "line": pick(group["lines"])} for group in summary["groups"]],
    }
