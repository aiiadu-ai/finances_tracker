"""Jars: savings goals. Money moves in and out of them with transfers, which are neither income nor
expense. Hryvnias and euros are never mixed: a jar lives in one currency."""

from datetime import date

CURRENCIES = ("UAH", "EUR")
MAX_NAME = 40
MAX_QUOTE = 200


def list_jars(conn, archived: bool = False, keep_id: int | None = None) -> list[dict]:
    """Jars with their balance (opening + deposits − withdrawals) and progress to the goal."""
    rows = conn.execute(
        """
        SELECT j.*, j.opening + COALESCE(SUM(CASE t.direction WHEN 'in' THEN t.amount ELSE -t.amount END), 0) AS balance,
               COUNT(t.id) AS moves
        FROM jars j LEFT JOIN transactions t ON t.jar_id = j.id AND t.kind = 'transfer'
        WHERE j.archived = ? OR j.id = ?
        GROUP BY j.id ORDER BY j.currency DESC, j.name COLLATE NOCASE
        """,
        (1 if archived else 0, keep_id),
    ).fetchall()
    return [describe(dict(row)) for row in rows]


def get_jar(conn, jar_id: int) -> dict | None:
    for jar in list_jars(conn, archived=True, keep_id=jar_id) + list_jars(conn):
        if jar["id"] == jar_id:
            return jar
    return None


def describe(jar: dict) -> dict:
    balance, target = jar["balance"], jar["target"]
    jar["reached"] = bool(target) and balance >= target
    jar["pct"] = min(100, balance * 100 // target) if target else None
    jar["left"] = max(0, target - balance) if target else None
    # Euro jars: what it is worth in hryvnias at the rate set by hand (never a market rate).
    jar["uah_value"] = balance * jar["my_rate"] // 100 if jar["currency"] == "EUR" and jar["my_rate"] else None
    return jar


def negative(conn, jar_ids) -> str | None:
    """The name of the first of these jars whose balance is below zero, if any."""
    ids = {i for i in jar_ids if i}
    for jar in list_jars(conn, archived=True) + list_jars(conn):
        if jar["id"] in ids and jar["balance"] < 0:
            return jar["name"]
    return None


def month_flows(conn, first: date, following: date) -> dict[str, int]:
    """Per currency: what went into jars in the month, minus what was taken out.

    A euro deposit paid with hryvnias takes those hryvnias out of the free ones instead of free euros.
    """
    flows = {code: 0 for code in CURRENCIES}
    for row in conn.execute(
        """
        SELECT currency, direction, amount, paid_uah FROM transactions
        WHERE kind = 'transfer' AND jar_id IS NOT NULL AND date >= ? AND date < ?
        """,
        (first.isoformat(), following.isoformat()),
    ):
        if row["paid_uah"]:
            flows["UAH"] += row["paid_uah"]
        else:
            flows[row["currency"]] += row["amount"] if row["direction"] == "in" else -row["amount"]
    return flows


def pick_quote(conn, jar_id: int) -> str | None:
    """After a deposit: this jar's own quote, else a random one from the general list."""
    row = conn.execute("SELECT quote FROM jars WHERE id = ?", (jar_id,)).fetchone()
    if row and row["quote"]:
        return row["quote"]
    row = conn.execute("SELECT text FROM quotes ORDER BY RANDOM() LIMIT 1").fetchone()
    return row["text"] if row else None
