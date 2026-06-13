"""Parse a sports-betting bet-slip screenshot into a structured draft via Claude vision.

Off-feed bets (placed outside the High picks feed) never reach app_bet via one-tap
capture. This module lets the app snap the slip and get back a BetIn-shaped draft the
user confirms — closing the measurement gap without sportsbook credentials.

The Anthropic client is injectable (client=...) so tests run offline. The default client
reads ANTHROPIC_API_KEY from the environment; import is lazy so a missing key/package only
matters at real call time, not at import time."""

import json

MODEL = "claude-opus-4-8"   # skill-mandated default; vision-capable, ~cents/scan at personal volume

SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sport": {"type": ["string", "null"]},
        "home": {"type": ["string", "null"]},
        "away": {"type": ["string", "null"]},
        "market": {"type": ["string", "null"]},
        "side": {"type": ["string", "null"]},
        "point": {"type": ["number", "null"]},
        "player": {"type": ["string", "null"]},
        "book": {"type": ["string", "null"]},
        "fill_odds": {"type": ["number", "null"]},
        "stake": {"type": ["number", "null"]},
        "label": {"type": ["string", "null"]},
        "notes": {"type": "string"},
    },
    "required": ["sport", "home", "away", "market", "side", "point",
                 "player", "book", "fill_odds", "stake", "label", "notes"],
    "additionalProperties": False,
}

_PROMPT = (
    "This is a screenshot of a single-leg sports bet slip from a US sportsbook.\n"
    "Extract the bet into the schema. Conventions:\n"
    "- sport: an Odds-API-style key when identifiable (baseball_mlb, basketball_wnba, "
    "basketball_nba, icehockey_nhl, baseball_npb), else null.\n"
    "- market: an Odds-API market key (h2h, spread, total, batter_total_bases, "
    "batter_home_runs, pitcher_strikeouts, pitcher_outs), else null.\n"
    "- side: the outcome exactly as a book lists it (team name, Over, Under, Home, Away).\n"
    "- point: the line as a number (1.5, -5.5, 8.5) or null for moneyline.\n"
    "- fill_odds: DECIMAL odds (convert American: +150 -> 2.50, -170 -> 1.59).\n"
    "- stake: the wager amount in dollars (number only), or null if not shown.\n"
    "- home/away: the two team abbreviations or names if shown, else null.\n"
    "- label: a short human-readable description (e.g. 'Nico Hoerner TB Over 1.5').\n"
    "- notes: anything ambiguous or a second leg you are ignoring.\n"
    "Use null for any field you cannot read confidently. Do not guess odds."
)


def _default_client():
    import anthropic
    return anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from the environment


def parse_bet_slip(image_b64, media_type="image/jpeg", client=None):
    """Return a dict matching SCAN_SCHEMA parsed from a base64 bet-slip image."""
    client = client or _default_client()
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": SCAN_SCHEMA}},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                {"type": "text", "text": _PROMPT},
            ],
        }],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    return json.loads(text)
