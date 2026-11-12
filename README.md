# finance

A personal finance tracker: a mobile-first website and a Telegram bot, built for a single user.

## Status

Stage 7 of 8: Monobank import (cards read every 10 minutes, bank rules, entries to sort on the site and in the bot), the Telegram bot and the site with the month against the year plan, analytics, jars and settings.

## Stack

- Python 3 and FastAPI
- SQLite (`data/finance.db`)
- HTML, CSS and a little JavaScript
- Later: python-telegram-bot (polling mode)

## Run it

First time only:

```
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
```

Start the site, then open <http://localhost:8000>:

```
.venv/bin/uvicorn app.main:app
```

Press `Ctrl+C` to stop it.

## Test it

```
.venv/bin/python -m pytest
```

## Secrets and data

Tokens live only in `.env`, and the database lives in `data/`. Git ignores both, so neither should ever be committed.

## More

- `CLAUDE.md`: the full project description, rules and stage plan
- `DESIGN.md`: the design system

## On the server

The site runs on the Hetzner server (user `aiia`, code in `~/finance`) as the systemd service `finance`.
It listens only on `127.0.0.1:8000`; `tailscale serve` shows it inside the Tailscale network, at the
`https://<server>.<tailnet>.ts.net` address (run `tailscale serve status` on the server to see it).
It is not on the public internet: open it from a device with Tailscale switched on (phone or computer,
same account).

The server database (`~/finance/data/finance.db`) is separate from the one on the laptop, which is only for testing.
The code comes from the public GitHub repo `aiiadu-ai/finances_tracker` (the server reads it with a read-only deploy key; secrets and data are never in it).

### How to update the site on the server after new changes

1. **On the laptop**, in `~/projects/finance`: check the tests pass (`.venv/bin/python -m pytest`),
   commit, and push: `git push`.
2. **On the server**: connect (`ssh finance-server` from the laptop; the server's address is in `~/.ssh/config`)
   and run:

   ```
   ~/finance/deploy/update.sh
   ```

   It pulls the new code, installs any new requirements and restarts the site (`sudo` asks for your password).
   At the end it prints `active` and `Site answers. Done.`
3. **Look at the site** on the phone and check the new thing works. Reload the page (pull down) if you still see the old version.

The data (`data/finance.db`) is never touched by an update.

If something goes wrong:
- See what the site says: `journalctl -u finance -n 50 --no-pager`
- Go back to the previous version: `cd ~/finance && git log --oneline -5`, then `git checkout <old-commit>` and
  `sudo systemctl restart finance` (afterwards `git checkout main` to return to the latest code).
- Restart without updating: `sudo systemctl restart finance`

### Backups

Every day at 03:30 (Kyiv time) cron runs `deploy/backup.py`: a whole copy of the database goes to
`~/backups/finance/finance-YYYY-MM-DD.db.gz`; copies older than 30 days are deleted.
The log is `~/backups/finance.log`.

Restore from a backup (stop the site first so nothing is written meanwhile):

```
sudo systemctl stop finance
cd ~/finance/data
cp finance.db finance.db.before-restore
gunzip -c ~/backups/finance/finance-YYYY-MM-DD.db.gz > finance.db
sudo systemctl start finance
```

### First-time setup (for the record)

Tailscale installed on the server and joined to the account (`sudo tailscale up`); the site exposed with
`sudo tailscale serve --bg 8000`; `deploy/finance.service` copied to `/etc/systemd/system/` and enabled with
`sudo systemctl enable --now finance`; the backup line added to the crontab of `aiia`.

### The Telegram bot

Code in `tgbot/` (logic in `app/botlogic.py`), run as the systemd service `finance-bot`
(`deploy/finance-bot.service`, 150 MB memory limit). It polls Telegram, so nothing is opened to the internet,
and it answers only the chat id in `.env`.

`.env` (on the server, in `~/finance/.env`; see `.env.example`):

```
TELEGRAM_BOT_TOKEN=...   # from @BotFather
TELEGRAM_CHAT_ID=...     # your own chat id
```

Run only one copy per token: a second copy (for example on the laptop) makes both fail.
After the first setup, `~/finance/deploy/update.sh` restarts the bot together with the site.

### Monobank import

`python -m monosync` (code in `monosync/`, logic in `app/mono.py`) reads the statement of the cards listed in
`MONOBANK_ACCOUNTS` and saves new lines (₴ and € only; each line once). It runs from cron every 10 minutes:

```
*/10 * * * * cd /home/aiia/finance && flock -n /tmp/monosync.lock .venv/bin/python -m monosync >> data/monosync.log 2>&1
```

`.env` needs `MONOBANK_TOKEN` and `MONOBANK_ACCOUNTS` (`python -m monosync --accounts` lists the ids).
A line is sorted by a bank rule (Settings → Bank rules); otherwise it waits in the inbox, and nothing counts it yet:
Home shows "N bank entries to sort", the page `/unsorted` and the bot's `/sort` let you choose a category
(and remember it as a rule), or mark it as sent to / received from people, or as a transfer between your
accounts (Settings → My accounts). The same amount leaving one card and arriving on another becomes a
transfer between accounts automatically.

Monobank jars become site jars by themselves (`python -m monosync --jars` lists them). A deposit is taken from the
jar statement and matched with the card line, so the money is counted once and "In jars" follows the bank.
