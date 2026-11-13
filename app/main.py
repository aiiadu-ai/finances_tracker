"""The website: home with the month against the plan, the year plan, analytics, jars, settings and transactions."""

import re
from contextlib import asynccontextmanager
from datetime import date
from itertools import groupby
from pathlib import Path
from urllib.parse import quote as quote_url, urlsplit

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import access, analytics, db, ui, jars, monthly, plans, rules, transfers, wealth
from .env import load_env

APP_DIR = Path(__file__).resolve().parent
CURRENCY_SYMBOLS = {"UAH": "₴", "EUR": "€"}
MAX_AMOUNT = 99_999_999_999  # in kopiykas / cents, i.e. 999 999 999.99
MAX_COMMENT = 500


def parse_amount(raw: str) -> int:
    """Turn what was typed ("1 234,50") into kopiykas / cents (123450)."""
    cleaned = re.sub(r"\s", "", raw)
    if not re.fullmatch(r"[0-9]+([.,][0-9]{1,2})?", cleaned):
        raise ValueError("not an amount")
    whole, _, fraction = cleaned.replace(",", ".").partition(".")
    amount = int(whole) * 100 + int(fraction.ljust(2, "0"))
    if not 0 < amount <= MAX_AMOUNT:
        raise ValueError("out of range")
    return amount


def amount_for_input(amount: int) -> str:
    whole, cents = divmod(amount, 100)
    return f"{whole}.{cents:02d}" if cents else str(whole)


def money(amount: int, currency: str) -> str:
    if amount < 0:
        return "−" + money(-amount, currency)
    whole, cents = divmod(amount, 100)
    text = f"{whole:,}".replace(",", " ")
    if cents:
        text += f".{cents:02d}"
    return f"{text} {CURRENCY_SYMBOLS[currency]}"


def day_label(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return f"{day:%a} · {day.day} {day:%b %Y}"


def short_date(iso_date: str) -> str:
    day = date.fromisoformat(iso_date)
    return f"{day.day} {day:%b}"


def parse_month(raw: str) -> date | None:
    """'2026-11' -> first day of that month, or None if it is not a sensible month."""
    if not re.fullmatch(r"20[0-9]{2}-(0[1-9]|1[0-2])", raw):
        return None
    return date.fromisoformat(raw + "-01")


def shift_month(first: date, delta: int) -> date:
    index = first.year * 12 + first.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


load_env()  # OWNER_LOGIN and other settings from .env (the real environment wins)


def page_context(request: Request) -> dict:
    return {"viewer": getattr(request.state, "role", "owner") == "viewer", "hidden": ui.HIDDEN_BLOCKS}


templates = Jinja2Templates(directory=APP_DIR / "templates", context_processors=[page_context])
templates.env.filters["money"] = money
templates.env.filters["short_date"] = short_date


def plain(amount: int | None) -> str:
    """An amount for a table cell: no currency sign; a dash when there is nothing to show."""
    if amount is None:
        return "—"
    return money(amount, "UAH").rsplit("\u00a0", 1)[0]


templates.env.filters["plain"] = plain
templates.env.filters["compact"] = analytics.compact

# Where a long word may break in a narrow bar chart (soft hyphens, shown as "-" only when the word breaks).
SYLLABLES = {
    "entertainment": "en-ter-tain-ment", "electronics": "elec-tron-ics", "education": "edu-ca-tion",
    "transportation": "trans-por-ta-tion", "installments": "in-stall-ments", "clothing": "cloth-ing",
    "hygiene": "hy-giene", "makeup": "make-up",
}


def breakable(text: str) -> str:
    """The text with soft hyphens inside long words: the known category names by syllable, others every few letters."""
    words = []
    for word in str(text).split(" "):
        key = word.lower()
        if key in SYLLABLES:
            parts = SYLLABLES[key].split("-")
            out, pos = [], 0
            for part in parts:
                out.append(word[pos : pos + len(part)])
                pos += len(part)
            word = "\u00ad".join(out)
        elif len(word) > 8:
            word = "\u00ad".join(word[i : i + 4] for i in range(0, len(word), 4))
        words.append(word)
    return " ".join(words)


templates.env.filters["breakable"] = breakable


def asset(name: str) -> str:
    """A static file's address with its modification time on the end, so after an update the
    browser fetches the new file instead of showing an old saved copy."""
    stamp = int((APP_DIR / "static" / name).stat().st_mtime)
    return f"/static/{name}?v={stamp}"


templates.env.globals["asset"] = asset


@asynccontextmanager
async def lifespan(_: FastAPI):
    db.init_db()
    yield


app = FastAPI(title="finance", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")


# ---------- Installable app: manifest and a tiny service worker (both must live at the site's root) ----------
@app.get("/manifest.webmanifest", include_in_schema=False)
def manifest():
    return FileResponse(APP_DIR / "static" / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(
        APP_DIR / "static" / "sw.js",
        media_type="text/javascript",
        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
    )


@app.middleware("http")
async def keep_viewers_to_shared_jars(request: Request, call_next):
    """A viewer (not the owner) sees only /jars with the shared jars, read only. Everything else is closed."""
    path = request.url.path
    if "//" in path and request.method == "GET":  # an address typed with a double slash: send to the right one
        return RedirectResponse(re.sub(r"/{2,}", "/", path) + (f"?{request.url.query}" if request.url.query else ""), status_code=307)
    request.state.role = access.role(request)
    if request.state.role == "viewer" and not access.viewer_may_open(request):
        if request.method == "GET" and request.url.path in ("/", "/home"):
            return RedirectResponse("/jars", status_code=303)
        return PlainTextResponse("This part is private.", status_code=403)
    return await call_next(request)


@app.get("/whoami", include_in_schema=False)
def whoami(request: Request):
    return templates.TemplateResponse(
        request, "whoami.html",
        {
            "login": access.login_of(request),
            "proxied": access.proxied(request),
            "role": request.state.role,
            "configured": bool(access.owners()),
        },
    )


@app.middleware("http")
async def block_cross_site_posts(request: Request, call_next):
    """There is no login, so stop other websites from submitting forms to this one."""
    origin = request.headers.get("origin")
    if (
        request.method == "POST"
        and origin
        and urlsplit(origin).netloc != request.headers.get("host")
    ):
        return PlainTextResponse("Cross-site request blocked.", status_code=403)
    return await call_next(request)


def tx_form(
    kind: str = Form(""),
    category_id: str = Form(""),
    amount: str = Form(""),
    currency: str = Form(""),
    comment: str = Form(""),
    tx_date: str = Form("", alias="date"),
    jar_id: str = Form(""),
    direction: str = Form(""),
    paid: str = Form(""),
) -> dict:
    return {
        "jar_id": jar_id,
        "direction": direction,
        "paid": paid.strip(),
        "kind": kind,
        "category_id": category_id,
        "amount": amount.strip(),
        "currency": currency,
        "comment": comment.strip(),
        "date": tx_date.strip(),
    }


def load_categories(conn, keep_id: int | None = None) -> dict:
    """Active categories by kind, plus the one a transaction being edited already uses."""
    # Grouped so that same-tinted buttons sit together.
    rows = conn.execute(
        """
        SELECT id, kind, name, "group" AS grp FROM categories
        WHERE archived = 0 OR id = ?
        ORDER BY CASE "group" WHEN 'essentials' THEN 0 WHEN 'fixed' THEN 1 WHEN 'fun' THEN 2 ELSE 3 END, id
        """,
        (keep_id,),
    ).fetchall()
    return {kind: [row for row in rows if row["kind"] == kind] for kind in ("expense", "income")}


def validate(conn, form: dict, keep_id: int | None = None) -> tuple[dict, dict]:
    """Return (clean values for the database, error messages by field).

    kind "jar" is a transfer: the jar decides the currency and there is no category.
    """
    errors = {}
    clean = {"comment": form["comment"], "category_id": None, "jar_id": None, "direction": None, "paid_uah": None}
    is_jar = form["kind"] == "jar"

    if form["kind"] not in ("expense", "income", "jar"):
        errors["kind"] = "Choose expense, income or jar."
    clean["kind"] = "transfer" if is_jar else form["kind"]

    if is_jar:
        jar = next((j for j in jars.list_jars(conn, keep_id=keep_jar_id(conn, keep_id)) if str(j["id"]) == form["jar_id"]), None)
        if jar is None:
            errors["jar_id"] = "Choose a jar."
        else:
            clean["jar_id"], clean["currency"] = jar["id"], jar["currency"]
        if form["direction"] in ("in", "out"):
            clean["direction"] = form["direction"]
        else:
            errors["direction"] = "Choose add money or withdraw."
        if form["paid"]:
            if jar is None or jar["currency"] != "EUR" or form["direction"] != "in":
                errors["paid"] = "Only money added to a euro jar can be paid in ₴."
            else:
                try:
                    clean["paid_uah"] = parse_amount(form["paid"])
                except ValueError:
                    errors["paid"] = "Enter the hryvnias paid, like 4200 or 4200.50."
    else:
        categories = load_categories(conn, keep_id).get(form["kind"], [])
        matching = [row["id"] for row in categories if str(row["id"]) == form["category_id"]]
        if matching:
            clean["category_id"] = matching[0]
        elif "kind" not in errors:
            errors["category_id"] = f"Choose an {form['kind']} category."
        if form["currency"] not in CURRENCY_SYMBOLS:
            errors["currency"] = "Choose ₴ or €."
        clean["currency"] = form["currency"]

    try:
        clean["amount"] = parse_amount(form["amount"])
    except ValueError:
        errors["amount"] = "Enter an amount above zero, like 80 or 80.50."

    if len(form["comment"]) > MAX_COMMENT:
        errors["comment"] = f"Keep the comment under {MAX_COMMENT} characters."

    try:
        clean["date"] = date.fromisoformat(form["date"]).isoformat()
    except ValueError:
        errors["date"] = "Choose a date."

    return clean, errors


def keep_jar_id(conn, tx_id: int | None) -> int | None:
    """The jar an entry being edited already uses (it stays selectable even if archived)."""
    if tx_id is None:
        return None
    row = conn.execute("SELECT jar_id FROM transactions WHERE id = ?", (tx_id,)).fetchone()
    return row["jar_id"] if row else None


TX_SELECT = """
    SELECT t.*, c.name AS category, c."group" AS grp, j.name AS jar, a.name AS account,
           COALESCE(
               c.name,
               CASE WHEN t.jar_id IS NOT NULL THEN
                        CASE t.direction WHEN 'out' THEN 'From ' ELSE 'To ' END || COALESCE(j.name, 'a jar')
                    WHEN t.counterpart = 'people' THEN
                        CASE t.direction WHEN 'in' THEN 'Received from people' ELSE 'Sent to people' END
                    ELSE 'To ' || COALESCE(a.name, 'my account') END
           ) AS title
    FROM transactions t
    LEFT JOIN categories c ON c.id = t.category_id
    LEFT JOIN jars j ON j.id = t.jar_id
    LEFT JOIN accounts a ON a.id = t.account_id
"""


def get_transaction(conn, tx_id: int):
    row = conn.execute(f"{TX_SELECT} WHERE t.id = ?", (tx_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transaction not found.")
    return row


def new_entry_values(kind: str = "expense", currency: str = "UAH") -> dict:
    return {"kind": kind, "currency": currency, "date": date.today().isoformat()}


def render_split(
    request: Request,
    conn,
    pane: str,
    view: str,
    *,
    tx=None,
    values: dict | None = None,
    errors: dict | None = None,
    saved=None,
    deleted: bool = False,
    status_code: int = 200,
    quote: str | None = None,
):
    """The list on the left and a pane (add / detail / edit / delete) on the right.

    On a phone only one of the two is shown: `view` says which.
    """
    rows = conn.execute(f"{TX_SELECT} ORDER BY t.date DESC, t.id DESC").fetchall()
    days = [
        {"label": day_label(day), "items": list(items)}
        for day, items in groupby(rows, key=lambda row: row["date"])
    ]
    context = {
        "pane": pane,
        "view": view,
        "days": days,
        "tx": tx,
        "selected": tx["id"] if tx else None,
        "day": day_label(tx["date"]) if tx else "",
        "categories": load_categories(conn, tx["category_id"] if tx else None),
        "jars": jars.list_jars(conn, keep_id=tx["jar_id"] if tx else None),
        "quote": quote,
        "values": values if values is not None else {},
        "errors": errors or {},
        "saved": saved,
        "deleted": deleted,
        "use_device_date": pane == "add" and not errors,
        "max_comment": MAX_COMMENT,
    }
    return templates.TemplateResponse(request, "transactions.html", context, status_code=status_code)


@app.get("/")
def home(request: Request, month: str = ""):
    current = date.today().replace(day=1)
    first = parse_month(month) or current
    following = shift_month(first, 1)
    with db.connection() as conn:
        sums = conn.execute(
            """
            SELECT kind, currency, SUM(amount) AS total FROM transactions
            WHERE date >= ? AND date < ? AND kind IN ('income', 'expense')
            GROUP BY kind, currency
            """,
            (first.isoformat(), following.isoformat()),
        ).fetchall()
        summary = monthly.month_summary(conn, first, following)
        waiting = len(rules.unsorted(conn))
        people = transfers.people_flows(conn, first, following)
        start = wealth.get_start(conn)
        standing = wealth.standing(conn, start, first, date.today())
        money_now = standing["all_money"]
        all_jars = jars.list_jars(conn)
        recent = {
            code: conn.execute(
                f"{TX_SELECT} WHERE t.currency = ? ORDER BY t.date DESC, t.id DESC LIMIT 6", (code,)
            ).fetchall()
            for code in monthly.CURRENCIES
        }
    totals = {(row["kind"], row["currency"]): row["total"] for row in sums}
    # One full set of numbers per currency; the switch on the page shows one of them.
    # Hryvnias and euros are never added together or converted.
    views = []
    for code in monthly.CURRENCIES:
        sent, received = people[code]["sent"], people[code]["received"]
        # Money sent to people is an expense and money received from them is income (see app/wealth.py).
        income = totals.get(("income", code), 0) + received
        expense = totals.get(("expense", code), 0) + sent
        views.append(
            {
                "code": code,
                "symbol": CURRENCY_SYMBOLS[code],
                "income": income,
                "expense": expense,
                "balance": income - expense,
                "in_jars": standing["in_jars"][code],  # what lies in the jars (not the month's movement)
                "all_money": money_now[code] if money_now else None,
                "sent": sent,
                "received": received,
                "free": standing["free"][code] if standing["free"] else None,
                "jars": [j for j in all_jars if j["currency"] == code],
                "recent": recent[code],
                "budget": monthly.currency_view(summary, code),
            }
        )
    return templates.TemplateResponse(
        request,
        "home.html",
        {
            "month_label": f"{first:%B %Y}",
            "prev_month": shift_month(first, -1).strftime("%Y-%m"),
            "next_month": following.strftime("%Y-%m") if first < current else None,
            "views": views,
            "waiting": waiting,
            "start_set": bool(start),
            "together": wealth.together(money_now, start["rate"]) if start and money_now else None,
            "standing_day": standing["cutoff"],
            "rate_text": amount_for_input(start["rate"]) if start and start["rate"] else "",
            "month": first.strftime("%Y-%m"),
        },
    )


@app.get("/add")
def add_page(request: Request, saved: int | None = None, kind: str = "", jar: str = "", dir: str = ""):
    with db.connection() as conn:
        saved_tx = None
        if saved is not None:
            saved_tx = conn.execute(f"{TX_SELECT} WHERE t.id = ?", (saved,)).fetchone()
        shown_kind = "jar" if saved_tx and saved_tx["kind"] == "transfer" else saved_tx["kind"] if saved_tx else "expense"
        values = new_entry_values(shown_kind, saved_tx["currency"] if saved_tx else "UAH")
        if saved_tx and shown_kind == "jar":
            values.update(jar_id=str(saved_tx["jar_id"]), direction=saved_tx["direction"])
        elif kind == "jar":  # from the Jars page: the jar and the direction are already chosen
            values.update(kind="jar", jar_id=jar if jar.isdigit() else "", direction="out" if dir == "out" else "in")
        quote = None
        if saved_tx and saved_tx["kind"] == "transfer" and saved_tx["direction"] == "in":
            quote = jars.pick_quote(conn, saved_tx["jar_id"])
        response = render_split(request, conn, "add", "detail", values=values, saved=saved_tx, quote=quote)
    return response


@app.post("/add")
def add_submit(request: Request, form: dict = Depends(tx_form)):
    with db.connection() as conn:
        clean, errors = validate(conn, form)
        if errors:
            return render_split(
                request, conn, "add", "detail", values=form, errors=errors, status_code=422
            )
        cursor = conn.execute(
            """
            INSERT INTO transactions (date, kind, category_id, amount, currency, comment, source, jar_id, direction, paid_uah)
            VALUES (:date, :kind, :category_id, :amount, :currency, :comment, 'site', :jar_id, :direction, :paid_uah)
            """,
            clean,
        )
        low = jars.negative(conn, [clean["jar_id"]])
        if low:  # a withdrawal bigger than what is in the jar: undo it
            conn.rollback()
            return render_split(
                request, conn, "add", "detail", values=form,
                errors={"amount": f"There is not that much in {low}."}, status_code=422,
            )
    return RedirectResponse(f"/add?saved={cursor.lastrowid}", status_code=303)


@app.get("/transactions")
def transactions_page(request: Request, tx: int | None = None, deleted: bool = False):
    with db.connection() as conn:
        if tx is None:
            # Phone: just the list. Computer: the list with the add form beside it.
            return render_split(
                request, conn, "add", "list", values=new_entry_values(), deleted=deleted
            )
        return render_split(request, conn, "detail", "detail", tx=get_transaction(conn, tx))


@app.get("/transactions/{tx_id}/edit")
def edit_page(request: Request, tx_id: int):
    with db.connection() as conn:
        tx = get_transaction(conn, tx_id)
        if tx["kind"] == "transfer" and tx["jar_id"] is None:  # bank transfers: delete and sort again instead
            return RedirectResponse(f"/transactions?tx={tx_id}", status_code=303)
        values = dict(tx)
        values["amount"] = amount_for_input(tx["amount"])
        values["category_id"] = str(tx["category_id"] or "")
        if tx["kind"] == "transfer":
            values["kind"] = "jar"
            values["jar_id"] = str(tx["jar_id"] or "")
            values["paid"] = amount_for_input(tx["paid_uah"]) if tx["paid_uah"] else ""
        return render_split(request, conn, "edit", "detail", tx=tx, values=values)


@app.post("/transactions/{tx_id}/edit")
def edit_submit(request: Request, tx_id: int, form: dict = Depends(tx_form)):
    with db.connection() as conn:
        tx = get_transaction(conn, tx_id)
        clean, errors = validate(conn, form, keep_id=tx["category_id"])
        if errors:
            return render_split(
                request, conn, "edit", "detail", tx=tx, values=form, errors=errors, status_code=422
            )
        conn.execute(
            """
            UPDATE transactions
            SET date = :date, kind = :kind, category_id = :category_id, amount = :amount, currency = :currency,
                comment = :comment, jar_id = :jar_id, direction = :direction, paid_uah = :paid_uah
            WHERE id = :id
            """,
            {**clean, "id": tx_id},
        )
        low = jars.negative(conn, [clean["jar_id"], tx["jar_id"]])
        if low:
            conn.rollback()
            return render_split(
                request, conn, "edit", "detail", tx=tx, values=form,
                errors={"amount": f"There is not that much in {low}."}, status_code=422,
            )
    return RedirectResponse(f"/transactions?tx={tx_id}", status_code=303)


@app.get("/transactions/{tx_id}/delete")
def delete_page(request: Request, tx_id: int, blocked: str = ""):
    with db.connection() as conn:
        errors = {"delete": f"Can't delete: {blocked} would drop below zero."} if blocked else None
        return render_split(request, conn, "delete", "detail", tx=get_transaction(conn, tx_id), errors=errors)


@app.post("/transactions/{tx_id}/delete")
def delete_submit(tx_id: int):
    with db.connection() as conn:
        tx = get_transaction(conn, tx_id)
        conn.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        low = jars.negative(conn, [tx["jar_id"]])
        if low:  # removing a deposit would leave a later withdrawal without money behind it
            conn.rollback()
            return RedirectResponse(f"/transactions/{tx_id}/delete?blocked={quote_url(low)}", status_code=303)
    return RedirectResponse("/transactions?deleted=true", status_code=303)


@app.get("/budgets", include_in_schema=False)
def old_budgets_address():
    """The separate budgets screen is gone: the limits are the year plan now."""
    return RedirectResponse("/year", status_code=303)


ZERO = re.compile(r"0+([.,]0{1,2})?")  # typed zero = no amount


# ---------- Year: the plan against reality ----------
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_year(raw: str) -> int | None:
    return int(raw) if re.fullmatch(r"20[0-9]{2}", raw) else None


def year_or_400(raw: str) -> int:
    year = parse_year(raw)
    if year is None:
        raise HTTPException(status_code=400, detail="Bad year.")
    return year


def cell_key(category_id: int, month: int, currency: str) -> str:
    return f"p_{category_id}_{month:02d}_{currency}"


def render_year(
    request: Request,
    conn,
    year: int,
    *,
    values: dict | None = None,
    errors: dict | None = None,
    bad: list | None = None,
    notice: str = "",
    confirm: dict | None = None,
    status_code: int = 200,
):
    categories = plans.year_categories(conn, year)
    if values is None:
        values = {
            cell_key(cat, month, code): amount_for_input(amount)
            for (cat, month, code), amount in plans.month_plans(conn, year).items()
        }
    today = date.today()
    context = {
        "year": year,
        "prev_year": year - 1 if year > 2000 else None,
        "next_year": year + 1 if year < 2099 else None,
        "month_names": MONTH_NAMES,
        "views": [
            {"code": code, "symbol": CURRENCY_SYMBOLS[code], "grid": plans.grid(conn, year, code, today, categories)}
            for code in plans.CURRENCIES
        ],
        "cell_key": cell_key,
        "values": values,
        "errors": errors or {},
        "bad": bad or [],
        "notice": notice,
        "confirm": confirm,
        "saved": request.query_params.get("saved") == "1",
        "copied": request.query_params.get("copied", ""),
    }
    return templates.TemplateResponse(request, "year.html", context, status_code=status_code)


@app.get("/year")
def year_page(request: Request, year: str = ""):
    with db.connection() as conn:
        return render_year(request, conn, parse_year(year) or date.today().year)


@app.post("/year")
async def year_save(request: Request):
    form = await request.form()
    year = year_or_400(str(form.get("year", "")))
    with db.connection() as conn:
        values, errors, bad, clean = {}, {}, [], []
        for cat in plans.year_categories(conn, year):
            for month in range(1, 13):
                for code in plans.CURRENCIES:
                    key = cell_key(cat["id"], month, code)
                    raw = str(form.get(key, "")).strip()
                    values[key] = raw
                    compact = re.sub(r"\s", "", raw)
                    if not compact or ZERO.fullmatch(compact):
                        continue  # empty (or zero) means: no plan
                    try:
                        clean.append((f"{year}-{month:02d}", cat["id"], code, parse_amount(raw)))
                    except ValueError:
                        errors[key] = "Not an amount."
                        bad.append(f"{cat['name']} · {MONTH_NAMES[month - 1]} · {CURRENCY_SYMBOLS[code]}")
        if errors:
            return render_year(request, conn, year, values=values, errors=errors, bad=bad, status_code=422)
        conn.execute("DELETE FROM plans WHERE month >= ? AND month < ?", (f"{year}-01", f"{year + 1}-01"))
        conn.executemany(
            "INSERT INTO plans (month, category_id, currency, amount) VALUES (?, ?, ?, ?)", clean
        )
    return RedirectResponse(f"/year?year={year}&saved=1", status_code=303)


@app.post("/year/copy")
async def year_copy(request: Request):
    """Copy last year's plan into this year. Replaces this year's plan, but only after a confirmation."""
    form = await request.form()
    year = year_or_400(str(form.get("year", "")))
    with db.connection() as conn:
        source = conn.execute(
            "SELECT month, category_id, currency, amount FROM plans WHERE month >= ? AND month < ?",
            (f"{year - 1}-01", f"{year}-01"),
        ).fetchall()
        if not source:
            return render_year(request, conn, year, notice=f"There is no plan for {year - 1} to copy.")
        if plans.month_plans(conn, year) and not form.get("confirm"):
            return render_year(
                request,
                conn,
                year,
                confirm={"action": "/year/copy", "text": f"{year} already has a plan. Replace it with {year - 1}'s?"},
            )
        conn.execute("DELETE FROM plans WHERE month >= ? AND month < ?", (f"{year}-01", f"{year + 1}-01"))
        conn.executemany(
            "INSERT INTO plans (month, category_id, currency, amount) VALUES (?, ?, ?, ?)",
            [(f"{year}{r['month'][4:]}", r["category_id"], r["currency"], r["amount"]) for r in source],
        )
    return RedirectResponse(f"/year?year={year}&copied={len(source)}", status_code=303)


# ---------- Analytics ----------
@app.get("/analytics")
def analytics_page(request: Request, period: str = "month", month: str = "", year: str = ""):
    today = date.today()
    period = "year" if period == "year" else "month"
    if period == "month":
        selected = min(parse_month(month) or today.replace(day=1), today.replace(day=1))
        label = f"{selected:%B %Y}"
        prev_href = f"/analytics?month={shift_month(selected, -1):%Y-%m}"
        next_href = f"/analytics?month={shift_month(selected, 1):%Y-%m}" if selected < today.replace(day=1) else None
    else:
        picked = min(parse_year(year) or today.year, today.year)
        selected = date(picked, 1, 1)
        label = str(picked)
        prev_href = f"/analytics?period=year&year={picked - 1}" if picked > 2000 else None
        next_href = f"/analytics?period=year&year={picked + 1}" if picked < today.year else None
    if period == "month":
        month_href = f"/analytics?month={selected:%Y-%m}"
    else:  # the Month switch from a year: this month if it is the current year, else its December
        in_year = today.replace(day=1) if selected.year == today.year else date(selected.year, 12, 1)
        month_href = f"/analytics?month={in_year:%Y-%m}"
    with db.connection() as conn:
        views = [
            {"code": code, "symbol": CURRENCY_SYMBOLS[code], "data": analytics.build(conn, period, selected, today, code)}
            for code in analytics.CURRENCIES
        ]
    return templates.TemplateResponse(
        request,
        "analytics.html",
        {
            "period": period,
            "label": label,
            "prev_href": prev_href,
            "next_href": next_href,
            "month_href": month_href,
            "year_href": f"/analytics?period=year&year={selected.year}",
            "views": views,
            "month_names": MONTH_NAMES,
            "selected_year": selected.year,
        },
    )


# ---------- Jars ----------
RATE = re.compile(r"[0-9]+([.,][0-9]{1,2})?")


def jar_values(jar=None) -> dict:
    if jar is None:
        return {"currency": "UAH", "name": "", "target": "", "rate": "", "opening": "", "quote": "", "shared": False}
    return {
        "currency": jar["currency"],
        "name": jar["name"],
        "target": amount_for_input(jar["target"]) if jar["target"] else "",
        "rate": f"{jar['my_rate'] / 100:.2f}".rstrip("0").rstrip(".") if jar["my_rate"] else "",
        "opening": amount_for_input(jar["opening"]) if jar["opening"] else "",
        "quote": jar["quote"],
        "shared": bool(jar["shared"]),
    }


def check_jar(conn, form: dict, jar=None) -> tuple[dict, dict]:
    """Clean values and error messages for the jar form."""
    errors, clean = {}, {}
    name = form["name"].strip()
    if not name or len(name) > jars.MAX_NAME:
        errors["name"] = f"Give it a name of up to {jars.MAX_NAME} characters."
    elif conn.execute(
        "SELECT 1 FROM jars WHERE lower(name) = lower(?) AND id IS NOT ?", (name, jar["id"] if jar else None)
    ).fetchone():
        errors["name"] = "There is already a jar with this name."
    clean["name"] = name
    clean["currency"] = jar["currency"] if jar else form["currency"]
    if clean["currency"] not in CURRENCY_SYMBOLS:
        errors["currency"] = "Choose ₴ or €."
    clean["target"] = clean["my_rate"] = None
    clean["opening"] = 0
    if form["target"].strip():
        try:
            clean["target"] = parse_amount(form["target"])
        except ValueError:
            errors["target"] = "Enter the goal like 50000, or leave it empty."
    if form["opening"].strip() and not ZERO.fullmatch(re.sub(r"\s", "", form["opening"])):
        try:
            clean["opening"] = parse_amount(form["opening"])
        except ValueError:
            errors["opening"] = "Enter what is already in it like 1200, or leave it empty."
    if clean["currency"] == "EUR" and form["rate"].strip():
        text = re.sub(r"\s", "", form["rate"])
        if RATE.fullmatch(text) and 0 < float(text.replace(",", ".")) < 1000:
            clean["my_rate"] = round(float(text.replace(",", ".")) * 100)
        else:
            errors["rate"] = "Enter your rate in ₴ per €, like 43.50."
    clean["quote"] = form["quote"].strip()
    clean["shared"] = 1 if form.get("shared") else 0
    if len(clean["quote"]) > jars.MAX_QUOTE:
        errors["quote"] = f"Keep the quote under {jars.MAX_QUOTE} characters."
    return clean, errors


def jar_form(
    name: str = Form(""), currency: str = Form("UAH"), target: str = Form(""),
    rate: str = Form(""), opening: str = Form(""), quote: str = Form(""), shared: str = Form(""),
) -> dict:
    return {"name": name, "currency": currency, "target": target, "rate": rate, "opening": opening, "quote": quote,
            "shared": bool(shared)}


@app.get("/jars")
def jars_page(request: Request, saved: str = ""):
    with db.connection() as conn:
        active, archived = jars.list_jars(conn), jars.list_jars(conn, archived=True)
    if request.state.role == "viewer":  # a viewer sees only shared jars, nothing archived
        active, archived = [j for j in active if j["shared"]], []
    return templates.TemplateResponse(
        request, "jars.html",
        {"open_jars": active, "archived": archived, "saved": saved, "currencies": list(CURRENCY_SYMBOLS.items())},
    )


def render_jar_form(request: Request, jar, values: dict, errors: dict | None = None, status_code: int = 200):
    return templates.TemplateResponse(
        request, "jar_form.html",
        {"jar": jar, "values": values, "errors": errors or {}, "max_name": jars.MAX_NAME, "max_quote": jars.MAX_QUOTE},
        status_code=status_code,
    )


@app.get("/jars/new")
def jar_new_page(request: Request):
    return render_jar_form(request, None, jar_values())


@app.post("/jars/new")
def jar_new_submit(request: Request, form: dict = Depends(jar_form)):
    with db.connection() as conn:
        clean, errors = check_jar(conn, form)
        if errors:
            return render_jar_form(request, None, form, errors, 422)
        conn.execute(
            "INSERT INTO jars (name, currency, target, my_rate, quote, opening, shared) "
            "VALUES (:name, :currency, :target, :my_rate, :quote, :opening, :shared)",
            clean,
        )
    return RedirectResponse(f"/jars?saved={quote_url(clean['name'])}", status_code=303)


def jar_or_404(conn, jar_id: int):
    jar = jars.get_jar(conn, jar_id)
    if jar is None:
        raise HTTPException(status_code=404, detail="Jar not found.")
    return jar


@app.get("/jars/{jar_id}/edit")
def jar_edit_page(request: Request, jar_id: int):
    with db.connection() as conn:
        jar = jar_or_404(conn, jar_id)
    return render_jar_form(request, jar, jar_values(jar))


@app.post("/jars/{jar_id}/edit")
def jar_edit_submit(request: Request, jar_id: int, form: dict = Depends(jar_form)):
    with db.connection() as conn:
        jar = jar_or_404(conn, jar_id)
        clean, errors = check_jar(conn, form, jar)
        if errors:
            return render_jar_form(request, jar, {**form, "currency": jar["currency"]}, errors, 422)
        conn.execute(
            "UPDATE jars SET name = :name, target = :target, my_rate = :my_rate, quote = :quote, opening = :opening, shared = :shared WHERE id = :id",
            {**clean, "id": jar_id},
        )
        low = jars.negative(conn, [jar_id])
        if low:  # a smaller opening balance would leave the jar below zero
            conn.rollback()
            return render_jar_form(request, jar, form, {"opening": f"That would leave {low} below zero."}, 422)
    return RedirectResponse(f"/jars?saved={quote_url(clean['name'])}", status_code=303)


@app.post("/jars/{jar_id}/archive")
def jar_archive(jar_id: int):
    with db.connection() as conn:
        jar_or_404(conn, jar_id)
        conn.execute("UPDATE jars SET archived = 1 WHERE id = ?", (jar_id,))
    return RedirectResponse("/jars", status_code=303)


@app.post("/jars/{jar_id}/restore")
def jar_restore(jar_id: int):
    with db.connection() as conn:
        jar_or_404(conn, jar_id)
        conn.execute("UPDATE jars SET archived = 0 WHERE id = ?", (jar_id,))
    return RedirectResponse("/jars", status_code=303)


@app.post("/jars/{jar_id}/delete")
def jar_delete(jar_id: int):
    """Only a jar with no transfers can be deleted; otherwise archive it."""
    with db.connection() as conn:
        jar = jar_or_404(conn, jar_id)
        if jar["moves"]:
            raise HTTPException(status_code=400, detail="This jar has transfers. Archive it instead.")
        conn.execute("DELETE FROM jars WHERE id = ?", (jar_id,))
    return RedirectResponse("/jars", status_code=303)


# ---------- Settings: categories and quotes ----------
MAX_CATEGORY = 40
MAX_QUOTE_TEXT = 200
GROUPS = list(monthly.GROUP_LABELS.items())


def money_values(conn, form: dict | None = None) -> dict:
    """What the Money tab shows: the saved start, or what was just typed (after a mistake)."""
    if form is not None:
        return form
    start = wealth.get_start(conn)
    first = date.today().replace(day=1).isoformat()
    if not start:
        return {"date": first, "uah": "", "eur": "", "rate": ""}
    return {
        "date": start["date"],
        "uah": amount_for_input(start["UAH"]) if start["UAH"] else "",
        "eur": amount_for_input(start["EUR"]) if start["EUR"] else "",
        "rate": amount_for_input(start["rate"]) if start["rate"] else "",
    }


def render_settings(request: Request, conn, error: str = "", saved: str = "", status_code: int = 200, tab: str = "expense", money_form: dict | None = None):
    rows = conn.execute(
        """
        SELECT c.id, c.kind, c.name, c."group" AS grp, c.archived,
               (SELECT COUNT(*) FROM transactions t WHERE t.category_id = c.id) AS uses
        FROM categories c
        ORDER BY CASE c."group" WHEN 'essentials' THEN 0 WHEN 'fixed' THEN 1 WHEN 'fun' THEN 2 ELSE 3 END, c.id
        """
    ).fetchall()
    return templates.TemplateResponse(
        request, "settings.html",
        {
            "expense": [r for r in rows if r["kind"] == "expense" and not r["archived"]],
            "income": [r for r in rows if r["kind"] == "income" and not r["archived"]],
            "archived": [r for r in rows if r["archived"]],
            "quotes": conn.execute("SELECT id, text FROM quotes ORDER BY id").fetchall(),
            "rules": rules.list_rules(conn),
            "money": money_values(conn, money_form),
            "accounts": transfers.list_accounts(conn, archived=None),
            "max_pattern": rules.MAX_PATTERN,
            "groups": GROUPS,
            "error": error,
            "saved": saved,
            "tab": tab,
            "max_category": MAX_CATEGORY,
            "max_quote": MAX_QUOTE_TEXT,
        },
        status_code=status_code,
    )


@app.get("/settings")
def settings_page(request: Request, saved: str = ""):
    with db.connection() as conn:
        return render_settings(request, conn, saved=saved)


def clean_category(conn, kind: str, name: str, group: str, own_id: int | None = None) -> tuple[str, str | None, str]:
    """(name, group, error)."""
    name = " ".join(name.split())
    if not name or len(name) > MAX_CATEGORY:
        return name, None, f"Give the category a name of up to {MAX_CATEGORY} characters."
    if conn.execute(
        "SELECT 1 FROM categories WHERE kind = ? AND lower(name) = lower(?) AND id IS NOT ?", (kind, name, own_id)
    ).fetchone():
        return name, None, f"There is already a {kind} category called {name}."
    if kind == "expense":
        if group not in dict(GROUPS):
            return name, None, "Choose a group: Essentials, Fixed or Fun."
        return name, group, ""
    return name, None, ""


@app.post("/settings/categories/add")
def category_add(request: Request, kind: str = Form(""), name: str = Form(""), group: str = Form("")):
    with db.connection() as conn:
        if kind not in ("income", "expense"):
            raise HTTPException(status_code=400, detail="Unknown kind.")
        name, grp, error = clean_category(conn, kind, name, group)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="income" if kind == "income" else "expense")
        conn.execute('INSERT INTO categories (kind, name, "group") VALUES (?, ?, ?)', (kind, name, grp))
    return RedirectResponse(f"/settings?saved={quote_url(name)}#{kind}", status_code=303)


def category_or_404(conn, category_id: int):
    row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    return row


@app.post("/settings/categories/{category_id}")
def category_save(request: Request, category_id: int, name: str = Form(""), group: str = Form("")):
    with db.connection() as conn:
        row = category_or_404(conn, category_id)
        name, grp, error = clean_category(conn, row["kind"], name, group, category_id)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab=row["kind"])
        conn.execute('UPDATE categories SET name = ?, "group" = ? WHERE id = ?', (name, grp, category_id))
    return RedirectResponse(f"/settings?saved={quote_url(name)}#{row['kind']}", status_code=303)


@app.post("/settings/categories/{category_id}/archive")
def category_archive(category_id: int):
    with db.connection() as conn:
        row = category_or_404(conn, category_id)
        conn.execute("UPDATE categories SET archived = 1 WHERE id = ?", (category_id,))
    return RedirectResponse(f"/settings#{row['kind']}", status_code=303)


@app.post("/settings/categories/{category_id}/restore")
def category_restore(category_id: int):
    with db.connection() as conn:
        row = category_or_404(conn, category_id)
        conn.execute("UPDATE categories SET archived = 0 WHERE id = ?", (category_id,))
    return RedirectResponse(f"/settings#{row['kind']}", status_code=303)


def clean_quote(conn, text: str, own_id: int | None = None) -> tuple[str, str]:
    text = " ".join(text.split())
    if not text or len(text) > MAX_QUOTE_TEXT:
        return text, f"Write a quote of up to {MAX_QUOTE_TEXT} characters."
    if conn.execute("SELECT 1 FROM quotes WHERE text = ? AND id IS NOT ?", (text, own_id)).fetchone():
        return text, "That quote is already in the list."
    return text, ""


@app.post("/settings/quotes/add")
def quote_add(request: Request, text: str = Form("")):
    with db.connection() as conn:
        text, error = clean_quote(conn, text)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="quotes")
        conn.execute("INSERT INTO quotes (text) VALUES (?)", (text,))
    return RedirectResponse("/settings?saved=quote#quotes", status_code=303)


@app.post("/settings/quotes/{quote_id}")
def quote_save(request: Request, quote_id: int, text: str = Form("")):
    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM quotes WHERE id = ?", (quote_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Quote not found.")
        text, error = clean_quote(conn, text, quote_id)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="quotes")
        conn.execute("UPDATE quotes SET text = ? WHERE id = ?", (text, quote_id))
    return RedirectResponse("/settings?saved=quote#quotes", status_code=303)


@app.post("/settings/quotes/{quote_id}/delete")
def quote_delete(quote_id: int):
    with db.connection() as conn:
        conn.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    return RedirectResponse("/settings#quotes", status_code=303)


# ---------- Bank lines that wait for a category ----------
def unsorted_rows(conn):
    cats = load_categories(conn)
    return [{**line, "categories": cats[line["kind"]]} for line in rules.unsorted(conn)]


def render_unsorted(request: Request, conn, saved: str = "", error: str = "", status_code: int = 200):
    return templates.TemplateResponse(
        request, "unsorted.html",
        {"rows": unsorted_rows(conn), "accounts": transfers.list_accounts(conn), "saved": saved, "error": error},
        status_code=status_code,
    )


@app.get("/unsorted")
def unsorted_page(request: Request, saved: str = ""):
    with db.connection() as conn:
        return render_unsorted(request, conn, saved=saved)


@app.post("/unsorted/{line_id}")
def unsorted_save(
    request: Request, line_id: int, action: str = Form(""), category_id: str = Form(""),
    account_id: str = Form(""), remember: str = Form(""),
):
    with db.connection() as conn:
        line = conn.execute("SELECT kind, comment FROM inbox WHERE id = ?", (line_id,)).fetchone()
        if line is None:  # already sorted (for example in the bot)
            return RedirectResponse("/unsorted", status_code=303)
        new_id, error = None, ""
        if action == "people":
            new_id = rules.make_transfer(conn, line_id, "people")
        elif action == "accounts":
            if line["kind"] == "expense" and transfers.list_accounts(conn) and not account_id.isdigit():
                error = "Choose the account the money went to."
            else:
                new_id = rules.make_transfer(conn, line_id, "accounts", int(account_id) if account_id.isdigit() else None)
                error = "" if new_id else "That account can't be used."
        else:
            got = rules.sort_entry(conn, line_id, int(category_id)) if category_id.isdigit() else None
            new_id = got[1] if got else None
            error = "" if new_id else "Choose a category for that entry."
        if error or new_id is None:
            return render_unsorted(request, conn, error=error or "Couldn't sort that entry.", status_code=422)
        if remember:
            rules.remember(conn, new_id)
    return RedirectResponse("/unsorted?saved=1", status_code=303)


# ---------- Settings: bank rules and my accounts ----------
def rule_target(conn, raw: str) -> tuple[dict | None, str]:
    """What the select's value means: {category_id | transfer, account_id}, or an error."""
    if raw == "people":
        return {"category_id": None, "transfer": "people", "account_id": None}, ""
    if raw == "accounts":
        return {"category_id": None, "transfer": "accounts", "account_id": None}, ""
    if raw.startswith("account:") and raw[8:].isdigit() and conn.execute(
        "SELECT 1 FROM accounts WHERE id = ? AND archived = 0", (int(raw[8:]),)
    ).fetchone():
        return {"category_id": None, "transfer": "accounts", "account_id": int(raw[8:])}, ""
    if raw.isdigit() and conn.execute("SELECT 1 FROM categories WHERE id = ? AND archived = 0", (int(raw),)).fetchone():
        return {"category_id": int(raw), "transfer": None, "account_id": None}, ""
    return None, "Choose what the rule does."


@app.post("/settings/rules/add")
def rule_add(request: Request, pattern: str = Form(""), target: str = Form("")):
    with db.connection() as conn:
        pattern = rules.clean_pattern(pattern)
        chosen, error = rule_target(conn, target)
        if not pattern:
            error = "Write the text to look for in the bank description."
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="rules")
        rules.save_rule(conn, pattern, **chosen)
        rules.apply_to_waiting(conn)
    return RedirectResponse("/settings?saved=rule#rules", status_code=303)


@app.post("/settings/rules/{rule_id}")
def rule_save(request: Request, rule_id: int, pattern: str = Form(""), target: str = Form("")):
    with db.connection() as conn:
        if conn.execute("SELECT 1 FROM rules WHERE id = ?", (rule_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Rule not found.")
        pattern = rules.clean_pattern(pattern)
        chosen, error = rule_target(conn, target)
        if not pattern:
            error = "Write the text to look for in the bank description."
        elif conn.execute("SELECT 1 FROM rules WHERE key = ? AND id <> ?", (pattern.casefold(), rule_id)).fetchone():
            error = "There is already a rule with that text."
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="rules")
        rules.save_rule(conn, pattern, rule_id=rule_id, **chosen)
        rules.apply_to_waiting(conn)
    return RedirectResponse("/settings?saved=rule#rules", status_code=303)


@app.post("/settings/rules/{rule_id}/delete")
def rule_delete(rule_id: int):
    with db.connection() as conn:
        conn.execute("DELETE FROM rules WHERE id = ?", (rule_id,))
    return RedirectResponse("/settings#rules", status_code=303)


@app.post("/settings/accounts/add")
def account_add(request: Request, name: str = Form("")):
    with db.connection() as conn:
        name, error = transfers.clean_name(conn, name)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="accounts")
        conn.execute("INSERT INTO accounts (name) VALUES (?)", (name,))
    return RedirectResponse("/settings?saved=account#accounts", status_code=303)


def account_or_404(conn, account_id: int):
    if conn.execute("SELECT 1 FROM accounts WHERE id = ?", (account_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Account not found.")


@app.post("/settings/accounts/{account_id}")
def account_save(request: Request, account_id: int, name: str = Form("")):
    with db.connection() as conn:
        account_or_404(conn, account_id)
        name, error = transfers.clean_name(conn, name, account_id)
        if error:
            return render_settings(request, conn, error=error, status_code=422, tab="accounts")
        conn.execute("UPDATE accounts SET name = ? WHERE id = ?", (name, account_id))
    return RedirectResponse("/settings?saved=account#accounts", status_code=303)


@app.post("/settings/accounts/{account_id}/archive")
def account_archive(account_id: int):
    with db.connection() as conn:
        account_or_404(conn, account_id)
        conn.execute("UPDATE accounts SET archived = 1 WHERE id = ?", (account_id,))
    return RedirectResponse("/settings#accounts", status_code=303)


@app.post("/settings/accounts/{account_id}/restore")
def account_restore(account_id: int):
    with db.connection() as conn:
        account_or_404(conn, account_id)
        conn.execute("UPDATE accounts SET archived = 0 WHERE id = ?", (account_id,))
    return RedirectResponse("/settings#accounts", status_code=303)


@app.post("/settings/money")
def money_save(request: Request, start_date: str = Form(""), uah: str = Form(""), eur: str = Form(""), rate: str = Form("")):
    form = {"date": start_date.strip(), "uah": uah.strip(), "eur": eur.strip(), "rate": rate.strip()}
    with db.connection() as conn:
        try:
            day = date.fromisoformat(form["date"]).isoformat()
            start_uah, start_eur, rate_value = wealth.parse_money(uah), wealth.parse_money(eur), wealth.parse_rate(rate)
        except ValueError:
            return render_settings(
                request, conn, status_code=422, tab="money", money_form=form,
                error="Check the date and the amounts: numbers like 5000 or 450.50 (the rate like 50 or 48.5).",
            )
        wealth.save_start(conn, day, start_uah, start_eur, rate_value)
    return RedirectResponse("/settings?saved=money#money", status_code=303)
