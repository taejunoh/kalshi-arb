#!/usr/bin/env python3
"""NPB final scores from npb.jp (the Odds API has NO scores feed for NPB).

Used by softbook_ev_paper_pnl.settle() as a fallback so NPB game-market paper
bets settle automatically right after games end instead of sitting pending
until manual settlement / stale-void. Only returns a score when the page shows
試合終了 (game over) — a live/partial linescore is never used.

Flow: commence (UTC) -> JST date -> npb.jp/scores/YYYY/MMDD/ index -> each game
page -> linescore rows (visitor first; last 3 numeric cells = R/H/E) -> match
teams by Japanese name fragment. One index + <=6 game fetches per date, cached.
"""
import re, html, urllib.request
from datetime import datetime, timedelta

# Odds API English team name -> distinctive Japanese fragment on npb.jp
TEAMS = {
    "hanshin tigers": "阪神",
    "fukuoka softbank hawks": "ソフトバンク",
    "hokkaido nippon-ham fighters": "日本ハム",
    "yokohama dena baystars": "ベイスターズ",
    "saitama seibu lions": "西武",
    "hiroshima toyo carp": "広島",
    "chiba lotte marines": "ロッテ",
    "chunichi dragons": "中日",
    "orix buffaloes": "オリックス",
    "tokyo yakult swallows": "ヤクルト",
    "tohoku rakuten golden eagles": "楽天",
    "yomiuri giants": "巨人",
}

_day_cache = {}        # "YYYY/MMDD" -> list[(jp_team, runs)] pairs per finished game


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        return urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        if len(body) > 2000:        # npb.jp soft-403: real content with a 403 status
            return body
        raise


def _jst_day(commence_iso):
    try:
        dt = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
    except Exception:
        return None
    return (dt + timedelta(hours=9)).strftime("%Y/%m%d")     # game day in Japan


def _linescore(page):
    """[(team_jp, runs)] from a game page — visitor row first, last 3 cells R/H/E."""
    out = []
    for r in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip()
                 for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, re.S)]
        if len(cells) >= 4 and all(c.isdigit() for c in cells[-3:]):
            out.append((cells[0], int(cells[-3])))
    return out[:2]


def _finished_games(day):
    if day in _day_cache:
        return _day_cache[day]
    games = []
    try:
        idx = _get(f"https://npb.jp/scores/{day}/")
        codes = sorted(set(re.findall(r"/scores/%s/([a-z]+-[a-z]+-\d+)/" % day, idx)))
        for code in codes:
            try:
                page = html.unescape(_get(f"https://npb.jp/scores/{day}/{code}/"))
            except Exception:
                continue
            if "試合終了" not in page:          # not final (live/前) -> never use
                continue
            rows = _linescore(page)
            if len(rows) == 2:
                games.append(rows)
    except Exception:
        pass
    _day_cache[day] = games
    return games


def scores_for(away, home, commence_iso):
    """{away: runs, home: runs} for a FINISHED NPB game, else None."""
    fa = TEAMS.get((away or "").lower().strip())
    fh = TEAMS.get((home or "").lower().strip())
    day = _jst_day(commence_iso)
    if not (fa and fh and day):
        return None
    for rows in _finished_games(day):
        names = [n for n, _ in rows]
        if any(fa in n for n in names) and any(fh in n for n in names):
            ra = next(r for n, r in rows if fa in n)
            rh = next(r for n, r in rows if fh in n)
            return {away: ra, home: rh}
    return None


if __name__ == "__main__":
    # self-test on the finished 2026-06-10 slate (JST)
    cases = [
        ("Chunichi Dragons", "Chiba Lotte Marines", "2026-06-10T09:00:00Z", 11, 4),
        ("Tokyo Yakult Swallows", "Orix Buffaloes", "2026-06-10T09:00:00Z", 4, 8),
        ("Yomiuri Giants", "Tohoku Rakuten Golden Eagles", "2026-06-10T09:00:00Z", 7, 0),
    ]
    for away, home, ct, ea, eh in cases:
        sc = scores_for(away, home, ct)
        ok = sc and sc[away] == ea and sc[home] == eh
        print(f"  {away[:14]:<14} @ {home[:14]:<14} -> {sc}  {'OK' if ok else 'CHECK'}")
