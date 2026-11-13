"""SQLite database: connection, schema and starting categories."""

import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

INCOME_CATEGORIES = ["Salary", "Gifts", "Refunds", "Found money", "Extra income"]
EXPENSE_CATEGORIES = [
    "Food",
    "Coffee",
    "Gifts",
    "Pets",
    "Electronics",
    "AI",
    "Phone",
    "Installments",
    "Health",
    "Skin & body care",
    "Makeup",
    "Hygiene",
    "Education",
    "Clothing",
    "Entertainment",
    "Transportation",
    "Sport",
    "Tax",
    "Other",
]

# Groups only tint the category buttons and list rows (no labels). Income has no groups.
EXPENSE_GROUPS = {
    "essentials": ["Food", "Health", "Hygiene", "Transportation", "Pets", "Skin & body care"],
    "fixed": ["Phone", "AI", "Installments", "Tax", "Sport"],
    "fun": ["Coffee", "Entertainment", "Gifts", "Clothing", "Makeup", "Electronics", "Education", "Other"],
}
GROUP_OF = {name: group for group, names in EXPENSE_GROUPS.items() for name in names}

START_QUOTES = [
    "Should have? Could have? Would have? Did.",
    "It's not so hard.",
    "A lot goes wrong before everything goes right.",
    "Start.",
    "I refuse to disappoint myself.",
    "Keep going.",
    "F*ck the mood, follow the plan.",
    "I want smiles on results day.",
    "Goal: outdo me.",
    "I've already worked so hard.",
]

# amount is a whole number of kopiykas / cents, so sums never pick up rounding errors.
SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id       INTEGER PRIMARY KEY,
    kind     TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    name     TEXT NOT NULL,
    "group"  TEXT CHECK ("group" IN ('essentials', 'fixed', 'fun')),
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    UNIQUE (kind, name)
);

-- A jar: a savings goal in one currency. opening = money already in it, outside any month's balance.
-- my_rate: hundredths of a hryvnia per euro, set by hand (euro jars only, for the "about N hryvnias" line).
CREATE TABLE IF NOT EXISTS jars (
    id       INTEGER PRIMARY KEY,
    name     TEXT NOT NULL UNIQUE,
    currency TEXT NOT NULL CHECK (currency IN ('UAH', 'EUR')),
    target   INTEGER CHECK (target > 0),
    my_rate  INTEGER CHECK (my_rate > 0),
    quote    TEXT NOT NULL DEFAULT '',
    opening  INTEGER NOT NULL DEFAULT 0 CHECK (opening >= 0),
    -- Shared: the only jars a viewer (for example her partner) can see.
    shared   INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1)),
    -- The Monobank jar this one follows (its money comes from the bank statement). One site jar per Mono jar.
    mono_id  TEXT,
    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

CREATE TABLE IF NOT EXISTS quotes (
    id   INTEGER PRIMARY KEY,
    text TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS transactions (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('income', 'expense', 'transfer')),
    category_id INTEGER REFERENCES categories (id),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    currency    TEXT NOT NULL CHECK (currency IN ('UAH', 'EUR')),
    comment     TEXT NOT NULL DEFAULT '',
    source      TEXT NOT NULL DEFAULT 'site'
                CHECK (source IN ('site', 'telegram', 'mono', 'agricole')),
    external_id TEXT UNIQUE,
    jar_id      INTEGER REFERENCES jars (id),
    -- Transfers only: 'in' puts money into the jar, 'out' takes it back. paid_uah: a euro deposit
    -- that was paid with hryvnias (those hryvnias are no longer free).
    direction   TEXT CHECK (direction IN ('in', 'out')),
    paid_uah    INTEGER CHECK (paid_uah > 0),
    -- Transfers that are not to a jar: "people" (sent to / received from) or "accounts" (between her own
    -- accounts; account_id is the account the money arrived in). Every non-transfer has a category.
    counterpart TEXT CHECK (counterpart IN ('people', 'accounts')),
    account_id  INTEGER REFERENCES accounts (id),
    -- Between her own accounts: the account the money came from (for a line that arrived on a card).
    from_account_id INTEGER REFERENCES accounts (id),
    CHECK (kind = 'transfer' OR counterpart IS NULL),
    CHECK ((kind = 'transfer') = (category_id IS NULL)),
    CHECK (kind <> 'transfer' OR (jar_id IS NOT NULL) <> (counterpart IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS transactions_date ON transactions (date);

-- Bank rules: "if the description contains this text, it is this category (or a transfer)".
-- key is the lower-cased pattern, so the same text is never stored twice.
CREATE TABLE IF NOT EXISTS rules (
    id          INTEGER PRIMARY KEY,
    pattern     TEXT NOT NULL,
    key         TEXT NOT NULL UNIQUE,
    category_id INTEGER REFERENCES categories (id),
    is_transfer INTEGER NOT NULL DEFAULT 0 CHECK (is_transfer IN (0, 1)),
    -- For a transfer rule: "people" or "accounts" (empty = accounts); account_id: where the money goes.
    transfer    TEXT CHECK (transfer IN ('people', 'accounts')),
    account_id  INTEGER REFERENCES accounts (id),
    CHECK ((is_transfer = 1) = (category_id IS NULL))
);

-- Aiia's own places where money lies (cards, cash...). The Monobank cards are added by the sync.
CREATE TABLE IF NOT EXISTS accounts (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    mono_id      TEXT UNIQUE,
    currency     TEXT CHECK (currency IN ('UAH', 'EUR')),
    kind         TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('card', 'cash', 'other')),
    -- Cash and other hand-kept accounts: what was in it on opening_date. A Monobank card (kind 'card') has no
    -- opening: its balance is what the bank says (balance, at balance_at = unix time of the reading).
    opening      INTEGER NOT NULL DEFAULT 0,
    opening_date TEXT,
    balance      INTEGER,
    balance_at   INTEGER,
    archived     INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1))
);

-- Bank lines waiting for a category. They are NOT transactions yet: nothing counts them until sorted.
CREATE TABLE IF NOT EXISTS inbox (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    currency    TEXT NOT NULL CHECK (currency IN ('UAH', 'EUR')),
    comment     TEXT NOT NULL DEFAULT '',
    external_id TEXT NOT NULL UNIQUE,
    notified    INTEGER NOT NULL DEFAULT 0 CHECK (notified IN (0, 1))
);

-- Bank lines that were used up on purpose (for example the jar side of a transfer), so they never come back.
CREATE TABLE IF NOT EXISTS seen_lines (
    external_id TEXT PRIMARY KEY
);

-- Small notes for the bank sync (when each card was last read).
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- The year plan: what is expected for one category in one month (income and expense). No row = no plan.
CREATE TABLE IF NOT EXISTS plans (
    month       TEXT NOT NULL CHECK (month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
    category_id INTEGER NOT NULL REFERENCES categories (id),
    currency    TEXT NOT NULL CHECK (currency IN ('UAH', 'EUR')),
    amount      INTEGER NOT NULL CHECK (amount > 0),
    PRIMARY KEY (month, category_id, currency)
);
"""


def db_path() -> Path:
    return Path(os.environ.get("FINANCE_DB", PROJECT_ROOT / "data" / "finance.db"))


@contextmanager
def connection():
    """Open the database, save changes on success, undo them on error, always close."""
    conn = sqlite3.connect(db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def migrate_accounts(conn) -> None:
    """Accounts get a currency, a kind and an opening balance; transactions get from_account_id.
    Cash accounts (one per currency) are made, and the entries from the site and the bot go to them."""
    account_columns = {row["name"] for row in conn.execute("PRAGMA table_info(accounts)")}
    for name, ddl in (
        ("currency", "TEXT CHECK (currency IN ('UAH', 'EUR'))"),
        ("kind", "TEXT NOT NULL DEFAULT 'other' CHECK (kind IN ('card', 'cash', 'other'))"),
        ("opening", "INTEGER NOT NULL DEFAULT 0"),
        ("opening_date", "TEXT"),
        ("balance", "INTEGER"),
        ("balance_at", "INTEGER"),
    ):
        if name not in account_columns:
            conn.execute(f"ALTER TABLE accounts ADD COLUMN {name} {ddl}")
    tx_columns = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "from_account_id" not in tx_columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN from_account_id INTEGER REFERENCES accounts (id)")
    conn.execute("UPDATE accounts SET kind = 'card' WHERE mono_id IS NOT NULL AND kind = 'other'")
    # The cash accounts: an existing account called Cash is taken as the hryvnia cash; the euro one is made.
    start = conn.execute("SELECT value FROM meta WHERE key = 'start_date'").fetchone()
    day = start["value"] if start else None
    for currency, label in (("UAH", "Cash ₴"), ("EUR", "Cash €")):
        have = conn.execute("SELECT id FROM accounts WHERE kind = 'cash' AND currency = ?", (currency,)).fetchone()
        if have:
            continue
        old = conn.execute("SELECT id FROM accounts WHERE lower(name) = 'cash' AND mono_id IS NULL AND kind = 'other'").fetchone() if currency == "UAH" else None
        if old:
            conn.execute("UPDATE accounts SET kind = 'cash', currency = 'UAH', opening_date = COALESCE(opening_date, ?) WHERE id = ?", (day, old["id"]))
        else:
            conn.execute("INSERT INTO accounts (name, kind, currency, opening_date) VALUES (?, 'cash', ?, ?)", (label, currency, day))
    for row in conn.execute("SELECT id, currency FROM accounts WHERE kind = 'cash'").fetchall():
        conn.execute(
            """
            UPDATE transactions SET account_id = ? WHERE account_id IS NULL AND currency = ? AND source IN ('site', 'telegram')
              AND (kind IN ('income', 'expense') OR (kind = 'transfer' AND jar_id IS NOT NULL))
            """,
            (row["id"], row["currency"]),
        )


def move_budgets_into_plans(conn) -> None:
    """The separate budgets table is gone: a month's limit is the planned amount from the year plan.
    Old limits are copied into the plan where that month has no plan yet (a plan that is already there stays),
    then the table is dropped."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'budgets'").fetchone():
        return
    conn.execute(
        """
        INSERT OR IGNORE INTO plans (month, category_id, currency, amount)
        SELECT b.month, b.category_id, b.currency, b.amount FROM budgets b
        JOIN categories c ON c.id = b.category_id WHERE c.kind = 'expense'
        """
    )
    conn.execute("DROP TABLE budgets")


def move_waiting_to_inbox(conn) -> None:
    """Bank lines that were saved without a category go to the waiting list (inbox), and transfers that
    are not to a jar get the "accounts" kind. Needed once, for databases made by an earlier version."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "external_id" in columns:
        notified = "notified" if "notified" in columns else "0"
        conn.execute(
            f"""
            INSERT OR IGNORE INTO inbox (date, kind, amount, currency, comment, external_id, notified)
            SELECT date, kind, amount, currency, comment, external_id, {notified} FROM transactions
            WHERE kind IN ('income', 'expense') AND category_id IS NULL AND external_id IS NOT NULL
            """
        )
        conn.execute("DELETE FROM transactions WHERE kind IN ('income', 'expense') AND category_id IS NULL")
    conn.execute("UPDATE transactions SET counterpart = 'accounts' WHERE kind = 'transfer' AND jar_id IS NULL AND counterpart IS NULL")


def rebuild_transactions(conn) -> None:
    """Older databases have older rules for the table (checks, a spare column): rebuild it once with the
    current definition. Rows and ids are kept."""
    sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'transactions'").fetchone()["sql"]
    if "(counterpart IS NOT NULL)" in sql:
        return
    ddl = re.search(r"CREATE TABLE IF NOT EXISTS transactions \(.*?\n\);", SCHEMA, re.S).group(0)
    conn.execute(ddl.replace("IF NOT EXISTS transactions", "transactions_new", 1))
    old = [row["name"] for row in conn.execute("PRAGMA table_info(transactions)")]
    new = {row["name"] for row in conn.execute("PRAGMA table_info(transactions_new)")}
    names = ", ".join(name for name in old if name in new)
    conn.execute(f"INSERT INTO transactions_new ({names}) SELECT {names} FROM transactions")
    conn.execute("DROP TABLE transactions")
    conn.execute("ALTER TABLE transactions_new RENAME TO transactions")
    conn.execute("CREATE INDEX IF NOT EXISTS transactions_date ON transactions (date)")


def init_db() -> None:
    """Create the tables. Starting categories are added only to an empty table."""
    db_path().parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        had_quotes = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'quotes'").fetchone()
        conn.executescript(SCHEMA)
        if not had_quotes:  # the starting quotes go in once, so deleting them later sticks
            conn.executemany("INSERT INTO quotes (text) VALUES (?)", [(q,) for q in START_QUOTES])
        tx_columns = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)")}
        if "direction" not in tx_columns:  # a database made before jars: add the new columns
            conn.execute("ALTER TABLE transactions ADD COLUMN direction TEXT CHECK (direction IN ('in', 'out'))")
            conn.execute("ALTER TABLE transactions ADD COLUMN paid_uah INTEGER CHECK (paid_uah > 0)")
        if "counterpart" not in tx_columns:
            conn.execute("ALTER TABLE transactions ADD COLUMN counterpart TEXT CHECK (counterpart IN ('people', 'accounts'))")
            conn.execute("ALTER TABLE transactions ADD COLUMN account_id INTEGER REFERENCES accounts (id)")
        jar_columns = {row["name"] for row in conn.execute("PRAGMA table_info(jars)")}
        if "shared" not in jar_columns:
            conn.execute("ALTER TABLE jars ADD COLUMN shared INTEGER NOT NULL DEFAULT 0 CHECK (shared IN (0, 1))")
        if "mono_id" not in jar_columns:
            conn.execute("ALTER TABLE jars ADD COLUMN mono_id TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jars_mono_id ON jars (mono_id) WHERE mono_id IS NOT NULL")
        rule_columns = {row["name"] for row in conn.execute("PRAGMA table_info(rules)")}
        if "transfer" not in rule_columns:
            conn.execute("ALTER TABLE rules ADD COLUMN transfer TEXT CHECK (transfer IN ('people', 'accounts'))")
            conn.execute("ALTER TABLE rules ADD COLUMN account_id INTEGER REFERENCES accounts (id)")
        migrate_accounts(conn)
        move_budgets_into_plans(conn)
        move_waiting_to_inbox(conn)
        rebuild_transactions(conn)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(categories)")}
        if "group" not in columns:
            # A database made before groups existed: add the column and fill it once.
            conn.execute(
                'ALTER TABLE categories ADD COLUMN "group" TEXT '
                "CHECK (\"group\" IN ('essentials', 'fixed', 'fun'))"
            )
            conn.executemany(
                "UPDATE categories SET \"group\" = ? WHERE kind = 'expense' AND name = ?",
                [(group, name) for name, group in GROUP_OF.items()],
            )
        if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
            rows = [("income", name, None) for name in INCOME_CATEGORIES]
            rows += [("expense", name, GROUP_OF[name]) for name in EXPENSE_CATEGORIES]
            conn.executemany('INSERT INTO categories (kind, name, "group") VALUES (?, ?, ?)', rows)
