# scripts/softbook_deeplink.py
#!/usr/bin/env python3
"""Tiered sportsbook deep-link builder for the +EV companion app.

Reality (researched + adversarially verified 2026-06-15, see PREFILL_BOOKS below):
bet-slip PREFILL works for FanDuel (addToBetslip universal link), DraftKings
(?outcomes= / dksb://sb/addbet/<sid>) and Caesars (/bet/betslip?selectionIds=, {state}
filled) — all open the NATIVE app on a populated slip. BetMGM (options= link drops the
selection on a host redirect), theScore (native router ignores the web quick-bet param; no
scheme) and BetRivers (no applinks AASA at all → every link opens a mobile-WEB slip in
Safari, not the app) do NOT prefill — they open the event page + copy. Links/SIDs come from
The Odds API includeLinks/includeSids; we never hand-build hosts (state drift), only
substitute the {state} placeholder the API leaves in. copy_text is the clean SEARCH entity
(player / team / matchup name — NO book prefix, odds, or line, which all break search) to
paste into the book's search box, so a non-prefill link degrades to a one-tap search, never
a dead end. The bet side/line/odds stay visible on the app's pick card.

Returned dict: {"tier": "prefill"|"event"|"home", "url": str|None, "copy_text": str}
"""
import softbook_ev_core as core

# Books whose Odds API (includeLinks/includeSids) bet-slip link resolves to a PRE-FILLED
# slip IN THE NATIVE APP. On-device verified (NY iPhone, 2026-06-15): FanDuel, DraftKings,
# and Caesars (williamhill_us; link carries a {state} placeholder we substitute) open the
# native app with the slip populated.
#
# Deliberately EXCLUDED (prefill NOT achievable or not worth it — do not re-chase; see
# app/DEEPLINK_VERIFICATION.md):
#  - BetRivers (betrivers): DEMOTED 2026-06-30 (user preference). ny.betrivers.com serves NO
#    applinks AASA (webcredentials only), so even the working Kambi coupon link
#    (#event/<id>?coupon=single|<sid>|) only ever opened a mobile-WEB slip in Safari, never
#    the native app. A prefilled slip in a stray Safari tab is worse UX than an honest
#    event-page open + copyable text, so BetRivers now stays event/home tier like BetMGM.
#  - BetMGM (betmgm): the options= betslip link 301-redirects sports.{state}.betmgm.com ->
#    www.betmgm.com, which DROPS the ?options= param -> homepage (selection lost, before any
#    app/AASA matters); BetMGM's AASA is itself broken (redirect + malformed appIDs) and no
#    custom URL scheme exists. Even the surviving EVENT link only ever opens Safari, never the
#    app -> COPY-ONLY as of 2026-07-08, same reasoning as BetRivers below.
#  - theScore (espnbet): AASA components ["*"] so the app DOES open, but market_selection_id[0]
#    =... is theScore's WEB SPA quick-bet format the native in-app router ignores; no custom
#    scheme, and the real external path (Branch.io) isn't constructable from a raw sid. The
#    event link opens the app on the game page = best achievable.
#  - Fanatics (sid only, no link) / BallyBet (no link): event/home + copy.
PREFILL_BOOKS = {"fanduel", "draftkings", "williamhill_us"}

# Books iOS cannot open natively AT ALL — emit NO url (copy-only), because any url we could
# emit is an https link that just launches Safari, which the user does not want.
#  - BetRivers (betrivers): no applinks AASA (https -> Safari, never the app) AND no custom URL
#    scheme — on-device verified 2026-07-01 that betrivers://, betriverssportsbook://,
#    rushstreet://, rushstreetinteractive://, rsi://, playsugarhouse:// are all unregistered.
#    So even the event link only opened a Safari tab. Copy the search entity; user opens the
#    BetRivers app manually and pastes. (BallyBet is already copy-only via HOME_URL=None.)
#  - BetMGM (betmgm): same shape as BetRivers — AASA is broken (redirect + malformed appIDs)
#    and no custom URL scheme exists, so the surviving event link (see PREFILL_BOOKS bullet
#    above) only ever opens Safari, never the app. Moved to copy-only 2026-07-08 (was
#    event-tier, which mislabeled itself "복사 + 직접 앱 열기" in the companion app despite
#    never opening the native app).
COPY_ONLY_BOOKS = {"betrivers", "betmgm"}

# US state for {state} host/path placeholders (NY user).
STATE = "ny"


def _fill_placeholders(url):
    """Substitute the {state} placeholder the Odds API leaves in some bet-slip / event links
    (Caesars betslip path, BetMGM/BetRivers event hosts) with the NY user's state. Leaving it
    un-substituted resolves to a broken host."""
    return url.replace("{state}", STATE)

# App-home fallbacks (open the app/site to its home when no event/betslip link is
# available). https hosts open the native app only if that host has an applinks AASA,
# else Safari. None => copy-only.
#  - Fanatics: the Odds API gives ONLY a sid (no link) and sportsbook.fanatics.com has NO
#    AASA, so the web URL opened Safari, not the app. The af_dp value "fanaticssportsbook://"
#    is NOT a registered iOS URL scheme (on-device Safari test: did not open) — it's only
#    AppsFlyer's internal deep link used AFTER the app is launched. Use the brand's AppsFlyer
#    OneLink instead: fanatics.onelink.me serves an AASA claiming /5kut/* (and /Xqu0/, /nQVy/)
#    for com.fbg.sportsbook (App Store id1616738407), so this https UNIVERSAL link opens the
#    native app directly — NO LSApplicationQueriesSchemes / canOpenURL needed (it's https).
#    Opens to the Discover home — no prefill (the sid can't address a betslip).
HOME_URL = {
    "fanduel": "https://sportsbook.fanduel.com",
    "draftkings": "https://sportsbook.draftkings.com",
    "betmgm": "https://sports.nj.betmgm.com",
    "williamhill_us": "https://www.williamhill.us",
    "betrivers": "https://www.betrivers.com",
    "fanatics": "https://fanatics.onelink.me/5kut/swz1hzmb",
    "espnbet": "https://espnbet.com",
    "ballybet": None,
}


def _search_text(pick):
    """The clean entity to paste into a sportsbook's SEARCH box — no book prefix, no odds, no
    market/line noise (those break search). The bet side/line/odds stay visible on the app's
    pick card; search only needs the NAME of the thing to find. Mirrors core.label's branching:
      h2h / spread -> the team/player being backed (side)
      total        -> the game has no name in the pick, so search a team from the matchup
                      (home, else away; the raw 'Over'/'Under' side is a useless last resort)
      prop / other -> the player (team_totals lands here too, with player = the team name)."""
    m = pick.get("market")
    if m == "total":
        return pick.get("home") or pick.get("away") or pick.get("side") or ""
    if m in ("h2h", "spread"):
        return pick.get("side") or ""
    return pick.get("player") or pick.get("side") or ""


def build(book_key, links, pick):
    """book_key: Odds API key (e.g. 'fanduel'). links: {betslip?, sid?, event?} extracted for
    THIS outcome. pick: dict with market/side/point/player[/home/away]; copy_text is the SEARCH
    entity to paste into the book's search box (see _search_text)."""
    copy_text = _search_text(pick)
    url, tier = None, "home"

    if book_key in COPY_ONLY_BOOKS:
        # No native app path (no AASA, no scheme) — never emit a url that would only open Safari.
        return {"tier": "home", "url": None, "copy_text": copy_text}

    if book_key in PREFILL_BOOKS:
        # Prefer the provider-returned bet-slip link — the book's own share-to-slip URL
        # (FanDuel addToBetslip, DraftKings ?outcomes=, Caesars /bet/betslip?selectionIds=).
        # Substitute any {state} placeholder. DK also exposes a SID we fall back to as a
        # dksb:// scheme link if no https betslip link is present.
        if links.get("betslip"):
            return {"tier": "prefill", "url": _fill_placeholders(links["betslip"]),
                    "copy_text": copy_text}
        if book_key == "draftkings" and links.get("sid"):
            return {"tier": "prefill", "url": f"dksb://sb/addbet/{links['sid']}",
                    "copy_text": copy_text}

    if links.get("event"):
        # Event links can also carry a {state} placeholder (BetMGM/BetRivers) — substitute
        # so the link resolves to the event page instead of a broken host.
        return {"tier": "event", "url": _fill_placeholders(links["event"]),
                "copy_text": copy_text}

    return {"tier": "home", "url": HOME_URL.get(book_key), "copy_text": copy_text}


def extract_links(ev_data, book_key, market, side, point, player):
    """Walk an Odds API single-event payload (includeLinks/includeSids) for the
    one outcome matching this pick; return {betslip, sid, event}. Mirrors
    core.line_quotes' matching (canon market, side name, player desc, point)."""
    target = None if market == "h2h" else round(point or 0.0, 1)
    out = {"betslip": None, "sid": None, "event": None}
    for b in ev_data.get("bookmakers", []):
        if b.get("key") != book_key:
            continue
        out["event"] = b.get("link") or out["event"]
        for m in b.get("markets", []):
            if core._canon(m["key"]) != market:
                continue
            out["event"] = m.get("link") or out["event"]
            for o in m.get("outcomes", []):
                if o["name"] != side or (o.get("description") or None) != (player or None):
                    continue
                pt = None if market == "h2h" else round(o.get("point", 0.0), 1)
                if pt != target:
                    continue
                if o.get("link"):
                    out["betslip"] = o["link"]
                if o.get("sid"):
                    out["sid"] = o["sid"]
    return out
