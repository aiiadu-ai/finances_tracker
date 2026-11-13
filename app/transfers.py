"""Her own accounts (where money lies) and transfers to / from people.

Transfers are neither income nor expense. Money sent to people is shown on its own and does reduce
"Free" (it has really left); money received from people raises it. Hryvnias and euros never mix.
"""

from datetime import date

CURRENCIES = ("UAH", "EUR")
MAX_NAME = 40


def list_accounts(conn, archived: bool | None = False, currency: str | None = None) -> list[dict]:
    """Accounts by name. archived: False = active only, True = archived only, None = all.
    currency: only the accounts in that currency (an account with no currency set fits any)."""
    where = [] if archived is None else [f"archived = {1 if archived else 0}"]
    args = []
    if currency:
        where.append("(currency = ? OR currency IS NULL)")
        args.append(currency)
    order = "CASE currency WHEN 'UAH' THEN 0 WHEN 'EUR' THEN 1 ELSE 2 END, CASE kind WHEN 'cash' THEN 0 WHEN 'card' THEN 1 ELSE 2 END, name COLLATE NOCASE"
    sql = "SELECT * FROM accounts" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY " + order
    return [dict(r) for r in conn.execute(sql, args)]


def cash_account(conn, currency: str) -> int | None:
    """The cash account of a currency: where entries made by hand (site, bot) are counted."""
    row = conn.execute("SELECT id FROM accounts WHERE kind = 'cash' AND currency = ? AND archived = 0 ORDER BY id LIMIT 1", (currency,)).fetchone()
    return row["id"] if row else None


# What an entry does to a hand-kept account X (cash). Money paid / received by hand, money put into a jar by hand,
# money drawn from a card into X (the card line says "went to X"), money put from X onto a card ("came from X").
CASH_NET_SQL = """
    SUM(CASE
        WHEN account_id = :id AND kind = 'income' THEN amount
        WHEN account_id = :id AND kind = 'expense' THEN -amount
        WHEN account_id = :id AND kind = 'transfer' AND jar_id IS NOT NULL THEN CASE direction WHEN 'in' THEN -amount ELSE amount END
        WHEN account_id = :id AND kind = 'transfer' AND counterpart = 'accounts' AND direction = 'out' THEN amount
        WHEN from_account_id = :id AND kind = 'transfer' AND counterpart = 'accounts' THEN -amount
        ELSE 0 END)"""


def hand_balance(conn, account: dict) -> int:
    """What lies in a hand-kept account: its opening + everything recorded on it from the opening day."""
    row = conn.execute(
        f"SELECT COALESCE({CASH_NET_SQL}, 0) AS net FROM transactions WHERE date >= :since",
        {"id": account["id"], "since": account["opening_date"] or "0000-00-00"},
    ).fetchone()
    return (account["opening"] or 0) + row["net"]


def accounts_overview(conn) -> dict[str, list[dict]]:
    """For the Accounts tab: per currency, the Monobank cards (what the bank says) and the cash accounts (counted)."""
    out = {"UAH": [], "EUR": []}
    for account in list_accounts(conn):
        if account["currency"] not in out:  # no currency yet: it cannot be shown
            continue
        if account["kind"] == "card":
            out[account["currency"]].append({"name": account["name"], "kind": "card", "balance": account["balance"], "at": account["balance_at"]})
        elif account["kind"] in ("cash", "other"):
            out[account["currency"]].append({"name": account["name"], "kind": account["kind"], "balance": hand_balance(conn, account), "at": None})
    for rows in out.values():
        rows.sort(key=lambda r: (r["kind"] != "card", r["name"].lower()))
    return out


def clean_name(conn, name: str, own_id: int | None = None) -> tuple[str, str]:
    name = " ".join(name.split())
    if not name or len(name) > MAX_NAME:
        return name, f"Give the account a name of up to {MAX_NAME} characters."
    if conn.execute("SELECT 1 FROM accounts WHERE name = ? AND id IS NOT ?", (name, own_id)).fetchone():
        return name, "There is already an account with that name."
    return name, ""


def ensure_mono_account(conn, mono_id: str, name: str, currency: str | None = None) -> int:
    """The account for a Monobank card (made once; she may rename it later)."""
    row = conn.execute("SELECT id FROM accounts WHERE mono_id = ?", (mono_id,)).fetchone()
    if row:
        if currency:
            conn.execute("UPDATE accounts SET currency = ?, kind = 'card' WHERE id = ?", (currency, row["id"]))
        return row["id"]
    base, n = name, 1
    while conn.execute("SELECT 1 FROM accounts WHERE name = ?", (name,)).fetchone():
        n += 1
        name = f"{base} ({n})"
    return conn.execute("INSERT INTO accounts (name, mono_id, kind, currency) VALUES (?, ?, 'card', ?)", (name, mono_id, currency)).lastrowid


def set_card_balance(conn, mono_id: str, balance: int, when: int) -> None:
    """What Monobank says is on a card (own money: a credit limit is not counted)."""
    conn.execute("UPDATE accounts SET balance = ?, balance_at = ? WHERE mono_id = ?", (balance, when, mono_id))


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
