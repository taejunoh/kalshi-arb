# scripts/softbook_deeplink.py
#!/usr/bin/env python3
"""Tiered sportsbook deep-link builder for the +EV companion app.

Reality (researched + adversarially verified, see the design spec): bet-slip
PREFILL is primary-source-confirmed only for FanDuel (provider-returned
addToBetslip universal link) and DraftKings (dksb://sb/addbet/<sid>), and is
best-effort even there. Every other book: open the event page / app and rely on
the copyable bet text. Links/SIDs come from The Odds API includeLinks/includeSids;
we never hand-build hosts (state drift). Always emit copy_text so a failed
prefill degrades to manual entry, never a dead end.

Returned dict: {"tier": "prefill"|"event"|"home", "url": str|None, "copy_text": str}
"""
import softbook_ev_core as core

# Only these two get the "prefill" tier; for them we trust a provider betslip
# link / SID. (BetMGM/Caesars/BetRivers/Fanatics/theScore/BallyBet: no public
# prefill confirmed -> event/home only.)
PREFILL_BOOKS = {"fanduel", "draftkings"}

# Universal-link / app home fallbacks (open the app to its home when no
# event/betslip link is available). None => copy-only.
HOME_URL = {
    "fanduel": "https://sportsbook.fanduel.com",
    "draftkings": "https://sportsbook.draftkings.com",
    "betmgm": "https://sports.nj.betmgm.com",
    "williamhill_us": "https://www.williamhill.us",
    "betrivers": "https://www.betrivers.com",
    "fanatics": "https://sportsbook.fanatics.com",
    "espnbet": "https://espnbet.com",
    "ballybet": None,
}


def _odds(px):
    try:
        return f"{float(px):.2f}"
    except Exception:
        return "?"


def build(book_key, links, pick):
    """book_key: Odds API key (e.g. 'fanduel'). links: {betslip?, sid?, event?}
    extracted for THIS outcome. pick: dict with label/odds/book(label)."""
    label = core.NY_LEGAL.get(book_key, book_key)
    copy_text = f"{label} · {pick['label']} @ {_odds(pick.get('odds'))}"
    url, tier = None, "home"

    if book_key in PREFILL_BOOKS:
        # Prefer the provider-returned bet-slip link (https universal link) for BOTH
        # books — it is the book's own share-to-slip URL (FanDuel addToBetslip,
        # DraftKings ?outcomes=). DK also exposes a SID we fall back to as a dksb://
        # scheme link if no https betslip link is present.
        if links.get("betslip"):
            return {"tier": "prefill", "url": links["betslip"], "copy_text": copy_text}
        if book_key == "draftkings" and links.get("sid"):
            return {"tier": "prefill", "url": f"dksb://sb/addbet/{links['sid']}",
                    "copy_text": copy_text}

    if links.get("event"):
        return {"tier": "event", "url": links["event"], "copy_text": copy_text}

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
