#!/usr/bin/env python3
"""Fetch FOMC press conference transcripts and build keyword frequency data.

Parses the last N transcripts from the Federal Reserve website and calculates
how often Powell mentions specific keywords. Output is used by the mentions
trader strategy to compare historical frequency vs Kalshi market prices.

Usage:
    python scripts/build_mention_data.py
"""
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import httpx

try:
    import fitz  # pymupdf
except ImportError:
    print("pymupdf required: pip install pymupdf")
    sys.exit(1)

# Transcript dates (most recent first)
TRANSCRIPT_DATES = [
    "20241218", "20241107", "20240918", "20240731",
    "20240612", "20240501", "20240320", "20240131",
    "20231213", "20231101", "20230920", "20230726",
]

BASE_URL = "https://www.federalreserve.gov/mediacenter/files/FOMCpresconf{}.pdf"

# Keywords to track — keys match the mentions trader TICKER_KEYWORD_MAP
KEYWORDS = {
    "ai": ["artificial intelligence", " ai "],
    "anchor": ["anchor", "anchored"],
    "balance sheet": ["balance sheet"],
    "bitcoin": ["bitcoin"],
    "central bank": ["central bank"],
    "consumer confidence": ["consumer confidence"],
    "credit": ["credit"],
    "dissent": ["dissent"],
    "dollar": ["dollar"],
    "dot plot": ["dot plot"],
    "egg": [" egg ", "eggs"],
    "expectation": ["expectation"],
    "gas": ["gasoline", "natural gas", " gas "],
    "gold": [" gold "],
    "good afternoon": ["good afternoon"],
    "goods inflation": ["goods inflation"],
    "iran": ["iran"],
    "kalshi": ["kalshi"],
    "layoff": ["layoff", "lay off", "layoffs"],
    "median": ["median"],
    "national debt": ["national debt"],
    "oil": [" oil "],
    "pandemic": ["pandemic"],
    "pardon": ["pardon"],
    "president": ["president"],
    "probability": ["probability"],
    "productivity": ["productivity"],
    "projection": ["projection"],
    "qe": ["quantitative easing", " qe "],
    "qt": ["quantitative tightening", " qt "],
    "recession": ["recession"],
    "renovation": ["renovation"],
    "replace": ["replace", "replacement"],
    "restrictive": ["restrictive"],
    "shock": ["shock"],
    "shutdown": ["shutdown", "shut down"],
    "slowdown": ["slowdown", "slow down", "slowing down"],
    "soft landing": ["soft landing"],
    "softening": ["softening"],
    "stagflation": ["stagflation"],
    "tariff": ["tariff"],
    "tax": [" tax ", "taxes", "taxation"],
    "trade war": ["trade war"],
    "trump": ["trump"],
    "uncertainty": ["uncertainty", "uncertain"],
    "unchanged": ["unchanged"],
    "volatility": ["volatility", "volatile"],
    "yield curve": ["yield curve"],
}

OUTPUT_PATH = Path(__file__).parent.parent / "data" / "mention_frequencies.json"


def fetch_and_parse(date: str, client: httpx.Client) -> str | None:
    url = BASE_URL.format(date)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        tmp = f"/tmp/fomc_{date}.pdf"
        with open(tmp, "wb") as f:
            f.write(resp.content)
        doc = fitz.open(tmp)
        text = " ".join(page.get_text() for page in doc).lower()
        doc.close()
        os.remove(tmp)
        return text
    except Exception as e:
        print(f"  FAIL {date}: {e}")
        return None


def main():
    client = httpx.Client(
        headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        follow_redirects=True,
        timeout=30,
    )

    counts = defaultdict(int)
    total = 0

    print(f"Fetching {len(TRANSCRIPT_DATES)} FOMC transcripts...")
    for date in TRANSCRIPT_DATES:
        text = fetch_and_parse(date, client)
        if text is None:
            continue
        total += 1
        for kw, patterns in KEYWORDS.items():
            if any(p in text for p in patterns):
                counts[kw] += 1
        print(f"  OK {date}: {len(text):,} chars")

    if total == 0:
        print("No transcripts parsed. Exiting.")
        sys.exit(1)

    freq = {}
    for kw in sorted(KEYWORDS.keys()):
        f = counts[kw] / total
        freq[kw] = round(f, 2)

    output = {"powell": freq}
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)

    print(f"\nParsed {total} transcripts, {len(freq)} keywords")
    print(f"Saved to {OUTPUT_PATH}")

    # Show high-frequency and low-frequency keywords
    print("\nAlways says (>80%):")
    for kw, f in sorted(freq.items(), key=lambda x: x[1], reverse=True):
        if f >= 0.80:
            print(f"  {kw:25s} {f:.0%}")
    print("\nRarely says (<20%):")
    for kw, f in sorted(freq.items(), key=lambda x: x[1]):
        if f < 0.20:
            print(f"  {kw:25s} {f:.0%}")


if __name__ == "__main__":
    main()
