"""late-crosser Ledger filter on /api/ev-history — added 2026-06-13.

High-trigger (paper_bet_high) = first-entry-members ∪ late-crossers. A High-trigger
row is a late-crosser iff its (event,market,player) is NOT in the First-entry High
ledger (paper_bet, tier='High') — i.e. the line only reached High after moving
(earliest +EV snapshot was Med/Low). That subset settled ~breakeven dilution; the
filter lets the dashboard show it apart from the first-entry +ROI core.
"""
import os
import sqlite3
import sys

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from monitoring.dashboard.api import create_app          # noqa: E402
from core.database import Database                        # noqa: E402

COLS = ("commence_time TEXT, placed_at TEXT, settled_at TEXT, sport TEXT, market TEXT, "
        "label TEXT, home TEXT, away TEXT, book TEXT, odds REAL, ev REAL, stake REAL, "
        "tier TEXT, conf REAL, completed INT, won INT, result TEXT, pnl REAL, "
        "event_id TEXT, player TEXT")


def _seed(path):
    db = sqlite3.connect(path)
    for t in ("paper_bet", "paper_bet_high"):
        db.execute(f"CREATE TABLE {t} ({COLS})")

    def ins(t, event_id, market, player, tier="High"):
        row = dict(commence_time="2026-06-13T23:00:00Z", placed_at="2026-06-13T20:00:00Z",
                   settled_at=None, sport="baseball_mlb", market=market, label="x",
                   home="H", away="A", book="FanDuel", odds=2.0, ev=0.03, stake=2.0,
                   tier=tier, conf=60.0, completed=1, won=1, result="win", pnl=2.0,
                   event_id=event_id, player=player)
        db.execute(f"INSERT INTO {t} VALUES ({','.join('?' * len(row))})", tuple(row.values()))

    # First-entry ledger (A): two High keys define the +ROI core, one Med (NOT a key)
    ins("paper_bet", "E1", "total", "", tier="High")
    ins("paper_bet", "E1", "pitcher_strikeouts", "Skubal", tier="High")
    ins("paper_bet", "E3", "h2h", "", tier="Med")
    # High-trigger ledger (B): 2 first-entry members + 2 late-crossers
    ins("paper_bet_high", "E1", "total", "")                  # first-entry (key in A-High)
    ins("paper_bet_high", "E1", "pitcher_strikeouts", "Skubal")  # first-entry
    ins("paper_bet_high", "E2", "spread", "")                 # late (E2 absent from A)
    ins("paper_bet_high", "E3", "h2h", "")                    # late (E3 is Med in A, not High)
    db.commit()
    db.close()


@pytest_asyncio.fixture
async def client(tmp_path):
    database = Database(str(tmp_path / "kalshi.db"))
    await database.initialize()
    app = create_app(database, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    await database.close()


@pytest.mark.asyncio
async def test_high_trigger_returns_all_lines(client, tmp_path, monkeypatch):
    pdb = tmp_path / "softbook_paper.db"
    _seed(str(pdb))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(pdb))
    data = (await client.get("/api/ev-history?strategy=high")).json()
    assert data["count"] == 4


@pytest.mark.asyncio
async def test_late_crosser_is_high_minus_first_entry(client, tmp_path, monkeypatch):
    pdb = tmp_path / "softbook_paper.db"
    _seed(str(pdb))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(pdb))
    data = (await client.get("/api/ev-history?strategy=late")).json()
    assert data["count"] == 2
    keys = {(b["event_id"], b["market"]) for b in data["bets"]}
    assert keys == {("E2", "spread"), ("E3", "h2h")}      # E1 lines excluded (first-entry)


@pytest.mark.asyncio
async def test_first_entry_plus_late_equals_high(client, tmp_path, monkeypatch):
    pdb = tmp_path / "softbook_paper.db"
    _seed(str(pdb))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(pdb))
    high = (await client.get("/api/ev-history?strategy=high")).json()["count"]
    late = (await client.get("/api/ev-history?strategy=late")).json()["count"]
    # the 2 dropped are exactly the first-entry MEMBERS of high (E1 ×2)
    assert high - late == 2


@pytest.mark.asyncio
async def test_first_entry_default_unchanged(client, tmp_path, monkeypatch):
    pdb = tmp_path / "softbook_paper.db"
    _seed(str(pdb))
    monkeypatch.setenv("SOFTBOOK_PAPER_DB", str(pdb))
    data = (await client.get("/api/ev-history")).json()   # default strategy=first
    assert data["count"] == 3                             # all paper_bet rows (High+High+Med)
