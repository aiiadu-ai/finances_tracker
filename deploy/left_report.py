#!/usr/bin/env python3
"""Read-only: the numbers behind the Year row "Left at end" for the running month and the next two.

  python3 deploy/left_report.py                     # the database in data/ (or FINANCE_DB)
  FINANCE_DB=/path/to/copy.db python3 deploy/left_report.py --today 2026-11-17

It opens the database read-only, changes nothing, and prints in plain money (not rounded down to hundreds; the page
rounds down). Per currency: the plan chain, what really happened, what the plan still expects this month category by
category, and the forecast for the months to come.
"""

import argparse
import os
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import wealth  # noqa: E402
from app.db import PROJECT_ROOT  # noqa: E402


def money(amount: int, sign: str = "") -> str:
    whole, cents = divmod(abs(amount), 100)
    text = f"{whole:,}".replace(",", " ") + (f".{cents:02d}" if cents else "")
    return ("−" if amount < 0 else ("+" if sign == "+" and amount > 0 else "")) + text


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--today", default=date.today().isoformat())
    today = date.fromisoformat(parser.parse_args().today)
    path = Path(os.environ.get("FINANCE_DB", PROJECT_ROOT / "data" / "finance.db"))
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    start = wealth.get_start(conn)
    if not start:
        raise SystemExit("No starting money set (Settings → Money): nothing to show.")
    print(f"Today {today}. Start {start['date']}: {money(start['UAH'])} ₴ and {money(start['EUR'])} €.")
    months = [(today.year + (today.month - 1 + i) // 12, (today.month - 1 + i) % 12 + 1) for i in range(3)]
    results = {}
    for year in {y for y, _ in months}:
        results[year] = wealth.left_at_month_end(conn, start, year, today)
    still = wealth.remaining_plan(conn, today)
    for code, sign in (("UAH", "₴"), ("EUR", "€")):
        print(f"\n=== {sign} " + "=" * 50)

        def cell(series, y, m):
            c = results[y][code][series]["months"][m - 1]
            return None if c["value"] is None else (c["value"], c["forecast"])

        def show(series, y, m, signed=False):
            c = cell(series, y, m)
            return "—" if c is None else ("≈" if c[1] else "") + money(c[0], "+" if signed else "") + f" {sign}"

        print("Month         Plan only            Actual / forecast (≈)       Actual − Plan")
        for y, m in months:
            print(f"{y}-{m:02d}      {show('plan', y, m):<20} {show('actual', y, m):<27} {show('diff', y, m, True)}")
        print(f"\nStill expected this month ({today:%Y-%m}), category by category: the plan minus what is done, never below 0")
        for kind, label in (("expense", "spending"), ("income", "income")):
            for r in still[code][kind]:
                print(f"  {label:<9} {r['name']:<18} plan {money(r['plan']):>10}  done {money(r['done']):>10}  still {money(r['left']):>10}")
        print(f"  Net still expected this month: {money(still[code]['net'], '+')} {sign}")
        now = cell("actual", *months[0])
        if now:
            print(f"  So far {money(now[0])} {sign}  →  this month ends at {money(now[0] + still[code]['net'])} {sign} (forecast)")
        if len(months) > 1:
            (y1, m1), (y2, m2) = months[0], months[1]
            nxt, plan_step = cell("actual", y2, m2), cell("plan", y2, m2)[0] - cell("plan", y1, m1)[0]
            print(f"  Next month: the end of this month + its plan {money(plan_step, '+')} {sign} = {money(nxt[0])} {sign}")
            if now:
                print(f"  (the old way forgot the rest of this month: {money(now[0] + plan_step)} {sign})")


if __name__ == "__main__":
    main()
