# tests/test_softbook_deeplink.py
"""Tiered sportsbook deep-link builder. FD/DK/Caesars -> best-effort NATIVE-app bet-slip
prefill from provider links/SIDs ({state} substituted); BetMGM/theScore/BetRivers/others ->
open event/app + copy the SEARCH entity (name to paste into the book's search box; always
present so a non-prefill link degrades to a one-tap search). See app/DEEPLINK_VERIFICATION.md
for the per-book verification record."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_deeplink as dl  # noqa: E402

PICK = {"book": "FanDuel", "label": "Brandon Nimmo TB Over 1.5", "odds": 2.2,
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Brandon Nimmo"}


def test_copy_text_is_search_entity_always_present():
    # copy_text is the SEARCH entity (name to paste into the book's search box), NOT a bet line.
    out = dl.build("fanduel", {}, PICK)
    assert out["copy_text"] == "Brandon Nimmo"  # the prop's player — no book/odds/market noise


def test_copy_text_search_entity_by_market():
    # h2h -> the backed team/player (the user's Fanatics tennis ML case that failed search)
    h2h = dl.build("fanatics", {"sid": "1"},
                   {"market": "h2h", "side": "Mananchaya Sawangkaew", "point": None,
                    "player": None, "odds": 2.35})
    assert h2h["copy_text"] == "Mananchaya Sawangkaew"
    # spread -> the team (the -1.5 line is dropped so search matches the team, not "-1.5")
    sp = dl.build("fanduel", {}, {"market": "spread", "side": "New York Yankees",
                                  "point": -1.5, "player": None, "odds": 1.91})
    assert sp["copy_text"] == "New York Yankees"
    # total -> the game has no name in the pick, so search a team from the matchup
    tot = dl.build("fanduel", {}, {"market": "total", "side": "Over", "point": 8.5,
                                   "player": None, "home": "Boston Red Sox",
                                   "away": "New York Yankees", "odds": 1.95})
    assert tot["copy_text"] == "Boston Red Sox"
    # prop -> the player
    pr = dl.build("fanduel", {}, {"market": "batter_total_bases", "side": "Over", "point": 1.5,
                                  "player": "Brandon Nimmo", "odds": 2.2})
    assert pr["copy_text"] == "Brandon Nimmo"


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


def test_thescore_excluded_from_prefill_opens_app_not_slip():
    # On-device theScore's app opens from the link but does NOT load the selection -> not prefill.
    out = dl.build("espnbet",
                   {"betslip": "https://sportsbook.thescore.bet/?market_selection_id[0]=abc",
                    "event": "https://sportsbook.thescore.bet/event/1"}, PICK)
    assert out["tier"] != "prefill"
    assert "market_selection_id" not in (out["url"] or "")


def test_caesars_state_placeholder_substituted_in_betslip_link():
    # Caesars' betslip link carries a {state} placeholder -> replace with the NY user's state.
    out = dl.build("williamhill_us",
                   {"betslip": "https://sportsbook.caesars.com/us/{state}/bet/betslip?selectionIds=X"}, PICK)
    assert out["tier"] == "prefill"
    assert "{state}" not in out["url"]
    assert "/us/ny/bet/betslip" in out["url"]


def test_betmgm_is_copy_only_never_emits_a_url():
    # 2026-07-08: BetMGM is COPY-ONLY, same shape as BetRivers. Verified 2026-06-15: the
    # options= betslip link 301-redirects sports.{state}.betmgm.com -> www.betmgm.com, which
    # DROPS the ?options= param -> homepage (selection lost); BetMGM's AASA is itself broken
    # (redirect + malformed appIDs) and no custom URL scheme exists, so even the surviving
    # EVENT link only ever opens Safari, never the app. Emit NONE so the app never launches
    # Safari and shows the honest "복사 · 앱에서 검색" label instead of the old, false
    # "복사 + 직접 앱 열기".
    links = {"betslip": "https://sports.{state}.betmgm.com/en/sports?options=X&type=Single",
             "event": "https://sports.{state}.betmgm.com/en/sports/events/foo-19684635"}
    out = dl.build("betmgm", links, PICK)
    assert out["url"] is None            # never opens Safari
    assert out["tier"] == "home"
    assert out["copy_text"] == "Brandon Nimmo"   # still the search entity to paste


def test_betrivers_is_copy_only_never_emits_a_url():
    # 2026-07-01: BetRivers is COPY-ONLY. No applinks AASA (https -> Safari, never the app) AND
    # no custom URL scheme (on-device verified: betrivers://, rushstreet://, rsi://, … all
    # unregistered). Any url we could emit only opens a Safari tab, which the user does not want,
    # so emit NONE — even when the API hands us a betslip AND an event link. Copy the search
    # entity; the user opens the BetRivers app manually and pastes.
    links = {"betslip": "https://ny.betrivers.com/?page=sportsbook#event/1?coupon=single|X|",
             "event": "https://ny.betrivers.com/?page=sportsbook#event/1"}
    out = dl.build("betrivers", links, PICK)
    assert out["url"] is None            # never opens Safari
    assert out["tier"] == "home"
    assert out["copy_text"] == "Brandon Nimmo"   # still the search entity to paste


def test_fill_placeholders_substitutes_state_token():
    got = dl._fill_placeholders("https://sports.{state}.betmgm.com/en/sports/events/foo-1")
    assert got == "https://sports.ny.betmgm.com/en/sports/events/foo-1"


def test_fanatics_home_opens_native_app_via_onelink_universal_link():
    # Odds API gives Fanatics only a sid (no link) and sportsbook.fanatics.com has no AASA. The
    # af_dp value "fanaticssportsbook://" is NOT a registered iOS scheme (on-device Safari test
    # failed). Use the AppsFlyer OneLink universal link — fanatics.onelink.me AASA claims /5kut/*
    # for com.fbg.sportsbook, so this https URL opens the native app (no scheme whitelist needed).
    out = dl.build("fanatics", {"sid": "1530070174"}, PICK)
    assert out["tier"] == "home"
    assert out["url"] == "https://fanatics.onelink.me/5kut/swz1hzmb"
    assert out["copy_text"] == "Brandon Nimmo"   # search entity to paste into Fanatics search


def test_non_prefill_book_never_prefills_even_with_betslip_link():
    # Fanatics (sid only, no provider link) / BallyBet: prefill not available -> never claim it
    out = dl.build("fanatics", {"betslip": "https://example/bet"}, PICK)
    assert out["tier"] in ("event", "home")
    assert out["url"] != "https://example/bet"


def test_unknown_book_with_no_links_falls_back_to_none_url():
    out = dl.build("ballybet", {}, PICK)
    assert out["tier"] == "home"
    assert out["url"] is None          # app has no scheme/link -> copy-only
    assert out["copy_text"] == "Brandon Nimmo"   # still the search entity (copy-only fallback)


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
