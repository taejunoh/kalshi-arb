"""The host watchdog warns before the disk fills, not after.

2026-08-06: / reached 100% with 4 MB free. SQLite returned "disk I/O error" on
every write and macro_event / xvenue_polymarket stopped. The box had been filling
for weeks — 30 days of uncompressed logs beside a 12 GB bot.db on a 29 GB disk —
and the first anyone knew of it was two strategies erroring.

Memory already had a watchdog with hysteresis and rate limiting. Disk now runs
through the same state machine, on the same 60s timer, so the warning arrives
while there is still room to act.

Two things here are load-bearing beyond "does it alert":
  * it must NOT alert every minute — a permanent alarm is the same as none;
  * the reading must not shell out or walk the tree. A full disk is exactly when
    you cannot afford `du` over 17 GB, and fork() may already be failing under
    the memory pressure that often comes with it.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import mem_watchdog as w  # noqa: E402


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Watchdog wired to a temp state file, with Telegram and the memory
    reading stubbed out so only the disk path is under test."""
    sent = []
    monkeypatch.setattr(w, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(w, "send_telegram", lambda t: sent.append(t) or True)
    monkeypatch.setattr(w, "_meminfo", lambda: (2000, 4000, 0))     # memory fine
    monkeypatch.setattr(w, "_big_consumers", lambda: "  bot.db 12.0GB")
    monkeypatch.setattr(w, "_loadavg", lambda: ["0.1", "0.1", "0.1"])
    monkeypatch.setattr(w, "REALERT_SEC", 1800)

    def at(pct):
        monkeypatch.setattr(w, "_disk", lambda path=None: (pct, 29 * (100 - pct) / 100, 29.0))
        w.main()
        return sent

    return at, sent


def _state(tmp_path):
    return json.load(open(tmp_path / "state.json"))


# ── alerting ──────────────────────────────────────────────────────────

def test_it_warns_before_the_disk_is_full(rig):
    """85%, not 100% — the point is room to act."""
    at, sent = rig
    at(86)
    assert len(sent) == 1
    assert "디스크 부족" in sent[0] and "86%" in sent[0]


def test_a_healthy_disk_is_silent(rig):
    at, sent = rig
    at(60)
    assert sent == []


def test_the_alert_names_what_is_using_the_space(rig):
    at, sent = rig
    at(95)
    assert "bot.db 12.0GB" in sent[0]


def test_it_says_what_breaks(rig):
    """The failure mode is not obvious from a percentage: writes start failing
    and strategies stop."""
    at, sent = rig
    at(95)
    assert "disk I/O error" in sent[0]


# ── not every minute ──────────────────────────────────────────────────

def test_it_does_not_alert_on_every_run(rig):
    """The timer fires every 60s and a full disk does not fix itself. Repeating
    would be a permanent alarm, which is the same as no alarm."""
    at, sent = rig
    for _ in range(10):
        at(92)
    assert len(sent) == 1


def test_it_re_alerts_after_the_interval(rig, monkeypatch):
    """…but it must not be forgotten either."""
    at, sent = rig
    at(92)
    later = w.time.time() + 3600            # capture before patching, or recurse
    monkeypatch.setattr(w.time, "time", lambda: later)
    at(92)
    assert len(sent) == 2
    assert "여전히" in sent[1]


def test_recovery_is_reported_once(rig):
    at, sent = rig
    at(92)
    at(70)
    at(70)
    assert len(sent) == 2 and "디스크 회복" in sent[1]


def test_the_hysteresis_band_is_quiet(rig):
    """Between the recover threshold and the alert threshold: no alert, no
    recovery notice, no flapping while it hovers."""
    at, sent = rig
    at(92)
    at(w.DISK_ALERT_PCT - 1)          # below alert, above recover
    assert len(sent) == 1


# ── the reading itself ────────────────────────────────────────────────

def test_disk_is_read_without_subprocess_or_a_tree_walk(monkeypatch):
    """A full disk is when you can least afford `du`, and fork() may be failing
    under the memory pressure that tends to accompany it."""
    def boom(*a, **k):
        raise AssertionError("watchdog must not shell out to read the disk")

    monkeypatch.setattr(w.subprocess, "run", boom)
    pct, free_gb, total_gb = w._disk("/")
    assert 0 <= pct <= 100 and free_gb >= 0 and total_gb > 0


# ── memory keeps working ──────────────────────────────────────────────

def test_memory_alerts_still_fire(rig, monkeypatch):
    at, sent = rig
    monkeypatch.setattr(w, "_meminfo", lambda: (100, 4000, 0))
    monkeypatch.setattr(w, "_top_mem", lambda n=5: "  900MB python3")
    at(50)
    assert len(sent) == 1 and "메모리 부족" in sent[0]


def test_the_two_subsystems_keep_separate_state(rig, monkeypatch, tmp_path):
    """One being low must not silence or reset the other."""
    at, sent = rig
    monkeypatch.setattr(w, "_meminfo", lambda: (100, 4000, 0))
    monkeypatch.setattr(w, "_top_mem", lambda n=5: "  900MB python3")
    at(92)
    assert len(sent) == 2
    st = _state(tmp_path)
    assert st["mem"]["state"] == "low" and st["disk"]["state"] == "low"


def test_a_legacy_state_file_is_migrated(tmp_path, monkeypatch):
    """The old file held memory only, flat. Dropping it would re-alert for a
    condition already reported."""
    monkeypatch.setattr(w, "STATE_FILE", str(tmp_path / "s.json"))
    json.dump({"state": "low", "last_alert": 12345}, open(tmp_path / "s.json", "w"))
    st = w._load_state()
    assert st["mem"] == {"state": "low", "last_alert": 12345}
    assert st["disk"] == {"state": "ok", "last_alert": 0}
