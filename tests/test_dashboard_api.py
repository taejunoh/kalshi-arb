import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from monitoring.dashboard.api import create_app
from core.database import Database


@pytest_asyncio.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    # Seed some test data
    await database.record_trade(
        strategy="sum_arb", market_id="m1", direction="YES",
        price=0.48, size=100.0, fee=1.51, status="filled", pnl=4.0,
    )
    await database.record_trade(
        strategy="maker_preorder", market_id="m2", direction="BUY",
        price=0.47, size=50.0, fee=0.0, status="filled", pnl=2.0,
    )
    await database.record_order(
        order_id="order_1", market_id="m3", strategy="maker_preorder",
        side="BUY", price=0.48, size=25.0, order_type="limit", status="open",
    )
    yield database
    await database.close()


@pytest_asyncio.fixture
async def client(db):
    app = create_app(db, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_get_status(client):
    resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["bankroll"] == 1000.0
    assert "daily_pnl" in data
    assert "trade_count" in data


@pytest.mark.asyncio
async def test_get_trades(client):
    resp = await client.get("/api/trades")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


@pytest.mark.asyncio
async def test_get_trades_filter_by_strategy(client):
    resp = await client.get("/api/trades?strategy=sum_arb")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["strategy"] == "sum_arb"


@pytest.mark.asyncio
async def test_get_open_orders(client):
    resp = await client.get("/api/orders")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["order_id"] == "order_1"


@pytest.mark.asyncio
async def test_get_performance_paper_bucket_from_db(db):
    """Paper bucket is built from DB trades with paper_* status."""
    # Seed a paper trade (sum_arb trade from fixture was 'filled' = Live;
    # Live data now comes from Kalshi API, so seed a paper one for this test)
    await db.record_trade(
        strategy="sports_midshot", market_id="KXNBAGAME-test", direction="yes",
        price=0.30, size=3.0, fee=0.07, status="paper_settled", pnl=6.0,
    )
    app = create_app(db, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    assert data["sports_midshot"]["paper"]["trade_count"] == 1
    assert data["sports_midshot"]["paper"]["total_pnl"] == 6.0
    assert data["sports_midshot"]["paper"]["wins"] == 1
    # Live bucket empty when no kalshi_client provided
    assert data["sports_midshot"]["live"]["trade_count"] == 0


@pytest.mark.asyncio
async def test_get_performance_live_bucket_from_kalshi(db):
    """Live bucket is reconstructed from Kalshi API — positions + settlements."""
    class MockKalshi:
        def get_positions(self):
            return [
                {"ticker": "KXFEDMENTION-26APR-SHOC", "position_fp": "156",
                 "market_exposure_dollars": "45.16"},
                {"ticker": "KXCPI-26APR-T0.4", "position_fp": "1",
                 "market_exposure_dollars": "0.38"},
                {"ticker": "KXFEDMENTION-26APR-CLOSED", "position_fp": "0",
                 "market_exposure_dollars": "0"},  # zero position, skip
            ]
        def get_settlements(self, limit=200):
            return [
                # Mentions trader winning settle
                {"ticker": "KXFEDMENTION-26MAR-OLD",
                 "revenue": 2000, "yes_total_cost_dollars": "10.00",
                 "no_total_cost_dollars": "0", "fee_cost": "0.50",
                 "market_result": "yes"},
                # Econ trader losing settle
                {"ticker": "KXCPI-26MAR-T0.3",
                 "revenue": 0, "yes_total_cost_dollars": "0",
                 "no_total_cost_dollars": "5.00", "fee_cost": "0.10",
                 "market_result": "yes"},
            ]

    app = create_app(db, kalshi_client=MockKalshi())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    # mentions_trader: 1 open position (SHOC) + 1 winning settle = 2 trades
    m = data["mentions_trader"]["live"]
    assert m["trade_count"] == 2
    assert m["wins"] == 1
    assert m["losses"] == 0
    # net = 20 - 10 - 0.5 = 9.5 (settled only; open position has no PnL yet)
    assert m["total_pnl"] == pytest.approx(9.5, abs=0.01)
    # total_size = 10 (settled cost) + 45.16 (open exposure)
    assert m["total_size"] == pytest.approx(55.16, abs=0.01)
    # econ_trader is RETIRED — surfaced in /api/performance with archived=true
    # so the dashboard can render it in the Archived section.
    assert data["econ_trader"]["archived"] is True


@pytest.mark.asyncio
async def test_performance_paper_includes_clv_positive_rate(db):
    """Paper bucket exposes clv_positive_rate for each strategy."""
    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m-win", direction="YES",
        price=0.40, size=3.0, fee=0.07, status="paper_settled", pnl=1.5,
    )
    # trade_id 3 (after 2 fixture trades)
    await db.record_clv_snapshot(trade_id=3, snapshot_at="entry", yes_ask=0.40, no_ask=0.62)
    await db.record_clv_snapshot(trade_id=3, snapshot_at="closing", yes_ask=0.55, no_ask=0.47)

    await db.record_trade(
        strategy="sports_pinnacle_edge", market_id="m-lose", direction="YES",
        price=0.60, size=3.0, fee=0.07, status="paper_settled", pnl=-3.0,
    )
    await db.record_clv_snapshot(trade_id=4, snapshot_at="entry", yes_ask=0.60, no_ask=0.42)
    await db.record_clv_snapshot(trade_id=4, snapshot_at="closing", yes_ask=0.50, no_ask=0.52)

    app = create_app(db, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    # 1 of 2 favorable → 50%
    assert data["sports_pinnacle_edge"]["paper"]["clv_positive_rate"] == pytest.approx(0.5, abs=1e-6)


@pytest.mark.asyncio
async def test_strategy_status_marks_loop_disabled(db):
    """Strategies removed from main.py _strategy_loop should report active=false.
    sports_trader / maker_preorder → 'removed_from_loop'.
    shutdown_fade → DEPRECATED reason."""
    class MockConfig:
        LIVE_STRATEGIES = set()
        MM_ENABLED = True
        SHUTDOWN_FADE_ENABLED = True
        SPREAD_SCANNER_ENABLED = True
        MENTIONS_ENABLED = True
        ECON_ENABLED = True
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    assert resp.status_code == 200
    data = resp.json()
    # sports_trader / econ_trader are archived — surfaced with archived=true
    # so the dashboard can render them in the Archived section.
    assert data["sports_trader"]["archived"] is True
    assert data["econ_trader"]["archived"] is True
    assert data["maker_preorder"]["active"] is False
    assert data["maker_preorder"]["reason"] == "removed_from_loop"
    assert data["shutdown_fade"]["active"] is False
    assert "DEPRECATED" in data["shutdown_fade"]["reason"]
    # All V3 sports variants retired 2026-04-17 (V31 is strict subset);
    # pinnacle_edge also retired same day for -37.5% ROI pending re-evaluation.
    for retired in ("sports_longshot", "sports_midshot", "sports_underdog",
                    "sports_pinnacle_edge"):
        assert data[retired]["active"] is False, retired
        assert data[retired]["reason"] == "removed_from_loop", retired
    # v31 siblings + v4_edge still active.
    assert data["sports_longshot_v31"]["active"] is True
    assert data["sports_midshot_v31"]["active"] is True
    assert data["sports_underdog_v31"]["active"] is True
    assert data["sports_v4_edge"]["active"] is True


@pytest.mark.asyncio
async def test_strategy_status_marks_env_disabled(db):
    """Strategies with ENABLED flag=false should report active=false with
    reason like 'MM_ENABLED=false'."""
    class MockConfig:
        LIVE_STRATEGIES = set()
        MM_ENABLED = False
        SHUTDOWN_FADE_ENABLED = False
        SPREAD_SCANNER_ENABLED = True
        MENTIONS_ENABLED = True
        ECON_ENABLED = True
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    data = resp.json()
    assert data["market_maker"]["active"] is False
    assert data["market_maker"]["reason"] == "MM_ENABLED=false"
    # shutdown_fade: DEPRECATED takes precedence over env flag
    assert data["shutdown_fade"]["active"] is False
    assert "DEPRECATED" in data["shutdown_fade"]["reason"]
    assert data["spread_scanner"]["active"] is True
    assert data["mentions_trader"]["mode"] == "PAPER"


@pytest.mark.asyncio
async def test_settlement_history_categorizes_by_strategy_name(db):
    """Settlement history groups settlements into strategy-name categories.
    Ensures econ_trader settlements don't get dumped into 'other' bucket."""
    class MockKalshi:
        def get_settlements(self, limit=200):
            return [
                # Econ trader loss
                {"ticker": "KXCPI-26MAR-T0.3", "revenue": 0,
                 "yes_total_cost_dollars": "0", "no_total_cost_dollars": "5.00",
                 "fee_cost": "0.10", "market_result": "yes", "settled_time": "2026-04-10T13:00Z"},
                # Sports trader win
                {"ticker": "KXNBAGAME-26APR10TEAM-X", "revenue": 1800,
                 "yes_total_cost_dollars": "6.00", "no_total_cost_dollars": "0",
                 "fee_cost": "0.30", "market_result": "yes", "settled_time": "2026-04-11T02:00Z"},
                # Mentions trader (future)
                {"ticker": "KXFEDMENTION-26APR-FAKE", "revenue": 1000,
                 "yes_total_cost_dollars": "8.00", "no_total_cost_dollars": "0",
                 "fee_cost": "0.20", "market_result": "yes", "settled_time": "2026-04-30T20:00Z"},
                # Shutdown fade
                {"ticker": "KXNUMSHUTDOWNS-27JAN01-T2", "revenue": 2000,
                 "yes_total_cost_dollars": "0", "no_total_cost_dollars": "5.00",
                 "fee_cost": "0.10", "market_result": "no", "settled_time": "2027-01-01T15:00Z"},
            ]
        def get_positions(self):
            return []

    app = create_app(db, kalshi_client=MockKalshi())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/settlement-history")
    data = resp.json()
    assert "econ_trader" in data
    assert data["econ_trader"]["losses"] == 1
    assert data["sports_trader"]["wins"] == 1
    assert data["mentions_trader"]["wins"] == 1
    assert data["shutdown_fade"]["wins"] == 1
    # 'other' bucket stays empty because all tickers match a known strategy
    assert data["other"]["wins"] == 0 and data["other"]["losses"] == 0


@pytest.mark.asyncio
async def test_strategy_status_mode_live_for_live_strategies(db):
    """mentions_trader in LIVE_STRATEGIES should show mode=LIVE."""
    class MockConfig:
        LIVE_STRATEGIES = {"mentions_trader"}
        MM_ENABLED = False
        SHUTDOWN_FADE_ENABLED = False
        SPREAD_SCANNER_ENABLED = False
        MENTIONS_ENABLED = True
        ECON_ENABLED = False
        SPORTS_ENABLED = True

    app = create_app(db, config=MockConfig())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/strategy-status")
    data = resp.json()
    # mentions_trader retired 2026-04-17 — sports v31 became the main live
    # focus; mentions stays in LIVE_STRATEGIES env for legacy consistency
    # but is removed from the scan loop, so active=False here.
    assert data["mentions_trader"]["mode"] == "LIVE"
    assert data["mentions_trader"]["active"] is False
    assert data["mentions_trader"]["reason"] == "removed_from_loop"
    assert data["sports_midshot"]["mode"] == "PAPER"


@pytest.mark.asyncio
async def test_performance_live_pre_live_split_includes_demoted_strategy(tmp_path):
    """Live bucket pre/live split must count settled trades from a strategy
    even after it has been removed from LIVE_STRATEGIES (demoted to paper).
    Otherwise the dashboard shows e.g. 8 cumulative live trades but 0/0 in
    the pre/live breakdown — the case observed for sports_longshot_v35
    after 2026-04-28 demote."""
    database = Database(str(tmp_path / "t.db"))
    await database.initialize()
    # 2 settled live trades from when the strategy WAS live, both pre-game
    await database.record_trade(
        strategy="sports_longshot_v35", market_id="KXNBAGAME-pre1", direction="yes",
        price=0.25, size=4.0, fee=0.07, status="settled", pnl=-4.0,
        minutes_to_start=120.0,
    )
    await database.record_trade(
        strategy="sports_longshot_v35", market_id="KXNBAGAME-pre2", direction="yes",
        price=0.22, size=4.0, fee=0.07, status="settled", pnl=-4.0,
        minutes_to_start=180.0,
    )
    # 1 settled live live-phase trade
    await database.record_trade(
        strategy="sports_longshot_v35", market_id="KXNBAGAME-live", direction="yes",
        price=0.25, size=4.0, fee=0.07, status="settled", pnl=-4.0,
        minutes_to_start=-30.0,
    )

    # Build a Config that does NOT have sports_longshot_v35 in LIVE_STRATEGIES
    # (simulating post-demote state).
    class FakeConfig:
        LIVE_STRATEGIES = {"sports_longshot_v31"}  # demoted strategy NOT here

    # Mock Kalshi to enable the cumulative-live aggregation block (currently
    # gated on `if kalshi_client:`). The DB rows are what drive the assertion.
    class MockKalshi:
        def get_positions(self):
            return []
        def get_settlements(self, limit=200):
            return []

    app = create_app(
        database, bankroll=1000.0, config=FakeConfig(), kalshi_client=MockKalshi(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    live_bucket = data["sports_longshot_v35"]["live"]
    # Cumulative count was already correct: 3 settled
    assert live_bucket["trade_count"] == 3
    # The fix: pre/live split must mirror the cumulative even after demote
    assert live_bucket["pre"]["trade_count"] == 2
    assert live_bucket["pre"]["total_pnl"] == pytest.approx(-8.0)
    assert live_bucket["pre"]["losses"] == 2
    assert live_bucket["live"]["trade_count"] == 1
    assert live_bucket["live"]["total_pnl"] == pytest.approx(-4.0)
    assert live_bucket["live"]["losses"] == 1


@pytest.mark.asyncio
async def test_performance_live_includes_crypto_phase1_db_attribution(tmp_path):
    """Crypto Phase 1 LIVE trades must be DB-attributed to the dashboard's
    'Strategy Performance (Live)' bucket. Kalshi settlements API can't
    disambiguate which Phase 1 trader (atm_hold / atm_fade / deribit_tail_fade
    / late_close_tail / bid_imbalance) bought a given KXBTC/KXETH ticker, so
    the pre-fix logic (sports prefix only) dropped these on the floor — the
    case observed 2026-05-20 where 6 atm_hold_v1 + 3 deribit_tail_fade_v1
    LIVE settled rows existed in DB but rendered 0 on the dashboard.
    """
    database = Database(str(tmp_path / "t.db"))
    await database.initialize()
    # 1 winning live atm_hold_v1
    await database.record_trade(
        strategy="atm_hold_v1", market_id="KXBTC-26MAY2001-B76750",
        direction="yes", price=0.63, size=19.53, fee=2.66,
        status="settled", pnl=9.30,
    )
    # 1 losing live atm_hold_v1
    await database.record_trade(
        strategy="atm_hold_v1", market_id="KXBTC-26MAY2005-B77450",
        direction="yes", price=0.51, size=19.89, fee=2.73,
        status="settled", pnl=-19.89,
    )
    # 1 winning live deribit_tail_fade_v1
    await database.record_trade(
        strategy="deribit_tail_fade_v1", market_id="KXBTC-26MAY2002-B76750",
        direction="no", price=0.92, size=19.32, fee=0.4,
        status="settled", pnl=0.21,
    )

    class FakeConfig:
        LIVE_STRATEGIES = {"atm_hold_v1", "deribit_tail_fade_v1"}

    class MockKalshi:
        def get_positions(self): return []
        def get_settlements(self, limit=200): return []

    app = create_app(
        database, bankroll=1000.0, config=FakeConfig(), kalshi_client=MockKalshi(),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()

    atm = data["atm_hold_v1"]["live"]
    assert atm["trade_count"] == 2
    assert atm["wins"] == 1
    assert atm["losses"] == 1
    assert atm["total_pnl"] == pytest.approx(9.30 - 19.89, abs=0.01)

    deribit = data["deribit_tail_fade_v1"]["live"]
    assert deribit["trade_count"] == 1
    assert deribit["wins"] == 1
    assert deribit["total_pnl"] == pytest.approx(0.21, abs=0.01)


@pytest.mark.asyncio
async def test_performance_splits_sports_paper_pre_live(tmp_path):
    database = Database(str(tmp_path / "t.db"))
    await database.initialize()
    # Pre-game winner
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-x", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=4.0,
        minutes_to_start=45.0,
    )
    # Pre-game loser
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-y", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
        minutes_to_start=120.0,
    )
    # Live loser
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-z", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
        minutes_to_start=-30.0,
    )
    # Untagged (still counts in top-level total only)
    await database.record_trade(
        strategy="sports_longshot", market_id="KXMLBGAME-w", direction="yes",
        price=0.3, size=2.0, fee=0.07, status="paper_settled", pnl=-2.0,
    )
    # Non-sports strategy should NOT get pre/live keys
    await database.record_trade(
        strategy="market_maker", market_id="m1", direction="yes",
        price=0.5, size=5.0, fee=0.07, status="paper_filled", pnl=0.0,
    )
    app = create_app(database, bankroll=1000.0)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/performance")
    data = resp.json()
    longshot_paper = data["sports_longshot"]["paper"]
    assert longshot_paper["trade_count"] == 4          # total unchanged
    assert longshot_paper["pre"]["trade_count"] == 2
    assert longshot_paper["pre"]["total_pnl"] == pytest.approx(2.0)
    assert longshot_paper["pre"]["wins"] == 1
    assert longshot_paper["pre"]["losses"] == 1
    assert longshot_paper["live"]["trade_count"] == 1
    assert longshot_paper["live"]["total_pnl"] == pytest.approx(-2.0)
    assert longshot_paper["live"]["wins"] == 0
    assert longshot_paper["live"]["losses"] == 1
    # Non-sports: no pre/live keys
    mm_paper = data["market_maker"]["paper"]
    assert "pre" not in mm_paper
    assert "live" not in mm_paper
