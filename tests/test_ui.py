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


def test_long_category_names_break_by_syllable_in_the_narrow_chart():
    from app.main import breakable
    assert breakable("Entertainment").split("­") == ["En", "ter", "tain", "ment"]
    assert breakable("Electronics").replace("­", "") == "Electronics"
    assert breakable("Food") == "Food" and breakable("Skin & body care") == "Skin & body care"
    assert "­" in breakable("Averyverylongname") and breakable("Averyverylongname").replace("­", "") == "Averyverylongname"


def test_category_chart_has_horizontal_names_and_filled_bars_but_groups_are_outlined(client):
    page = client.get("/analytics").text
    css = client.get("/static/style.css").text
    assert "vbars-cats" in page and "writing-mode: vertical-rl" not in css.split("Category bars")[1].split("Forecast")[0]
    group_rule = css.split(".vbars-groups .vc-bar {")[1].split("}")[0]
    assert "box-shadow" in group_rule and "var(--glow)" in group_rule and "background: var(--surface-raised)" in group_rule
    cats_rule = css.split(".vbars-cats .vc-bar {")[1].split("}")[0]
    assert "background: var(--fill)" in cats_rule
    assert all(f"--fill-{g}:" in css for g in ("essentials", "fixed", "fun"))


def test_chosen_group_has_no_outline_around_the_column(client):
    css = client.get("/static/style.css").text
    chosen = [rule for rule in css.split("}") if '.vbars-groups .vcol[aria-pressed="true"]' in rule]
    assert chosen and not any("inset" in rule or "background" in rule for rule in chosen)
    assert any(".vc-bar" in rule and "box-shadow" in rule for rule in chosen)  # only the bar glows more
    assert any(".vc-lab" in rule and "var(--ink)" in rule for rule in chosen)  # and its numbers are full ink
