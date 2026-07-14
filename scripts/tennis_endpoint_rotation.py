"""Daily check: do any mapped tennis endpoints return 0 events for 2+
consecutive days? If so, Telegram-alert with suggestion of currently-active
tennis endpoints (so we know what to rotate to).

State is persisted in data/tennis_rotation_state.json (one int counter per
endpoint slug, incrementing on zero-event days and resetting on non-zero).

Usage:
    venv/bin/python scripts/tennis_endpoint_rotation.py            # send Telegram
    venv/bin/python scripts/tennis_endpoint_rotation.py --dry-run  # stdout only
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from strategies.sports_scan_observer import SPORT_MAP


# Odds API key redaction. httpx logs the full request URL at INFO
# ("HTTP Request: GET https://...?apiKey=XXX ...") and embeds it in exception
# strings that this script logs via `{e}`, so without scrubbing the key lands
# in journald/stdout. Mask the query-param value anywhere it appears.
_API_KEY_RE = re.compile(r"(api[_-]?key=)[^&\s\"'}]+", re.IGNORECASE)


def _redact_api_key(text: str) -> str:
    """Replace apiKey=<value> query values with apiKey=***."""
    return _API_KEY_RE.sub(r"\1***", text)


class _RedactApiKeyFilter(logging.Filter):
    """Scrub apiKey=... from every log record before it reaches a handler.
    Formats msg%args first because httpx carries the URL in record.args."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        redacted = _redact_api_key(msg)
        if redacted != msg:
            record.msg = redacted
            record.args = ()
        return True


def _install_log_redaction() -> None:
    """Scrub apiKey=... from logs. Attach the filter at the LOGGER level for
    the two sources that carry the key — httpx (full request URL at INFO) and
    this script's own logger (exception messages via `{e}`) — because
    logger-level filters run in Logger.handle before propagation, independent
    of which handler basicConfig ends up installing on root. Root handlers get
    it too as a catch-all for any other library that echoes the URL."""
    filt = _RedactApiKeyFilter()
    for name in ("httpx", "httpcore", "tennis_endpoint_rotation"):
        logging.getLogger(name).addFilter(filt)
    for h in logging.getLogger().handlers:
        h.addFilter(filt)


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tennis_endpoint_rotation")
_install_log_redaction()

STATE_PATH = Path("data/tennis_rotation_state.json")
TENNIS_SERIES = frozenset({"KXATPMATCH", "KXWTAMATCH"})


def fetch_active_endpoints(api_key: str) -> list[dict]:
    """List every Odds API sport endpoint (active and inactive)."""
    r = httpx.get(
        "https://api.the-odds-api.com/v4/sports",
        params={"apiKey": api_key, "all": "true"},
        timeout=20,
    )
    r.raise_for_status()
    return r.json()


def _events_count_for_endpoint(api_key: str, slug: str) -> int:
    """Number of upcoming events on the slug (proxy for whether the
    tournament is currently running)."""
    try:
        r = httpx.get(
            f"https://api.the-odds-api.com/v4/sports/{slug}/events",
            params={"apiKey": api_key},
            timeout=20,
        )
        if r.status_code != 200:
            return 0
        return len(r.json() or [])
    except Exception as e:
        logger.warning(f"events_count {slug}: {e}")
        return 0


def _iter_slugs(value) -> tuple[str, ...]:
    """Tennis series now map to a tuple of concurrent tournament slugs; team
    sports still map to a single str. Normalise both shapes for iteration."""
    if isinstance(value, str):
        return (value,)
    return tuple(value)


def mapped_tennis_slugs(sport_map: dict) -> set[str]:
    """Every Odds API slug currently mapped under any tennis series."""
    out: set[str] = set()
    for series, value in sport_map.items():
        if series in TENNIS_SERIES:
            out.update(_iter_slugs(value))
    return out


def unmapped_active_tennis(active_endpoints: list[dict], sport_map: dict) -> list[dict]:
    """Active Odds API tennis endpoints that are NOT yet wired into SPORT_MAP.

    This is the other half of rotation: detect_stale_endpoints catches mapped
    slugs that went dead; this catches NEW tournaments the Odds API turned on
    that we haven't mapped (e.g. ATP grass appearing after the clay→grass
    handoff, when KXATPMATCH is temporarily empty). Returns the raw endpoint
    dicts (key/title/active), sorted by key.
    """
    mapped = mapped_tennis_slugs(sport_map)
    return sorted(
        (
            e for e in active_endpoints
            if e.get("key", "").startswith("tennis_")
            and e.get("active")
            and e.get("key") not in mapped
        ),
        key=lambda e: e.get("key", ""),
    )


def detect_stale_endpoints(
    state: dict[str, int],
    current_counts: dict[str, int],
    sport_map: dict,
    threshold: int = 2,
) -> tuple[dict[str, int], list[str]]:
    """Update consecutive-zero counters and return Kalshi series with one or
    more tennis slugs that have been zero-event for >= threshold days.

    Tennis series map to a tuple of concurrent tournament slugs; a series is
    listed once if any of its slugs trip the threshold today (per-slug detail
    is included in the alert body). Non-tennis series in sport_map are ignored.
    """
    new_state = dict(state)
    stale_series: list[str] = []
    for series, value in sport_map.items():
        if series not in TENNIS_SERIES:
            continue
        tripped_any = False
        for slug in _iter_slugs(value):
            count_today = current_counts.get(slug, 0)
            if count_today == 0:
                new_state[slug] = new_state.get(slug, 0) + 1
                if new_state[slug] >= threshold:
                    tripped_any = True
                    # Reset on alert to avoid repeated daily nag for the same stale slug
                    new_state[slug] = 0
            else:
                new_state[slug] = 0
        if tripped_any:
            stale_series.append(series)
    return new_state, stale_series


def _load_state() -> dict[str, int]:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            return {}
    return {}


def _save_state(state: dict[str, int]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state))


async def _send_telegram(text: str):
    cfg = Config()
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        print(text)
        return
    from monitoring.notifier import TelegramNotifier
    n = TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID)
    await n.send(text, msg_type="tennis_rotation")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    cfg = Config()
    api_key = cfg.ODDS_API_KEY
    if not api_key:
        logger.error("ODDS_API_KEY missing")
        sys.exit(1)

    # Today's events per mapped tennis slug. Tennis series are tuples — count
    # each slug separately so the rotation alert can name the specific stale
    # tournament (Italian Open done, Hamburg still running, etc.).
    current_counts: dict[str, int] = {}
    for series, value in SPORT_MAP.items():
        if series in TENNIS_SERIES:
            for slug in _iter_slugs(value):
                current_counts[slug] = _events_count_for_endpoint(api_key, slug)
                logger.info(f"{series} -> {slug}: {current_counts[slug]} events")

    state = _load_state()
    new_state, stale = detect_stale_endpoints(state, current_counts, SPORT_MAP)
    _save_state(new_state)

    # Always pull the live endpoint list (free, quota-exempt) — it drives both
    # the stale-slug suggestions AND unmapped-active discovery.
    try:
        active = fetch_active_endpoints(api_key)
    except Exception as e:
        logger.warning(f"fetch_active_endpoints failed: {e}")
        active = []
    active_tennis = [e for e in active
                     if e.get("key", "").startswith("tennis_") and e.get("active")]
    unmapped = unmapped_active_tennis(active, SPORT_MAP)

    if not stale and not unmapped:
        logger.info("No stale tennis endpoints; no unmapped-active endpoints.")
        return

    suggestions = "\n".join(
        f"  • {e['key']}  ({e.get('title','')})" for e in active_tennis[:10]
    ) or "  (none active right now)"

    lines = ["🎾 <b>Tennis endpoint rotation needed</b>", ""]
    if stale:
        lines.append("Stale (0 events for 2+ days):")
        for series in stale:
            slugs = _iter_slugs(SPORT_MAP[series])
            zero_slugs = [s for s in slugs if current_counts.get(s, 0) == 0]
            lines.append(f"  • {series} → " + ", ".join(zero_slugs or list(slugs)))
        lines.append("")
    if unmapped:
        lines.append("🆕 Active but NOT mapped (wire these into SPORT_MAP):")
        for e in unmapped:
            lines.append(f"  • {e['key']}  ({e.get('title','')})")
        lines.append("")
    lines += ["Currently active tennis endpoints:", suggestions]
    lines += ["", "<i>Action: edit TENNIS_ATP_SLUGS / TENNIS_WTA_SLUGS in "
              "strategies/sports_trader.py (SPORT_ODDS_KEYS), deploy, "
              "restart kalshi-bot.</i>"]
    text = "\n".join(lines)

    if args.dry_run:
        print(text)
    else:
        asyncio.run(_send_telegram(text))


if __name__ == "__main__":
    main()
