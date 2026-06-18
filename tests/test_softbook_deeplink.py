# tests/test_softbook_deeplink.py
"""Tiered sportsbook deep-link builder. FD/DK/Caesars/BetRivers -> best-effort bet-slip
prefill from provider links/SIDs (placeholders substituted); BetMGM/theScore/others ->
open event/app + copyable bet text (always present so a failed prefill degrades to
manual entry). See app/DEEPLINK_VERIFICATION.md for the per-book verification record."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import softbook_deeplink as dl  # noqa: E402

PICK = {"book": "FanDuel", "label": "Brandon Nimmo TB Over 1.5", "odds": 2.2,
        "market": "batter_total_bases", "side": "Over", "point": 1.5,
        "player": "Brandon Nimmo"}


def test_copy_text_always_present():
    out = dl.build("fanduel", {}, PICK)
    assert out["copy_text"] == "FanDuel · Brandon Nimmo TB Over 1.5 @ +120"  # 2.2 -> american


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


def test_betmgm_stays_event_betslip_link_drops_selection_on_redirect():
    # Verified 2026-06-15: BetMGM's options= betslip link 301-redirects sports.{state}.betmgm
    # -> www.betmgm.com which DROPS the ?options= param -> homepage (selection lost), and its
    # AASA is broken — prefill is not achievable. Stay event-tier; the event link survives the
    # redirect to the right game page, and its {state} placeholder is still substituted.
    out = dl.build("betmgm",
                   {"betslip": "https://sports.{state}.betmgm.com/en/sports?options=X&type=Single",
                    "event": "https://sports.{state}.betmgm.com/en/sports/events/foo-19684635"}, PICK)
    assert out["tier"] == "event"
    assert "options=" not in (out["url"] or "")
    assert "{state}" not in out["url"]
    assert out["url"] == "https://sports.ny.betmgm.com/en/sports/events/foo-19684635"


def test_betrivers_prefill_fills_kambi_coupon_placeholders():
    # Verified 2026-06-15: BetRivers only failed because the builder left {pickType}/{wagerAmount}
    # literal -> malformed -> homepage. Filled in, the Kambi coupon (coupon=single|<sid>|, empty
    # trailing stake) loads the selection in the slip. Opens the mobile-web Kambi slip (no applinks
    # AASA on ny.betrivers.com) but is a real prefill -> prefill-tier.
    links = {"betslip": "https://{state}.betrivers.com/?page=sportsbook#event/1024786776"
                        "?coupon={pickType}|4222431357|{wagerAmount}"}
    out = dl.build("betrivers", links, PICK)
    assert out["tier"] == "prefill"
    assert "{" not in out["url"]  # every placeholder substituted, no literal braces left
    assert out["url"] == ("https://ny.betrivers.com/?page=sportsbook"
                          "#event/1024786776?coupon=single|4222431357|")


def test_fill_placeholders_substitutes_all_known_tokens():
    got = dl._fill_placeholders("https://{state}.x/#e/1?coupon={pickType}|55|{wagerAmount}")
    assert got == "https://ny.x/#e/1?coupon=single|55|"


def test_fanatics_home_opens_native_app_via_onelink_universal_link():
    # Odds API gives Fanatics only a sid (no link) and sportsbook.fanatics.com has no AASA. The
    # af_dp value "fanaticssportsbook://" is NOT a registered iOS scheme (on-device Safari test
    # failed). Use the AppsFlyer OneLink universal link — fanatics.onelink.me AASA claims /5kut/*
    # for com.fbg.sportsbook, so this https URL opens the native app (no scheme whitelist needed).
    out = dl.build("fanatics", {"sid": "1530070174"}, PICK)
    assert out["tier"] == "home"
    assert out["url"] == "https://fanatics.onelink.me/5kut/swz1hzmb"
    assert out["copy_text"].startswith("Fanatics ·")


def test_non_prefill_book_never_prefills_even_with_betslip_link():
    # Fanatics (sid only, no provider link) / BallyBet: prefill not available -> never claim it
    out = dl.build("fanatics", {"betslip": "https://example/bet"}, PICK)
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
