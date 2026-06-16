"""External results adapters (softbook_results) — added 2026-06-16.

Covers the ESPN (tennis/NCAA/MMA) + Naver (KBO) sourcing + grading the paper and
app settlers use for sports the Odds API can't score. Fixtures mirror the real
payload shapes verified live 2026-06-16. HTTP is stubbed via fetch_json.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_results as R                                   # noqa: E402

ATP = "2026-06-16T09:30:00Z"
NCAA = "2026-06-15T23:00:00Z"
MMA = "2026-06-15T03:30:00Z"      # late-ET card -> ESPN buckets to 06-14
KBO = "2026-06-16T09:30:00Z"


def fetch(payloads):
    """Stub: return the payload whose key is a substring of the requested URL."""
    def f(url, timeout=20):
        for key, val in payloads.items():
            if key in url:
                return val
        return {}
    return f


# ---- fixtures ----

def _tennis_match(a, b, a_ls, b_ls, status="STATUS_FINAL", a_win=True):
    return {"status": {"type": {"name": status}}, "competitors": [
        {"athlete": {"displayName": a}, "winner": a_win,
         "linescores": [{"value": v} for v in a_ls]},
        {"athlete": {"displayName": b}, "winner": (not a_win) if status != "STATUS_WALKOVER" else False,
         "linescores": [{"value": v} for v in b_ls]}]}


def tennis_payload(matches, doubles=True):
    groupings = [{"grouping": {"displayName": "Men's Singles"}, "competitions": matches}]
    if doubles:                       # doubles must be ignored (athlete names absent)
        groupings.append({"grouping": {"displayName": "Men's Doubles"}, "competitions": [
            {"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
                {"winner": True, "linescores": [{"value": 6}, {"value": 6}]},
                {"winner": False, "linescores": [{"value": 3}, {"value": 4}]}]}]})
    return {"events": [{"name": "Test Open", "date": "2026-06-13T00:00Z", "groupings": groupings}]}


def atp_fetch(matches):
    return fetch({"tennis/atp": tennis_payload(matches)})


# Ethan Quinn (AWAY) d. Karen Khachanov (HOME) 1-6 6-4 6-4 -> winner has FEWER games (13 v 14)
QUINN = _tennis_match("Ethan Quinn", "Karen Khachanov", [1, 6, 6], [6, 4, 4])


# ---- tests: tennis ----

def test_tennis_h2h_by_winner_not_games():
    """Match winner with FEWER total games still wins the moneyline."""
    f = atp_fetch([QUINN])
    # Quinn is away, Khachanov home
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "h2h", "Ethan Quinn", None, fetch_json=f) == "win"
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "h2h", "Karen Khachanov", None, fetch_json=f) == "lose"


def test_tennis_spread():
    f = atp_fetch([QUINN])             # Quinn 13, Khachanov 14
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "spread", "Ethan Quinn", 2.5, fetch_json=f) == "win"   # -1+2.5>0
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "spread", "Karen Khachanov", -2.5, fetch_json=f) == "lose"


def test_tennis_total():
    f = atp_fetch([QUINN])             # total games = 27
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "total", "Over", 23.5, fetch_json=f) == "win"
    assert R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP,
                       "total", "Under", 23.5, fetch_json=f) == "lose"


def test_tennis_retirement_h2h_action_spread_total_void():
    ret = _tennis_match("Ekaterina Alexandrova", "Anastasia Potapova", [6], [1],
                        status="STATUS_RETIRED", a_win=True)
    f = fetch({"tennis/wta": tennis_payload([ret])})
    # h2h still grades to the advancing player
    assert R.grade_bet("tennis_wta_x", "Ekaterina Alexandrova", "Anastasia Potapova",
                       ATP, "h2h", "Ekaterina Alexandrova", None, fetch_json=f) == "win"
    # spread/total on a retirement -> void (no action)
    assert R.grade_bet("tennis_wta_x", "Ekaterina Alexandrova", "Anastasia Potapova",
                       ATP, "total", "Over", 10.5, fetch_json=f) == "void"
    assert R.grade_bet("tennis_wta_x", "Ekaterina Alexandrova", "Anastasia Potapova",
                       ATP, "spread", "Ekaterina Alexandrova", -3.5, fetch_json=f) == "void"


def test_tennis_walkover_void():
    wo = _tennis_match("Player A", "Player B", [], [], status="STATUS_WALKOVER")
    f = atp_fetch([wo])
    assert R.grade_bet("tennis_atp_x", "Player A", "Player B", ATP,
                       "h2h", "Player A", None, fetch_json=f) == "void"


def test_tennis_scheduled_pending():
    sched = _tennis_match("Player A", "Player B", [], [], status="STATUS_SCHEDULED")
    f = atp_fetch([sched])
    assert R.grade_bet("tennis_atp_x", "Player A", "Player B", ATP,
                       "h2h", "Player A", None, fetch_json=f) is None


def test_tennis_name_normalization_accents():
    m = _tennis_match("Emma Raducanu", "Iva Jovic", [6, 6], [2, 2])  # ESPN drops the diacritic
    f = fetch({"tennis/wta": tennis_payload([m])})
    # ledger has the accented form
    assert R.grade_bet("tennis_wta_x", "Emma Raducanu", "Iva Jović", ATP,
                       "h2h", "Emma Raducanu", None, fetch_json=f) == "win"
    assert R.grade_bet("tennis_wta_x", "Emma Raducanu", "Iva Jović", ATP,
                       "spread", "Emma Raducanu", -3.5, fetch_json=f) == "win"  # 12-4-3.5>0


def test_tennis_surname_first_ordering():
    """ESPN lists Chinese players surname-first (Wang Xinyu); Odds API given-first
    (Xinyu Wang). Token-set matching must reconcile both, on both sides."""
    m = _tennis_match("Wang Xinyu", "Zhang Shuai", [6, 1], [1, 3])
    f = atp_fetch([m])
    assert R.grade_bet("tennis_atp_x", "Xinyu Wang", "Shuai Zhang", ATP,
                       "h2h", "Xinyu Wang", None, fetch_json=f) == "win"
    assert R.grade_bet("tennis_atp_x", "Xinyu Wang", "Shuai Zhang", ATP,
                       "total", "Under", 12.5, fetch_json=f) == "win"   # 11<12.5


def test_tennis_not_found_pending():
    f = atp_fetch([QUINN])
    assert R.grade_bet("tennis_atp_x", "Nobody One", "Nobody Two", ATP,
                       "h2h", "Nobody One", None, fetch_json=f) is None


# ---- tests: NCAA baseball ----

def ncaa_fetch():
    payload = {"events": [{"competitions": [{"status": {"type": {"name": "STATUS_FINAL"}},
        "competitors": [
            {"team": {"displayName": "Oklahoma Sooners"}, "score": "4", "winner": True},
            {"team": {"displayName": "Georgia Bulldogs"}, "score": "3", "winner": False}]}]}]}
    return fetch({"baseball/college-baseball": payload})


def test_ncaa_h2h_spread_total():
    f = ncaa_fetch()                   # Oklahoma 4 (home), Georgia 3 (away)
    assert R.grade_bet("baseball_ncaa", "Oklahoma Sooners", "Georgia Bulldogs", NCAA,
                       "h2h", "Oklahoma Sooners", None, fetch_json=f) == "win"
    assert R.grade_bet("baseball_ncaa", "Oklahoma Sooners", "Georgia Bulldogs", NCAA,
                       "spread", "Georgia Bulldogs", 1.5, fetch_json=f) == "win"   # -1+1.5>0
    assert R.grade_bet("baseball_ncaa", "Oklahoma Sooners", "Georgia Bulldogs", NCAA,
                       "total", "Under", 7.5, fetch_json=f) == "win"               # 7<7.5
    assert R.grade_bet("baseball_ncaa", "Oklahoma Sooners", "Georgia Bulldogs", NCAA,
                       "total", "Over", 7.5, fetch_json=f) == "lose"


# ---- tests: MMA ----

def mma_fetch():
    payload = {"events": [{"competitions": [
        {"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [
            {"athlete": {"displayName": "Ciryl Gane"}, "winner": True},
            {"athlete": {"displayName": "Alex Pereira"}, "winner": False}]},
        {"status": {"type": {"name": "STATUS_FINAL"}}, "competitors": [   # draw / NC
            {"athlete": {"displayName": "Fighter A"}, "winner": False},
            {"athlete": {"displayName": "Fighter B"}, "winner": False}]}]}]}
    return fetch({"mma/ufc": payload})


def test_mma_h2h_and_jr_suffix():
    f = mma_fetch()
    assert R.grade_bet("mma_mixed_martial_arts", "Alex Pereira", "Ciryl Gane", MMA,
                       "h2h", "Ciryl Gane", None, fetch_json=f) == "win"
    assert R.grade_bet("mma_mixed_martial_arts", "Alex Pereira", "Ciryl Gane", MMA,
                       "h2h", "Alex Pereira", None, fetch_json=f) == "lose"


def test_mma_draw_push():
    f = mma_fetch()
    assert R.grade_bet("mma_mixed_martial_arts", "Fighter A", "Fighter B", MMA,
                       "h2h", "Fighter A", None, fetch_json=f) == "push"


# ---- tests: KBO (Naver) ----

def kbo_fetch():
    payload = {"result": {"games": [
        {"statusCode": "RESULT", "awayTeamName": "한화", "awayTeamScore": 5,
         "homeTeamName": "NC", "homeTeamScore": 6},
        {"statusCode": "CANCEL", "awayTeamName": "LG", "homeTeamName": "KIA",
         "awayTeamScore": None, "homeTeamScore": None}]}}
    return fetch({"schedule/games": payload})


def test_kbo_korean_mapping_and_markets():
    f = kbo_fetch()                    # Hanwha 5 @ NC 6
    assert R.grade_bet("baseball_kbo", "NC Dinos", "Hanwha Eagles", KBO,
                       "h2h", "NC Dinos", None, fetch_json=f) == "win"
    assert R.grade_bet("baseball_kbo", "NC Dinos", "Hanwha Eagles", KBO,
                       "spread", "NC Dinos", -1.5, fetch_json=f) == "lose"     # 6-5-1.5<0
    assert R.grade_bet("baseball_kbo", "NC Dinos", "Hanwha Eagles", KBO,
                       "total", "Under", 11.5, fetch_json=f) == "win"          # 11<11.5
    assert R.grade_bet("baseball_kbo", "NC Dinos", "Hanwha Eagles", KBO,
                       "team_total", "Over", 5.5, "NC Dinos", fetch_json=f) == "win"  # NC=6


def test_kbo_cancel_void():
    f = kbo_fetch()
    assert R.grade_bet("baseball_kbo", "Kia Tigers", "LG Twins", KBO,
                       "h2h", "LG Twins", None, fetch_json=f) == "void"


# ---- tests: dispatch ----

def test_handles():
    for s in ("tennis_atp_halle_open", "tennis_wta_german_open", "baseball_kbo",
              "baseball_ncaa", "mma_mixed_martial_arts"):
        assert R.handles(s)
    for s in ("baseball_mlb", "basketball_wnba", "soccer_fifa_world_cup",
              "boxing_boxing", "baseball_npb"):
        assert not R.handles(s)


def test_cache_reused_across_calls():
    calls = {"n": 0}

    def counting(url, timeout=20):
        calls["n"] += 1
        return tennis_payload([QUINN])
    cache = {}
    R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP, "h2h",
                "Ethan Quinn", None, fetch_json=counting, cache=cache)
    first = calls["n"]
    R.grade_bet("tennis_atp_x", "Karen Khachanov", "Ethan Quinn", ATP, "spread",
                "Ethan Quinn", 2.5, fetch_json=counting, cache=cache)
    assert calls["n"] == first         # second bet served from cache, no new fetch
