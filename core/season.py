"""Season classification — playoff vs regular season detection from a Kalshi
ticker plus trade timestamp.

Used to instrument trade rows so future analysis can split per-sport edge by
season type (e.g. "is the underdog edge a playoff-only artifact?"). Kalshi
ticker format gives no playoff hint; we infer from the calendar window.

Windows are approximate (start of postseason → end of finals). When a sport's
schedule shifts year-to-year by a few days, edge cases on the boundary are
tagged on the basis of the dominant assumption (early-window = regular,
late-window = playoff). Acceptable since this is for analysis only, not gating.
"""
from datetime import datetime
from typing import Optional


# (month_start, day_start, month_end, day_end) inclusive window per sport.
# Update each Jan when MLB / NBA / NHL / WNBA announce next year's schedule.
# Order matters: WNBAGAME must be checked BEFORE NBAGAME because the
# substring `NBAGAME` matches inside `WNBAGAME` (2026-05-13 incident —
# WNBA observations incorrectly tagged as NBA playoff for ~3 days).
_PLAYOFF_WINDOWS = {
    "WNBAGAME": (9, 21, 10, 19),  # WNBA playoffs: late Sep - mid Oct
    "NBAGAME":  (4, 14, 6, 25),   # NBA playoffs: mid-April to mid-late June
    "NHLGAME":  (4, 14, 6, 25),   # NHL playoffs: mid-April to mid-late June
    "MLBGAME":  (10, 1, 11, 5),   # MLB postseason: Oct 1 to ~Nov 5
}

# Iterate longer keys first so WNBAGAME wins the substring match against
# NBAGAME (Python dict preserves insertion order, but be explicit).
_PLAYOFF_WINDOW_KEYS_BY_LEN = sorted(_PLAYOFF_WINDOWS, key=len, reverse=True)


def is_playoff(ticker: str, timestamp_iso: Optional[str]) -> Optional[bool]:
    """Return True if the trade ticker falls inside its sport's playoff
    calendar window. Returns None when the ticker isn't a known sport
    or the timestamp can't be parsed (so callers can distinguish "regular
    season" (False) from "unknown" (None) if they care)."""
    if not ticker or not timestamp_iso:
        return None
    sport_key = next(
        (s for s in _PLAYOFF_WINDOW_KEYS_BY_LEN if s in ticker), None
    )
    if not sport_key:
        return None
    try:
        d = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    m_start, d_start, m_end, d_end = _PLAYOFF_WINDOWS[sport_key]
    md = (d.month, d.day)
    return (m_start, d_start) <= md <= (m_end, d_end)
