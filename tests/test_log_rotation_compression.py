"""Rotated bot logs are compressed, and still pruned.

2026-08-06: the disk hit 100% — 4 MB free — and SQLite began returning
"disk I/O error" on every write. Strategies went down. The cause was not a leak:
backupCount=30 was pruning correctly, but the bot writes 100-200 MB of log a day
and 30 days uncompressed is ~4 GB, on a 29 GB disk that also holds a 12 GB
bot.db.

Compressing on rotation takes that window to roughly 400 MB. The retention
policy is unchanged — this is the same 30 days, a tenth of the size.

The subtle half is that pruning has to keep working on the compressed names. It
does: the handler matches rotated files with a bare date pattern that a trailing
.gz does not break. Get that wrong and the fix quietly becomes the bug it was
meant to solve — files accumulate forever, invisible to backupCount.
"""
import gzip
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitoring.logger import _gzip_rotator, setup_logger  # noqa: E402


# ── the rotator ───────────────────────────────────────────────────────

def test_it_compresses_and_removes_the_original(tmp_path):
    src = tmp_path / "bot.log.2026-08-05"
    src.write_text("hello\n" * 1000)
    dest = str(src) + ".gz"
    _gzip_rotator(str(src), dest)
    assert not src.exists()
    assert gzip.open(dest, "rt").read() == "hello\n" * 1000


def test_it_actually_saves_space(tmp_path):
    """Bot logs are repetitive; a tenth is the realistic ratio."""
    src = tmp_path / "bot.log.2026-08-05"
    src.write_text("2026-08-05 12:00:00 [INFO] bot: scanning market KXNBA\n" * 20_000)
    plain = src.stat().st_size
    dest = str(src) + ".gz"
    _gzip_rotator(str(src), dest)
    assert os.path.getsize(dest) < plain / 5


def test_a_failure_leaves_the_log_rather_than_losing_it(tmp_path, monkeypatch):
    """Losing compression costs disk; raising out of a logging handler at
    midnight costs the process."""
    src = tmp_path / "bot.log.2026-08-05"
    src.write_text("payload\n")

    def boom(*a, **k):
        raise OSError("no space left on device")

    monkeypatch.setattr("monitoring.logger.shutil.copyfileobj", boom)
    _gzip_rotator(str(src), str(src) + ".gz")          # must not raise
    assert (tmp_path / "bot.log.2026-08-05").exists()
    assert not (tmp_path / "bot.log.2026-08-05.gz").exists(), "no half-written .gz"


# ── wiring ────────────────────────────────────────────────────────────

@pytest.fixture
def fresh_root():
    """setup_logger only configures a root with no handlers, and pytest's logging
    plugin attaches one for the call phase — after fixture setup. So the clearing
    has to happen inside the test body, which is what the returned helper does."""
    root = logging.getLogger()
    saved, level = root.handlers[:], root.level

    def install(log_dir):
        root.handlers = []
        setup_logger("bot", log_dir=str(log_dir))
        from logging.handlers import TimedRotatingFileHandler
        return next(h for h in root.handlers
                    if isinstance(h, TimedRotatingFileHandler))

    yield install
    for h in root.handlers:
        h.close()
    root.handlers, root.level = saved, level


def test_setup_logger_installs_the_compressing_rotator(tmp_path, fresh_root):
    h = fresh_root(tmp_path)
    assert h.rotator is _gzip_rotator
    assert h.namer("logs/bot.log.2026-08-05") == "logs/bot.log.2026-08-05.gz"


def test_retention_is_unchanged(tmp_path, fresh_root):
    """This is a size fix, not a policy change — still 30 days."""
    assert fresh_root(tmp_path).backupCount == 30


# ── pruning must survive the .gz suffix ───────────────────────────────

def test_the_handler_still_recognises_compressed_rotations(tmp_path, fresh_root):
    """If .gz broke the match, every compressed file would be invisible to
    backupCount and pile up forever — the original bug, wearing a fix."""
    ext = fresh_root(tmp_path).extMatch
    assert ext.search("2026-08-05.gz")
    assert ext.search("2026-08-05")


def test_compressed_files_beyond_the_window_are_deleted(tmp_path, fresh_root):
    h = fresh_root(tmp_path)
    for day in range(1, 41):                      # 40 days, window is 30
        (tmp_path / f"bot.log.2026-06-{day:02d}.gz").write_bytes(b"x")
    doomed = h.getFilesToDelete()
    assert len(doomed) == 10
    assert all(p.endswith(".gz") for p in doomed)
    assert all("2026-06-3" in p or "2026-06-0" in p for p in doomed[:1])
