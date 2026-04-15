from core.pinnacle_age import PinnacleAgeTracker


def test_first_observation_age_is_zero():
    t = PinnacleAgeTracker()
    age = t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    assert age == 0


def test_repeated_hash_returns_elapsed():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    age = t.observe("eu", "basketball_nba", "hashA", now=1042.0)
    assert age == 42


def test_new_hash_resets_age():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    t.observe("eu", "basketball_nba", "hashA", now=1042.0)
    age = t.observe("eu", "basketball_nba", "hashB", now=1050.0)
    assert age == 0


def test_per_region_sport_isolation():
    t = PinnacleAgeTracker()
    t.observe("eu", "basketball_nba", "hashA", now=1000.0)
    age = t.observe("us", "basketball_nba", "hashA", now=1042.0)
    assert age == 0  # different region, independent tracker


def test_none_hash_returns_none():
    t = PinnacleAgeTracker()
    age = t.observe("eu", "basketball_nba", None, now=1000.0)
    assert age is None
