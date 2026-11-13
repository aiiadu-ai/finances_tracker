"""Transactions: a month, filtered by category and account (it all lives in the address)."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, transfers
from app.main import Filter, app, filtered_rows, month_totals

MONTH = "month=2026-11"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("OWNER_LOGIN", raising=False)
    with TestClient(app) as c:
        with db.connection() as conn:
            c.ids = seed(conn)
        yield c


def seed(conn):
    cat = lambda n: conn.execute("SELECT id FROM categories WHERE name = ?", (n,)).fetchone()["id"]
    cash, cash_eur = transfers.cash_account(conn, "UAH"), transfers.cash_account(conn, "EUR")
    c1 = transfers.ensure_mono_account(conn, "C1", "Mono Black ·0648 ₴", "UAH")
    e1 = transfers.ensure_mono_account(conn, "E1", "Mono Euro ·1111 €", "EUR")
    jar = conn.execute("INSERT INTO jars (name, currency) VALUES ('Trip', 'UAH')").lastrowid

    def tx(day, kind, amount, comment, currency="UAH", **fields):
        cols = {"date": day, "kind": kind, "amount": amount, "currency": currency, "comment": comment, **fields}
        return conn.execute(f"INSERT INTO transactions ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", list(cols.values())).lastrowid

    ids = {
        "lunch": tx("2026-11-05", "expense", 30000, "lunch", category_id=cat("Food"), account_id=cash),
        "latte": tx("2026-11-06", "expense", 8000, "latte", category_id=cat("Coffee"), account_id=cash),
        "shop": tx("2026-11-07", "expense", 50000, "shop", category_id=cat("Food"), source="mono", external_id="C1:a1"),
        "pay": tx("2026-11-08", "income", 1000000, "pay", category_id=cat("Salary"), source="mono", external_id="C1:a2"),
        "phone": tx("2026-11-09", "expense", 1500, "phone eur", "EUR", category_id=cat("Phone"), source="mono", external_id="E1:b1"),
        "tojar": tx("2026-11-10", "transfer", 20000, "to jar", jar_id=jar, direction="in", source="mono", external_id="C1:a3"),
        "atm": tx("2026-11-11", "transfer", 90000, "atm", counterpart="accounts", direction="out", account_id=cash, source="mono", external_id="C1:a4"),
        "friend": tx("2026-11-12", "transfer", 100000, "friend", counterpart="people", direction="out", source="mono", external_id="C1:a5"),
        "october": tx("2026-10-20", "expense", 99900, "october lunch", category_id=cat("Food"), account_id=cash),
    }
    ids.update(food=cat("Food"), coffee=cat("Coffee"), phone_cat=cat("Phone"), salary=cat("Salary"), cash=cash, c1=c1, e1=e1, cash_eur=cash_eur)
    return ids


def shift(first):
    """The month before `first`, as YYYY-MM."""
    return f"{first.year - (first.month == 1)}-{(first.month - 2) % 12 + 1:02d}"


def shown(client, query=""):
    """The comments of the entries the list shows, for /transactions?month=2026-11&<query>."""
    html = client.get(f"/transactions?{MONTH}&{query}").text
    comments = ["lunch", "latte", "shop", "pay", "phone eur", "to jar", "atm", "friend", "october lunch"]
    return {c for c in comments if f'class="tx-comment">{c}<' in html or f'class="tx-comment">{c} ' in html}


def totals(client, query=""):
    ids = client.ids
    with db.connection() as conn:
        flt = Filter(date(2026, 11, 1), [int(x) for x in query.get("cat", [])], False, [int(x) for x in query.get("acc", [])])
        return month_totals(filtered_rows(conn, flt))


# ---------- The month ----------
def test_the_list_is_one_month_and_has_arrows(client):
    got = shown(client)
    assert "october lunch" not in got and {"lunch", "latte", "shop", "pay", "phone eur", "to jar", "atm", "friend"} == got  # nothing is hidden without a filter
    page = client.get(f"/transactions?{MONTH}").text
    assert "November 2026" in page and "month=2026-10" in page                  # the arrow to the previous month
    assert "october lunch" in client.get("/transactions?month=2026-10").text
    this_month = date.today().replace(day=1)
    assert 'aria-label="Next month"' not in client.get(f"/transactions?month={this_month:%Y-%m}").text  # no month in the future
    assert 'aria-label="Next month"' in client.get(f"/transactions?month={shift(this_month)}").text      # but one from the past has it


def test_nonsense_in_the_address_is_ignored(client):
    page = client.get("/transactions?month=nope&cat=abc&acc=zz")
    assert page.status_code == 200 and "Filter" in page.text


# ---------- Categories ----------
def test_one_or_more_categories(client):
    ids = client.ids
    assert shown(client, f"cat={ids['food']}") == {"lunch", "shop"}                       # transfers have no category: left out
    assert shown(client, f"cat={ids['food']}&cat={ids['coffee']}") == {"lunch", "latte", "shop"}
    assert shown(client, f"cat={ids['salary']}") == {"pay"}


def test_totals_are_per_currency_income_minus_expenses(client):
    ids = client.ids
    page = client.get(f"/transactions?{MONTH}&cat={ids['food']}&cat={ids['phone_cat']}").text.replace(" ", " ").replace(" ", " ")
    assert "−800 ₴" in page and "−15 €" in page and "3 entries" in page          # 300 + 500 and 15 €, each in its own currency
    assert totals(client, {"cat": [ids["salary"]]}) == {"UAH": 1000000}


# ---------- Accounts ----------
def test_an_account_shows_what_happened_on_it_and_ignores_moves_between_own_accounts(client):
    ids = client.ids
    assert shown(client, f"acc={ids['cash']}") == {"lunch", "latte"}                      # the ATM move into cash is left out
    assert shown(client, f"acc={ids['c1']}") == {"shop", "pay", "friend"}                 # a bank line belongs to its card; people count, the jar / own-account moves do not
    assert shown(client, f"acc={ids['e1']}") == {"phone eur"}
    assert shown(client, f"acc={ids['cash']}&acc={ids['e1']}") == {"lunch", "latte", "phone eur"}   # any of the chosen accounts


def test_the_totals_of_an_account_include_money_sent_to_people(client):
    assert totals(client, {"acc": [client.ids["c1"]]}) == {"UAH": -50000 + 1000000 - 100000}   # the shop, the pay, the friend


def test_category_and_account_together_need_both(client):
    ids = client.ids
    assert shown(client, f"cat={ids['food']}&acc={ids['cash']}") == {"lunch"}
    assert shown(client, f"cat={ids['food']}&acc={ids['c1']}") == {"shop"}
    assert shown(client, f"cat={ids['coffee']}&acc={ids['c1']}") == set()


# ---------- Transfers ----------
def test_the_transfers_pill_brings_them_back(client):
    ids = client.ids
    assert shown(client, "cat=transfer") == {"to jar", "atm", "friend"}
    assert shown(client, f"cat=transfer&acc={ids['c1']}") == {"to jar", "atm", "friend"}
    assert shown(client, f"cat=transfer&acc={ids['cash']}") == {"atm"}                    # drawn from a card into cash
    assert shown(client, f"cat=transfer&cat={ids['coffee']}") == {"latte", "to jar", "atm", "friend"}


# ---------- The bar itself ----------
def test_pills_are_links_that_toggle_and_the_clear_button_resets(client):
    ids = client.ids
    page = client.get(f"/transactions?{MONTH}").text
    assert f"/transactions?{MONTH}&amp;cat={ids['food']}" in page and "Clear" not in page.split("flt-panel")[1].split("</details>")[0]  # no Clear without a filter
    on = client.get(f"/transactions?{MONTH}&cat={ids['food']}&acc={ids['cash']}").text
    panel = on.split("flt-panel")[1].split("</details>")[0]
    assert panel.count('aria-checked="true"') == 2 and "flt-badge" in on and ">2<" in on.split("flt-badge")[1][:20]
    # the chosen Food pill points at the same filter without Food; the chosen account keeps only itself
    assert f'href="/transactions?{MONTH}&amp;acc={ids["cash"]}" role="checkbox" aria-checked="true"><span>Food' in on
    assert f'class="btn btn-outline flt-clear" href="/transactions?{MONTH}">Clear' in on


def test_groups_glow_but_carry_no_labels_and_income_has_its_own_row(client):
    panel = client.get(f"/transactions?{MONTH}").text.split("flt-panel")[1].split("</details>")[0]
    assert "g-essentials" in panel and "g-fixed" in panel and "g-fun" in panel
    assert ">Essentials<" not in panel and ">Fixed<" not in panel and ">Fun<" not in panel
    assert ">Income<" in panel and ">Accounts<" in panel and ">Transfers<" in panel and "Cash ₴" in panel and "Mono Black" in panel


def test_an_open_transaction_keeps_the_filter_in_its_links(client):
    ids = client.ids
    query = f"{MONTH}&cat={ids['food']}"
    page = client.get(f"/transactions?tx={ids['lunch']}&{query}").text
    escaped = query.replace("&", "&amp;")
    assert f'href="/transactions?{escaped}"' in page                                     # back to the same filtered month
    assert f'href="/transactions/{ids["lunch"]}/edit?{escaped}"' in page
    assert f"/transactions?tx={ids['shop']}&amp;{escaped}" in page                       # other entries in the list keep it too


def test_an_empty_result_says_so(client):
    ids = client.ids
    assert "Nothing matches this filter." in client.get(f"/transactions?{MONTH}&cat={ids['coffee']}&acc={ids['c1']}").text
    assert "Nothing in this month yet." in client.get("/transactions?month=2020-01").text
