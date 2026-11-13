"""Hidden blocks (app/ui.py): left out of the page, but still counted and easy to bring back."""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db, ui
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    monkeypatch.delenv("OWNER_LOGIN", raising=False)
    with TestClient(app) as c:
        with db.connection() as conn:
            food = conn.execute("SELECT id FROM categories WHERE name = 'Food'").fetchone()["id"]
            today = date.today()
            conn.execute("INSERT INTO plans VALUES (?, ?, 'UAH', 500000)", (today.strftime("%Y-%m"), food))
            conn.execute("INSERT INTO transactions (date, kind, category_id, amount, currency, comment) VALUES (?, 'expense', ?, 12300, 'UAH', 'lunch')",
                         (today.isoformat(), food))
        yield c


def test_these_blocks_are_hidden_for_now():
    assert ui.HIDDEN_BLOCKS == {"balance", "people", "recent", "change", "trend", "forecast"}


def test_home_keeps_the_wanted_blocks_and_drops_the_hidden_ones(client):
    page = client.get("/").text
    for kept in ("Income", "Expenses", "In jars", "Free", "left this month", "Essentials", "Categories", "Jars"):
        assert kept in page, kept
    assert 'aria-label="Balance"' not in page and 'aria-label="Recent"' not in page
    assert 'aria-label="Sent to people"' not in page and "grid-4" in page


def test_analytics_keeps_where_the_money_goes_and_plan_against_actual(client):
    page = client.get("/analytics").text
    assert "Where the money goes" in page and "Plan ↔ actual" in page
    for gone in ("Forecast to the end of the month", "Expenses by month", "Income by month", "so far"):
        assert gone not in page, gone


def test_every_hidden_block_comes_back_when_its_name_is_removed(client, monkeypatch):
    monkeypatch.setattr(ui, "HIDDEN_BLOCKS", frozenset())
    home, analytics = client.get("/").text, client.get("/analytics").text
    assert 'aria-label="Balance"' in home and 'aria-label="Recent"' in home and "grid-4" not in home
    for back in ("Forecast to the end of the month", "Expenses by month", "Income by month", "this month so far"):
        assert back in analytics, back


def test_the_numbers_behind_hidden_blocks_are_still_counted(client):
    from app import analytics
    with db.connection() as conn:
        built = analytics.build(conn, "month", date.today().replace(day=1), date.today(), "UAH")
    assert built["forecast"] is not None and built["dynamics"]["expense"]["now"] == 12300 and built["trend"]["expense"]["cols"]


def test_income_is_green_and_expenses_are_red_on_home(client):
    page = client.get("/").text
    assert 'class="card stat stat-income" aria-label="Income"' in page
    assert 'class="card stat stat-expense" aria-label="Expenses"' in page
    assert 'aria-label="In jars"' in page and "stat-income" not in page.split('aria-label="In jars"')[1].split("</section>")[0]
    css = client.get("/static/style.css").text
    assert ".stat-income .stat-value { color: var(--income); }" in css and ".stat-expense .stat-value { color: var(--expense); }" in css
    assert css.count("--income:") == 3 and css.count("--expense:") == 3  # light, dark by system, dark chosen by hand
