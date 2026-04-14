"""Ensure _save_traded_tickers uses atomic tempfile+replace so a crash
mid-write doesn't leave a truncated JSON that prevents strategies from
remembering which tickers they've already bet."""
import json
import os
from unittest.mock import MagicMock, patch

from strategies.sports_trader import SportsTraderStrategy


def _make_strategy(tmp_file: str) -> SportsTraderStrategy:
    config = MagicMock()
    config.KALSHI_FEE_PER_CONTRACT = 0.07
    config.ODDS_API_KEY = ""
    config.ODDS_API_REGION = "eu"
    s = SportsTraderStrategy(config, MagicMock(), MagicMock())
    s.TRADED_TICKERS_FILE = tmp_file  # override per-instance
    s._traded_tickers = {}
    return s


def test_save_traded_tickers_writes_atomically(tmp_path):
    """Final file content must be valid JSON, written via tempfile + os.replace."""
    target = str(tmp_path / "tickers.json")
    s = _make_strategy(target)
    s._traded_tickers = {"NBA-1": 1700000000.0, "MLB-2": 1700000500.0}
    s._save_traded_tickers()

    assert os.path.exists(target)
    with open(target) as f:
        data = json.load(f)
    assert data == {"NBA-1": 1700000000.0, "MLB-2": 1700000500.0}
    # Temp file shouldn't leak after successful write
    assert not os.path.exists(target + ".tmp")


def test_save_traded_tickers_preserves_existing_on_write_failure(tmp_path):
    """If the write crashes mid-way, the existing file should remain intact."""
    target = str(tmp_path / "tickers.json")
    with open(target, "w") as f:
        json.dump({"NBA-EXISTING": 1.0}, f)

    s = _make_strategy(target)
    s._traded_tickers = {"NBA-NEW": 2.0}

    # Simulate os.replace failure (e.g. disk full or permissions)
    with patch("os.replace", side_effect=OSError("disk full")):
        s._save_traded_tickers()  # should swallow exception, not crash

    # Original file must still be readable and unchanged
    with open(target) as f:
        data = json.load(f)
    assert data == {"NBA-EXISTING": 1.0}


def test_save_traded_tickers_survives_partial_tmp_file(tmp_path):
    """Even if a stale .tmp file exists from a prior crashed run, save works."""
    target = str(tmp_path / "tickers.json")
    # Leave a stale/corrupted tmp from a previous crash
    with open(target + ".tmp", "w") as f:
        f.write("{corrupted")

    s = _make_strategy(target)
    s._traded_tickers = {"NBA-3": 3.0}
    s._save_traded_tickers()

    with open(target) as f:
        data = json.load(f)
    assert data == {"NBA-3": 3.0}
