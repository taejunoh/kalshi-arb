# tests/test_softbook_deeplink.py
"""Tiered sportsbook deep-link builder. FD/DK -> best-effort bet-slip prefill
from provider-returned links/SIDs; all other books -> open event/app + copyable
bet text (always present so a failed prefill degrades to manual entry)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_deeplink as dl  # noqa: E402

PICK = {"book": "FanDuel", "label": "Brandon Nimmo TB Over 1.5", "odds": 2.2,
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Brandon Nimmo"}


def test_copy_text_always_present():
    out = dl.build("fanduel", {}, PICK)
    assert out["copy_text"] == "FanDuel · Brandon Nimmo TB Over 1.5 @ 2.20"


def test_fanduel_uses_provider_betslip_universal_link():
    links = {"betslip": "https://sportsbook.fanduel.com/addToBetslip?marketId=42&selectionId=99"}
    out = dl.build("fanduel", links, PICK)
    assert out["tier"] == "prefill"
    assert out["url"] == links["betslip"]


def test_fanduel_without_betslip_link_falls_back_to_event():
    out = dl.build("fanduel", {"event": "https://sportsbook.fanduel.com/baseball"}, PICK)
    assert out["tier"] == "event"
    assert out["url"] == "https://sportsbook.fanduel.com/baseball"


def test_draftkings_builds_dksb_scheme_from_sid():
    out = dl.build("draftkings", {"sid": "0OU76623766U1250_3"}, PICK)
    assert out["tier"] == "prefill"
    assert out["url"] == "dksb://sb/addbet/0OU76623766U1250_3"


def test_draftkings_prefers_provider_betslip_link_over_dksb_scheme():
    # The Odds API returns a real https ?outcomes= betslip link for DK; prefer it
    # over the hand-built dksb:// scheme (which was never primary-source verified).
    links = {"betslip": "https://sportsbook.draftkings.com/?outcomes=0ML123_3",
             "sid": "0ML123_3"}
    out = dl.build("draftkings", links, PICK)
    assert out["tier"] == "prefill"
    assert out["url"] == "https://sportsbook.draftkings.com/?outcomes=0ML123_3"


def test_draftkings_without_sid_uses_event_link():
    out = dl.build("draftkings", {"event": "https://sportsbook.draftkings.com/event/123"}, PICK)
    assert out["tier"] == "event"
    assert out["url"] == "https://sportsbook.draftkings.com/event/123"


def test_non_prefill_book_never_prefills_even_with_betslip_link():
    # BetMGM/Caesars/etc: prefill is NOT primary-source confirmed -> never claim it
    out = dl.build("betmgm", {"betslip": "https://example/bet"}, PICK)
    assert out["tier"] in ("event", "home")
    assert out["url"] != "https://example/bet"


def test_unknown_book_with_no_links_falls_back_to_none_url():
    out = dl.build("ballybet", {}, PICK)
    assert out["tier"] == "home"
    assert out["url"] is None          # app has no scheme/link -> copy-only
    assert out["copy_text"].startswith("BallyBet ·")


def test_extract_links_pulls_outcome_betslip_and_sid():
    ev = {"bookmakers": [{"key": "fanduel", "markets": [
        {"key": "batter_total_bases", "link": "https://fd/market", "outcomes": [
            {"name": "Over", "description": "Brandon Nimmo", "point": 1.5,
             "price": 2.2, "link": "https://fd/betslip?x=1", "sid": "S1"}]}]}]}
    got = dl.extract_links(ev, "fanduel", "batter_total_bases", "Over", 1.5, "Brandon Nimmo")
    assert got["betslip"] == "https://fd/betslip?x=1"
    assert got["sid"] == "S1"


def test_extract_links_matches_h2h_with_point_none():
    ev = {"bookmakers": [{"key": "draftkings", "markets": [
        {"key": "h2h", "outcomes": [
            {"name": "New York Mets", "price": 1.9, "sid": "DK1"}]}]}]}
    got = dl.extract_links(ev, "draftkings", "h2h", "New York Mets", None, None)
    assert got["sid"] == "DK1"
