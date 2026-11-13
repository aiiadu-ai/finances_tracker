"""Bank rules and the bank lines that wait for a category (the inbox).

A rule says: "if the bank description contains this text, it is this category", or a transfer to / from
people, or a transfer between her own accounts. They are Aiia's own rules; nothing is guessed.
A waiting line is not a transaction yet: when sorted it moves into the transactions and is counted.
"""

from . import transfers

MAX_PATTERN = 80
COUNTERPARTS = ("people", "accounts")


def clean_pattern(text: str) -> str:
    return " ".join(text.split())[:MAX_PATTERN]


def list_rules(conn) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            """
            SELECT r.id, r.pattern, r.category_id, r.is_transfer, COALESCE(r.transfer, 'accounts') AS transfer, r.account_id,
                   c.name AS category, c.kind AS kind, c.archived AS archived
            FROM rules r LEFT JOIN categories c ON c.id = r.category_id
            ORDER BY r.pattern COLLATE NOCASE
            """
        )
    ]


def find(conn, description: str, kind: str):
    """The rule that fits a description (the longest matching text wins), or None.

    A category rule only fits lines of its own kind (an expense rule never sorts income).
    Rules pointing to an archived category are ignored.
    """
    text = description.casefold()
    best = None
    for rule in list_rules(conn):
        if not rule["pattern"] or rule["pattern"].casefold() not in text:
            continue
        if not rule["is_transfer"] and (rule["kind"] != kind or rule["archived"]):
            continue
        if best is None or len(rule["pattern"]) > len(best["pattern"]):
            best = rule
    return best


def save_rule(conn, pattern: str, category_id: int | None, transfer: str | None = None,
              account_id: int | None = None, rule_id: int | None = None) -> int:
    """Add a rule (or change the one with this text). category_id None = a transfer ("people" / "accounts")."""
    pattern = clean_pattern(pattern)
    key = pattern.casefold()
    if category_id is None:
        transfer = transfer if transfer in COUNTERPARTS else "accounts"
        account_id = account_id if transfer == "accounts" else None
        values = (None, 1, transfer, account_id)
    else:
        values = (category_id, 0, None, None)
    if rule_id is not None:
        conn.execute(
            "UPDATE rules SET pattern = ?, key = ?, category_id = ?, is_transfer = ?, transfer = ?, account_id = ? WHERE id = ?",
            (pattern, key, *values, rule_id),
        )
        return rule_id
    row = conn.execute("SELECT id FROM rules WHERE key = ?", (key,)).fetchone()
    if row:
        conn.execute("UPDATE rules SET category_id = ?, is_transfer = ?, transfer = ?, account_id = ? WHERE id = ?", (*values, row["id"]))
        return row["id"]
    return conn.execute(
        "INSERT INTO rules (pattern, key, category_id, is_transfer, transfer, account_id) VALUES (?, ?, ?, ?, ?, ?)",
        (pattern, key, *values),
    ).lastrowid


# ---------- The inbox ----------
def unsorted(conn, exclude=()) -> list[dict]:
    """Bank lines waiting for a category, oldest first."""
    skip = set(exclude)
    return [
        dict(r)
        for r in conn.execute("SELECT id, date, kind, amount, currency, comment, external_id FROM inbox ORDER BY date, id")
        if r["id"] not in skip
    ]


def file_entry(conn, inbox_id: int, category_id: int | None = None, counterpart: str | None = None,
               account_id: int | None = None, from_account_id: int | None = None) -> int | None:
    """Move a waiting line into the transactions, as a category entry or as a transfer. Returns the new id."""
    row = conn.execute("SELECT * FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if row is None:
        return None
    direction = None
    if counterpart:
        direction = "in" if row["kind"] == "income" else "out"
        if counterpart == "people":
            account_id = from_account_id = None
        elif row["kind"] == "income":  # the money arrived on this very card; from_account_id says where it came from
            account_id = transfers.account_of_line(conn, row["external_id"])
        else:  # it left this card; account_id says where it went
            from_account_id = None
    new_id = conn.execute(
        """
        INSERT INTO transactions (date, kind, category_id, amount, currency, comment, source, external_id, direction, counterpart, account_id, from_account_id)
        VALUES (?, ?, ?, ?, ?, ?, 'mono', ?, ?, ?, ?, ?)
        """,
        (row["date"], "transfer" if counterpart else row["kind"], category_id, row["amount"], row["currency"],
         row["comment"], row["external_id"], direction, counterpart, account_id, from_account_id),
    ).lastrowid
    conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    return new_id


def make_jar_transfer(conn, inbox_id: int, jar_id: int) -> int | None:
    """A waiting card line is money going into a jar (expense) or coming back out of it (income)."""
    row = conn.execute("SELECT kind, amount, currency, date, comment, external_id FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    jar = conn.execute("SELECT currency FROM jars WHERE id = ?", (jar_id,)).fetchone()
    if row is None or jar is None or jar["currency"] != row["currency"]:
        return None
    new_id = conn.execute(
        """
        INSERT INTO transactions (date, kind, amount, currency, comment, source, external_id, jar_id, direction)
        VALUES (?, 'transfer', ?, ?, ?, 'mono', ?, ?, ?)
        """,
        (row["date"], row["amount"], row["currency"], row["comment"], row["external_id"], jar_id,
         "in" if row["kind"] == "expense" else "out"),
    ).lastrowid
    conn.execute("DELETE FROM inbox WHERE id = ?", (inbox_id,))
    return new_id


def sort_entry(conn, inbox_id: int, category_id: int) -> tuple[str, int] | None:
    """Give a waiting line its category. Returns (category name, new transaction id), or None if it can't be done."""
    row = conn.execute("SELECT kind FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    cat = conn.execute("SELECT name, kind FROM categories WHERE id = ? AND archived = 0", (category_id,)).fetchone()
    if row is None or cat is None or cat["kind"] != row["kind"]:
        return None
    return cat["name"], file_entry(conn, inbox_id, category_id=category_id)


def usable_account(conn, account_id: int | None, currency: str) -> bool:
    """An account that exists, is not archived and holds this currency (an account with no currency fits any)."""
    row = conn.execute("SELECT currency FROM accounts WHERE id = ? AND archived = 0", (account_id,)).fetchone()
    return row is not None and row["currency"] in (None, currency)


def make_transfer(conn, inbox_id: int, counterpart: str, account_id: int | None = None,
                  from_account_id: int | None = None) -> int | None:
    """A waiting line is a transfer to / from people, or between her own accounts: account_id is where the money went
    (a line that left a card), from_account_id where it came from (a line that arrived on a card). Returns the new id."""
    if counterpart not in COUNTERPARTS:
        return None
    line = conn.execute("SELECT currency FROM inbox WHERE id = ?", (inbox_id,)).fetchone()
    if line is None:
        return None
    for account in (account_id, from_account_id):
        if account is not None and not usable_account(conn, account, line["currency"]):
            return None
    return file_entry(conn, inbox_id, counterpart=counterpart, account_id=account_id, from_account_id=from_account_id)


def apply_rule(conn, inbox_id: int, rule: dict) -> bool:
    if rule["is_transfer"]:
        return make_transfer(conn, inbox_id, rule["transfer"], rule["account_id"]) is not None
    return sort_entry(conn, inbox_id, rule["category_id"]) is not None


def apply_to_waiting(conn) -> int:
    """Run every rule over the lines still waiting. Returns how many were sorted."""
    done = 0
    for line in unsorted(conn):
        rule = find(conn, line["comment"], line["kind"])
        if rule and apply_rule(conn, line["id"], rule):
            done += 1
    return done


def remember(conn, tx_id: int) -> tuple[str, int] | None:
    """Make a rule from a just-sorted entry: its description -> its category (or its kind of transfer),
    then run it over the lines still waiting. Returns (the rule's text, how many it sorted)."""
    tx = conn.execute(
        "SELECT kind, category_id, comment, counterpart, direction, account_id FROM transactions WHERE id = ?", (tx_id,)
    ).fetchone()
    pattern = clean_pattern(tx["comment"]) if tx else ""
    if not tx or not pattern:
        return None
    if tx["kind"] == "transfer":
        account = tx["account_id"] if tx["counterpart"] == "accounts" and tx["direction"] == "out" else None
        save_rule(conn, pattern, None, tx["counterpart"], account)
    else:
        save_rule(conn, pattern, tx["category_id"])
    return pattern, apply_to_waiting(conn)
