#!/usr/bin/env python3
"""Daily backup of the finance database.

Makes a consistent copy of data/finance.db (safe while the site is running), compresses it into
~/backups/finance/finance-YYYY-MM-DD.db.gz and deletes copies older than 30 days.
"""

import gzip
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import date
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get("FINANCE_DB", PROJECT / "data" / "finance.db"))
BACKUPS = Path(os.environ.get("FINANCE_BACKUPS", Path.home() / "backups" / "finance"))
KEEP_DAYS = 30


def make_backup(db: Path = DB, folder: Path = BACKUPS, today: date | None = None) -> Path:
    if not db.exists():
        raise SystemExit(f"No database at {db}: nothing to back up.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"finance-{(today or date.today()).isoformat()}.db.gz"
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        copy = Path(tmp) / "copy.db"
        source = sqlite3.connect(db)
        dest = sqlite3.connect(copy)
        with dest:
            source.backup(dest)  # SQLite's own online backup: a whole, consistent snapshot
        source.close()
        dest.close()
        partial = target.with_suffix(".gz.part")
        with open(copy, "rb") as src, gzip.open(partial, "wb") as out:
            shutil.copyfileobj(src, out)
        partial.replace(target)  # a half-written file never takes the real name
    target.chmod(0o600)
    return target


def prune(folder: Path = BACKUPS, keep_days: int = KEEP_DAYS, now: float | None = None) -> list[Path]:
    cutoff = (now or time.time()) - keep_days * 86400
    removed = []
    for old in folder.glob("finance-*.db.gz"):
        if old.stat().st_mtime < cutoff:
            old.unlink()
            removed.append(old)
    return removed


if __name__ == "__main__":
    path = make_backup()
    gone = prune()
    print(f"Backup: {path} ({path.stat().st_size} bytes); removed {len(gone)} old.", file=sys.stderr)
