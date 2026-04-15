import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_sample_once_returns_rows_per_region_sport():
    from scripts.measure_odds_freshness import sample_once, REGIONS, SPORTS

    class FakeResponse:
        headers = {
            "x-requests-remaining": "10000",
            "x-requests-used": "1",
            "x-requests-last": "1",
        }

        def raise_for_status(self):
            pass

        def json(self):
            return [
                {
                    "id": "g1",
                    "bookmakers": [
                        {
                            "key": "pinnacle",
                            "markets": [
                                {
                                    "key": "h2h",
                                    "outcomes": [
                                        {"name": "A", "price": 1.9},
                                        {"name": "B", "price": 2.0},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]

    async def fake_get(*a, **k):
        return FakeResponse()

    with patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=fake_get)):
        rows = await sample_once("fakekey", tick=7)

    assert isinstance(rows, list)
    assert len(rows) == len(REGIONS) * len(SPORTS)
    for r in rows:
        assert r["tick"] == 7
        assert "region" in r and "sport" in r and "ts" in r
