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
import sys
from pathlib import Path

import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from strategies.sports_scan_observer import SPORT_MAP

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tennis_endpoint_rotation")

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


def detect_stale_endpoints(
    state: dict[str, int],
    current_counts: dict[str, int],
    sport_map: dict[str, str],
    threshold: int = 2,
) -> tuple[dict[str, int], list[str]]:
    """Update consecutive-zero counters and return list of Kalshi series
    whose endpoint has been zero for >= threshold days.

    Only tennis series (KXATPMATCH, KXWTAMATCH) are considered. Non-tennis
    series in sport_map are ignored.
    """
    new_state = dict(state)
    stale_series: list[str] = []
    for series, slug in sport_map.items():
        if series not in TENNIS_SERIES:
            continue
        count_today = current_counts.get(slug, 0)
        if count_today == 0:
            new_state[slug] = new_state.get(slug, 0) + 1
            if new_state[slug] >= threshold:
                stale_series.append(series)
                # Reset on alert to avoid repeated daily nag for the same stale endpoint
                new_state[slug] = 0
        else:
            new_state[slug] = 0
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

    # Today's events per mapped tennis slug
    current_counts: dict[str, int] = {}
    for series, slug in SPORT_MAP.items():
        if series in TENNIS_SERIES:
            current_counts[slug] = _events_count_for_endpoint(api_key, slug)
            logger.info(f"{series} -> {slug}: {current_counts[slug]} events")

    state = _load_state()
    new_state, stale = detect_stale_endpoints(state, current_counts, SPORT_MAP)
    _save_state(new_state)

    if not stale:
        logger.info("No stale tennis endpoints.")
        return

    # Fetch active alternatives for the alert
    active = fetch_active_endpoints(api_key)
    active_tennis = [e for e in active
                     if e.get("key", "").startswith("tennis_") and e.get("active")]
    suggestions = "\n".join(
        f"  • {e['key']}  ({e.get('title','')})" for e in active_tennis[:10]
    ) or "  (none active right now)"

    lines = [
        "🎾 <b>Tennis endpoint rotation needed</b>",
        "",
        "Stale (0 events for 2+ days):",
    ]
    for series in stale:
        lines.append(f"  • {series} → {SPORT_MAP[series]}")
    lines += ["", "Currently active tennis endpoints:", suggestions]
    lines += ["", "<i>Action: edit strategies/sports_scan_observer.py SPORT_MAP, "
              "deploy, restart kalshi-bot.</i>"]
    text = "\n".join(lines)

    if args.dry_run:
        print(text)
    else:
        asyncio.run(_send_telegram(text))


if __name__ == "__main__":
    main()
