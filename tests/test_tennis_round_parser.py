"""Tennis round parser — added 2026-05-11.

Extracts round identifier from Kalshi tennis market titles for
cell-expansion analysis at T+14 archetype decision."""
from strategies.sports_trader import parse_tennis_round


def test_round_of_128():
    title = "Will Carlos Alcaraz win the Alcaraz vs Qualifier: Round Of 128 match?"
    assert parse_tennis_round(title) == "R128"


def test_round_of_64():
    title = "Will Coco Gauff win the Gauff vs Andreeva: Round Of 64 match?"
    assert parse_tennis_round(title) == "R64"


def test_round_of_32():
    title = "Will Sinner win the Sinner vs Medvedev: Round Of 32 match?"
    assert parse_tennis_round(title) == "R32"


def test_round_of_16():
    title = "Will Thiago Agustin Tirante win the Tirante vs Medvedev: Round Of 16 match?"
    assert parse_tennis_round(title) == "R16"


def test_quarterfinal():
    title = "Will Coco Gauff win the Gauff vs Andreeva: Quarterfinal match?"
    assert parse_tennis_round(title) == "QF"


def test_semifinal():
    title = "Will Alcaraz win the Alcaraz vs Sinner: Semifinal match?"
    assert parse_tennis_round(title) == "SF"


def test_final():
    title = "Will Sinner win the Sinner vs Alcaraz: Final match?"
    assert parse_tennis_round(title) == "F"


def test_case_insensitive():
    title = "WILL ALCARAZ WIN THE ALCARAZ VS SINNER: SEMIFINAL MATCH?"
    assert parse_tennis_round(title) == "SF"


def test_no_round_returns_none():
    title = "Will Wells win the Wells vs Dalby professional MMA fight scheduled for May 16"
    assert parse_tennis_round(title) is None


def test_empty_title_returns_none():
    assert parse_tennis_round("") is None
    assert parse_tennis_round(None) is None  # type: ignore[arg-type]


def test_malformed_round_returns_none():
    title = "Will X win the Y vs Z: Group Stage match?"
    assert parse_tennis_round(title) is None
