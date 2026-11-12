"""The network side of the Monobank import, with a pretend bank and a pretend Telegram (no network)."""

import httpx
import pytest

from app import db, mono
from monosync import sync


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("FINANCE_DB", str(tmp_path / "t.db"))
    db.init_db()
    with db.connection() as c:
        yield c


def test_reads_pages_and_splits_long_periods(monkeypatch):
    calls = []

    def fake_get(client, token, path):
        calls.append(path)
        _, _, _, account, start, end = path.split("/")
        if len(calls) == 1:  # a full first page: the rest is older
            return [{"id": str(i), "time": 2_000_000 - i} for i in range(sync.MAX_LINES)]
        return []

    monkeypatch.setattr(sync, "get", fake_get)
    start, end = 1_000_000, 1_000_000 + 40 * 24 * 3600  # 40 days: two pieces
    lines = sync.read_account(None, "tok", "ACC", start, end, lambda: None)
    assert len(lines) == sync.MAX_LINES
    pieces = {(int(p.split("/")[-2]), int(p.split("/")[-1])) for p in calls}
    assert all(b - a <= 31 * 24 * 3600 for a, b in pieces)
    assert len(calls) >= 3  # page 1, the older part of piece 1, piece 2


def test_rate_limit_stops_quietly():
    transport = httpx.MockTransport(lambda request: httpx.Response(429))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(sync.Stop):
            sync.get(client, "tok", "/personal/client-info")


def test_notify_sends_buttons_then_marks_done(conn, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    mono.import_lines(conn, "A", [{"id": "1", "time": 1_794_300_000, "description": "Silpo", "amount": -5000, "currencyCode": 980}])
    conn.commit()
    sent = []

    def handler(request):
        sent.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        sync.notify(client)
        sync.notify(client)  # nothing new: no second message
    assert len(sent) == 1 and sent[0]["chat_id"] == "42" and "Silpo" in sent[0]["text"]
    data = [b["callback_data"] for row in sent[0]["reply_markup"]["inline_keyboard"] for b in row]
    assert all(any(d.startswith(x) for d in data) for x in ("m:", "mp:", "ma:", "ms:"))
    assert all(len(d) <= 64 for d in data)


def test_notify_many_sends_one_summary(conn, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "42")
    lines = [{"id": str(i), "time": 1_794_300_000 + i, "description": f"Shop {i}", "amount": -100 * (i + 1), "currencyCode": 980}
             for i in range(8)]
    mono.import_lines(conn, "A", lines)
    conn.commit()
    sent = []
    transport = httpx.MockTransport(lambda r: (sent.append(r), httpx.Response(200, json={}))[1])
    with httpx.Client(transport=transport) as client:
        sync.notify(client)
    assert len(sent) == 1
    assert b"8 new bank entries" in sent[0].content


def test_jars_are_listed(capsys):
    info = {"accounts": [], "jars": [
        {"id": "J1", "title": "Trip", "currencyCode": 980, "balance": 4200050, "goal": 15000000},
        {"id": "J2", "title": "Rainy day", "currencyCode": 978, "balance": 90000},
    ]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=info))
    with httpx.Client(transport=transport) as client:
        sync.list_jars("tok", client)
    out = capsys.readouterr().out
    assert "J1   UAH   42 000.50   150 000.00   Trip" in out and "J2   EUR   900.00   -   Rainy day" in out


def test_no_jars_says_so(capsys):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"accounts": []}))
    with httpx.Client(transport=transport) as client:
        sync.list_jars("tok", client)
    assert "No jars" in capsys.readouterr().out
