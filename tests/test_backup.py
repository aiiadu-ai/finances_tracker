import gzip
import importlib.util
import os
import sqlite3
import time
from datetime import date
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "backup", Path(__file__).resolve().parent.parent / "deploy" / "backup.py"
)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def make_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.commit()
    conn.close()


def test_backup_is_a_whole_readable_copy(tmp_path):
    db = tmp_path / "finance.db"
    make_db(db)
    target = backup.make_backup(db, tmp_path / "bk", today=date(2026, 11, 9))
    assert target.name == "finance-2026-11-09.db.gz"
    restored = tmp_path / "restored.db"
    restored.write_bytes(gzip.decompress(target.read_bytes()))
    assert sqlite3.connect(restored).execute("SELECT x FROM t").fetchone() == (42,)
    assert sorted(p.name for p in (tmp_path / "bk").iterdir()) == [target.name]  # no leftovers


def test_missing_database_is_an_error_not_an_empty_backup(tmp_path):
    try:
        backup.make_backup(tmp_path / "nope.db", tmp_path / "bk")
    except SystemExit as stop:
        assert "nothing to back up" in str(stop)
    else:
        raise AssertionError("expected SystemExit")
    assert not list((tmp_path / "bk").glob("*.gz")) if (tmp_path / "bk").exists() else True


def test_prune_removes_only_copies_older_than_30_days(tmp_path):
    now = time.time()
    for name, age_days in (("finance-old.db.gz", 31), ("finance-edge.db.gz", 29), ("finance-new.db.gz", 0)):
        f = tmp_path / name
        f.write_bytes(b"x")
        os.utime(f, (now - age_days * 86400, now - age_days * 86400))
    other = tmp_path / "notes.txt"  # not a backup: never touched
    other.write_text("keep")
    os.utime(other, (now - 90 * 86400, now - 90 * 86400))
    removed = backup.prune(tmp_path, now=now)
    assert [p.name for p in removed] == ["finance-old.db.gz"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["finance-edge.db.gz", "finance-new.db.gz", "notes.txt"]
