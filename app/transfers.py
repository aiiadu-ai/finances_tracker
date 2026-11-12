"""Her own accounts (where money lies) and transfers to / from people.

Transfers are neither income nor expense. Money sent to people is shown on its own and does reduce
"Free" (it has really left); money received from people raises it. Hryvnias and euros never mix.
"""

from datetime import date

CURRENCIES = ("UAH", "EUR")
MAX_NAME = 40


def list_accounts(conn, archived: bool | None = False) -> list[dict]:
    """Accounts by name. archived: False = active only, True = archived only, None = all."""
    where = "" if archived is None else f"WHERE archived = {1 if archived else 0}"
    return [dict(r) for r in conn.execute(f"SELECT id, name, mono_id, archived FROM accounts {where} ORDER BY name COLLATE NOCASE")]


def clean_name(conn, name: str, own_id: int | None = None) -> tuple[str, str]:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        return name, f"Give the account a name of up to {MAX_NAME} characters."
    if conn.execute("SELECT 1 FROM accounts WHERE name = ? AND id IS NOT ?", (name, own_id)).fetchone():
        return name, "There is already an account with that name."
    return name, ""


def ensure_mono_account(conn, mono_id: str, name: str) -> int:
    """The account for a Monobank card (made once; she may rename it later)."""
    row = conn.execute("SELECT id FROM accounts WHERE mono_id = ?", (mono_id,)).fetchone()
    if row:
        return row["id"]
    base, n = name, 1
    while conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        n += 1
        name = f"{base} ({n})"
    return conn.execute("INSERT INTO accounts (name, mono_id) VALUES (?, ?)", (name, mono_id)).lastrowid


def account_of_line(conn, external_id: str) -> int | None:
    """The account a bank line belongs to (its external id starts with the card id)."""
    row = conn.execute("SELECT id FROM accounts WHERE mono_id = ?", (external_id.split(":", 1)[0],)).fetchone()
    return row["id"] if row else None


def people_flows(conn, first: date, following: date) -> dict[str, dict[str, int]]:
    """Per currency: what was sent to people and what was received from them in the month."""
    flows = {code: {"sent": 0, "received": 0} for code in CURRENCIES}
    for row in conn.execute(
        """
        SELECT currency, direction, SUM(amount) AS total FROM transactions
        WHERE kind = 'transfer' AND counterpart = 'people' AND date >= ? AND date < ?
        GROUP BY currency, direction
        """,
        (first.isoformat(), following.isoformat()),
    ):
        flows[row["currency"]]["received" if row["direction"] == "in" else "sent"] = row["total"]
    return flows
