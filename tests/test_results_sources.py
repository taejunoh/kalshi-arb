"""The non-Odds-API result sources, and the three ways they were failing.

Found 2026-08-05 while asking why five bets had gone unsettled for weeks:

  1. _http_json sent one header set to both providers. Naver needs a browser UA
     and its own Referer; ESPN 403s on ANY User-Agent. So every ESPN call had
     been returning 403 — silently, because the fetchers swallow exceptions and
     return []. handles() claims tennis and MMA and neither had been settling:
     12 tennis bets were waiting.

  2. The KBO fetcher only set a winner when the scores differed, so a tie came
     back indistinguishable from "no result yet" and was retried forever. The
     07-21 KT Wiz-Doosan 2-2 was still open 15 days later. grade_bet already
     mapped "draw" to push; nothing ever produced one.

  3. WNBA had no source here at all, so it fell through to the Odds API's
     /scores?daysFrom=3 — a bet older than three days can never settle. Two
     07-17 bets were stuck behind exactly that.

Boxing is still unsourced: ESPN has no boxing scoreboard endpoint (404).
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import softbook_results as R  # noqa: E402


# ── 1. per-host headers ───────────────────────────────────────────────

def _headers_for(url, monkeypatch):
    seen = {}

    class _Resp:
        def read(self): return b"{}"
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, timeout=None):
        seen["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(R.urllib.request, "urlopen", fake_urlopen)
    R._http_json(url)
    return {k.lower(): v for k, v in seen["headers"].items()}


def test_espn_is_called_with_no_headers(monkeypatch):
    """Verified against the live host: a UA — any of four tried, with and
    without a Referer — gets 403; no headers at all returns the scoreboard."""
    h = _headers_for(f"{R.ESPN_BASE}/tennis/atp/scoreboard?dates=20260805", monkeypatch)
    assert "user-agent" not in h and "referer" not in h


def test_naver_still_gets_its_ua_and_referer(monkeypatch):
    """The mirror failure: strip these and Naver breaks instead."""
    h = _headers_for(f"{R.NAVER_KBO}?fromDate=2026-07-21", monkeypatch)
    assert "naver" in h.get("referer", "")
    assert h.get("user-agent")


# ── 2. a KBO tie is a result ──────────────────────────────────────────

def _kbo(fetch_json):
    return R._results_for("baseball_kbo", "2026-07-21T09:30:00Z", fetch_json, {})


def _naver(games):
    return lambda url: {"result": {"games": games}}


def _game(home, away, hs, as_, status="RESULT"):
    return {"homeTeamName": home, "awayTeamName": away, "homeTeamScore": hs,
            "awayTeamScore": as_, "statusCode": status}


def test_a_tie_grades_as_a_push_not_a_retry():
    """The 07-21 KT Wiz-Doosan 2-2, retried every 15 minutes for 15 days."""
    recs = _kbo(_naver([_game("KT", "두산", 2, 2)]))
    assert recs and recs[0]["winner"] == "draw"
    assert R.grade_bet("baseball_kbo", "KT Wiz", "Doosan Bears",
                       "2026-07-21T09:30:00Z", "h2h", "KT Wiz", 0.0, "",
                       fetch_json=_naver([_game("KT", "두산", 2, 2)])) == "push"


@pytest.mark.parametrize("hs,as_,side,want", [(7, 3, "Kia Tigers", "win"),
                                              (7, 3, "Hanwha Eagles", "lose")])
def test_a_decided_game_still_grades_normally(hs, as_, side, want):
    f = _naver([_game("KIA", "한화", hs, as_)])
    assert R.grade_bet("baseball_kbo", "Kia Tigers", "Hanwha Eagles",
                       "2026-07-21T09:30:00Z", "h2h", side, 0.0, "",
                       fetch_json=f) == want


def test_an_unfinished_game_is_still_unknown():
    """The distinction the tie bug erased: no result yet must stay retryable."""
    recs = _kbo(_naver([_game("KT", "두산", 0, 0, status="BEFORE")]))
    assert recs and recs[0]["winner"] is None and recs[0]["status"] == "pending"


# ── 3. WNBA is sourced here now ───────────────────────────────────────

def test_wnba_is_handled():
    assert R.handles("basketball_wnba")


def test_wnba_reads_espn_and_not_the_odds_api():
    urls = []

    def fetch(url):
        urls.append(url)
        return {"events": []}

    R._results_for("basketball_wnba", "2026-07-17T01:00:00Z", fetch, {})
    assert urls and all("espn.com" in u and "/basketball/wnba/" in u for u in urls)
    assert any("dates=20260717" in u for u in urls)
    assert any("dates=20260716" in u for u in urls), "late tip-offs bucket back a day"


def test_a_postponed_game_voids_rather_than_hanging():
    """Dallas @ New York on 07-17 was STATUS_POSTPONED — the two stuck WNBA bets
    were on it, and void (refund) is the right answer, not a permanent retry."""
    def fetch(url):
        return {"events": [{"competitions": [{
            "competitors": [
                {"team": {"displayName": "New York Liberty"}, "score": "0"},
                {"team": {"displayName": "Dallas Wings"}, "score": "0"}],
            "status": {"type": {"name": "STATUS_POSTPONED"}}}]}]}
    assert R.grade_bet("basketball_wnba", "New York Liberty", "Dallas Wings",
                       "2026-07-17T01:00:00Z", "h2h", "New York Liberty", 0.0, "",
                       fetch_json=fetch) == "void"


# ── what is still unsourced ───────────────────────────────────────────

def test_boxing_is_still_not_claimed():
    """ESPN returns 404 for a boxing scoreboard. Claiming it would send those
    bets down a path that always yields None instead of into the stuck report."""
    assert not R.handles("boxing_boxing")
