"""Daily X (Twitter) results card for the public KeepMyPick track record.

The card is the credibility artifact: it posts YESTERDAY's graded picks —
losses included — with no cumulative ROI% (that number is 82% carried by
overnight picks a US reader cannot actually bet, so headlining it would sell
a record nobody can reproduce). Game first, then the picks on it, so a totals
line ("Over 6.5") is identifiable — the label alone names no game.
"""
import datetime

import keepmypick_x_post as card


def row(event_id="E1", away="Orix Buffaloes", home="Fukuoka SoftBank",
        label="Under 6.5", book="Caesars", odds=2.0, result="win", pnl=2.34,
        commence_time="2026-07-22T09:00:00Z"):
    return {"event_id": event_id, "away": away, "home": home, "label": label,
            "book": book, "odds": odds, "result": result, "pnl": pnl,
            "commence_time": commence_time}


DAY = datetime.date(2026, 7, 21)


def test_groups_picks_under_their_game_with_the_matchup_first():
    """Two picks on ONE game print the matchup once, then both picks."""
    rows = [
        row(event_id="E1", label="Under 6.5", result="win", pnl=2.34),
        row(event_id="E1", label="Fukuoka SoftBank Hawks -1.5", book="BetMGM",
            odds=2.45, result="lose", pnl=-2.00),
        row(event_id="E2", away="Yokohama DeNA", home="Hanshin",
            label="Over 6.5", result="lose", pnl=-2.96),
    ]
    out = card.compose_card(rows, DAY)
    assert out.count("Orix Buffaloes @ Fukuoka SoftBank") == 1   # not repeated
    assert "Yokohama DeNA @ Hanshin" in out
    # matchup line comes BEFORE the picks that belong to it
    assert out.index("Orix Buffaloes @ Fukuoka SoftBank") < out.index("Under 6.5")
    assert "✅ Under 6.5 +100 · Caesars" in out
    assert "❌ Fukuoka SoftBank Hawks -1.5 +145 · BetMGM" in out


def test_day_total_excludes_push_and_void_from_the_record():
    rows = [
        row(event_id="E1", result="win", pnl=2.34),
        row(event_id="E2", result="lose", pnl=-2.00),
        row(event_id="E3", result="push", pnl=0.0),
        row(event_id="E4", result="void", pnl=0.0),
    ]
    out = card.compose_card(rows, DAY)
    assert "Day: 1-1" in out                     # push/void not counted W-L
    assert "+0.17u" in out                       # (2.34-2.00)/2.0
    assert "(push)" in out and "(void)" in out   # but still shown, nothing hidden


def test_no_graded_picks_means_no_card():
    assert card.compose_card([], DAY) is None


def test_truncates_whole_games_when_over_the_char_limit():
    rows = [row(event_id="E%d" % i, away="Team A%d" % i, home="Team B%d" % i)
            for i in range(10)]
    out = card.compose_card(rows, DAY, max_chars=200)
    assert len(out) <= 200
    assert "more" in out              # tells the reader it was cut, never silent
