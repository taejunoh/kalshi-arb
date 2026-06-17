#!/usr/bin/env python3
"""Verify the soft-book +EV edge: closing-line value (CLV) — FULL markets.

For each recorded +EV bet (unique event+market+side+line), compare the OPENING
snapshot's Pinnacle fair prob to the LATEST (~closing) snapshot's fair prob on
that same bet. CLV = closing_fair - opening_fair: positive = the sharp line moved
TOWARD our bet after we took the soft price = genuinely +EV (we beat the close).
Diligent +EV bets show systematically positive CLV; ~0 = stale-line noise.

The headline cut is the FULL recorded population (diagnostic). The decision-relevant
cut is FIRST-ENTRY GAME ONLY (core.is_bet) — the actual single-source bet rule — so
that the first REAL CLV reads on the population we actually bet, not the props /
late-crossers we skip (which dominate the recorded volume and dilute the signal).
"""
import sqlite3, os, statistics, random
from collections import defaultdict

import softbook_ev_core as core

DB = os.getenv("SOFTBOOK_EV_DB", "/home/ubuntu/kalshi-arb/data/softbook_ev.db")


def _band(px):
    if px < 1.50:
        return "a) <1.50 (heavy fav)"
    if px <= 4.00:
        return "b) 1.50-4.00 (BET band)"
    return "c) >4.00 (longshot)"


def first_entry_map(rows):
    """(event_id, market, player) -> frozenset of the sides that were High at that key's
    EARLIEST recorded snapshot. A pick is first-entry iff ITS side is in the set.

    Side-aware mirror of core.market_first_entry_sides (the DB single source for the
    Telegram tag / app /picks flag / push): the OPPOSITE side that crosses into High only
    after the line moved is a LATE-CROSSER, not a first-entry. core.is_bet(first_entry,
    market) then drops props + late-crossers. Deterministic on same-scan multi-side ties
    (set membership at the min recorded_at, not a nondeterministic single-row pick)."""
    open_ts = {}                                    # fk -> earliest recorded_at
    for eid, market, side, point, player, ts, fair, px, tier in rows:
        fk = (eid, market, player or "")
        if fk not in open_ts or ts < open_ts[fk]:
            open_ts[fk] = ts
    sides = {}                                       # fk -> {sides High at open_ts}
    for eid, market, side, point, player, ts, fair, px, tier in rows:
        fk = (eid, market, player or "")
        if ts == open_ts[fk] and tier == "High":
            sides.setdefault(fk, set()).add(side)
    return {fk: frozenset(s) for fk, s in sides.items()}


def measure(rows, close):
    """Pure CLV computation over recorded snapshots.

    rows = iterable of (event_id, market, side, point, player, recorded_at,
    pinnacle_fair_prob, best_soft_price, tier); order-independent.
    close = {(event_id, market, side, point|0, player|""): close_fair} (TRUE closes
    from softbook_close_capture; a bet with no entry falls back to its last in-band
    snapshot, which biases CLV UP). Returns a dict of CLV series, including the
    first-entry-game-only subset (bet_*) selected by core.is_bet."""
    rows = list(rows)
    g = defaultdict(list)
    for eid, market, side, point, player, ts, fair, px, tier in rows:
        g[(eid, market, side, point, player)].append((ts, fair, px, tier))
    fe = first_entry_map(rows)
    clvs, px_clv, mkt, tier_clv, n_true = [], [], defaultdict(list), defaultdict(list), 0
    bet_clvs, bet_true, bet_clvs_true = [], 0, []    # first-entry GAME only (core.is_bet)
    for k, track in g.items():
        track.sort()
        open_fair = track[0][1]
        ck = (k[0], k[1], k[2], k[3] or 0, k[4] or "")
        if ck in close:
            close_fair, is_true = close[ck], True
        elif len(track) >= 2:
            close_fair, is_true = track[-1][1], False
        else:
            continue                                # no close available -> unmeasurable
        n_true += is_true
        clv = close_fair - open_fair                # close fair - open fair
        clvs.append(clv); px_clv.append((track[0][2], clv)); mkt[k[1]].append(clv)
        tier_clv[track[0][3] or "?"].append(clv)    # by OPENING confidence tier
        if core.is_bet(k[2] in fe.get((k[0], k[1], k[4] or ""), frozenset()), k[1]):
            bet_clvs.append(clv); bet_true += is_true
            if is_true:
                bet_clvs_true.append(clv)            # honest cut: TRUE close, no UP bias
    return {"n_groups": len(g), "clvs": clvs, "n_true": n_true, "px_clv": px_clv,
            "mkt": mkt, "tier_clv": tier_clv, "bet_clvs": bet_clvs, "bet_true": bet_true,
            "bet_clvs_true": bet_clvs_true}


def _ci95(series, seed=7, n_boot=3000):
    rnd = random.Random(seed)
    boots = sorted(statistics.mean([series[rnd.randrange(len(series))] for _ in series])
                   for _ in range(n_boot))
    return boots[int(0.025 * len(boots))], boots[int(0.975 * len(boots))]


def _verdict(lo, hi):
    if lo > 0:
        return "<<< REAL edge (CLV>0)"
    if hi < 0:
        return "LOSES to close (-EV)"
    return "spans 0 / stale-noise"


def main():
    db = sqlite3.connect(DB)
    try:
        rows = db.execute("""SELECT event_id, market, side, point, player, recorded_at,
            pinnacle_fair_prob, best_soft_price, tier FROM softbook_ev
            ORDER BY recorded_at""").fetchall()        # first_entry_map is order-independent
    except Exception as e:
        print("read err (schema?):", e); return
    # TRUE closing fairs (softbook_close_capture: Pinnacle at commence, any band).
    # Falling back to the last in-band snapshot biases CLV UP (corrected lines were
    # truncated out), so prefer the true close whenever it exists.
    close = {}
    try:
        for eid, m, s, p, pl, cf in db.execute(
                "SELECT event_id, market, side, point, player, close_fair FROM closing_line"):
            close[(eid, m, s, p or 0, pl or "")] = cf
    except sqlite3.OperationalError:
        pass                                          # capturer not deployed yet
    r = measure(rows, close)
    clvs, n_true = r["clvs"], r["n_true"]
    print(f"# {r['n_groups']} unique +EV bets recorded; {len(clvs)} CLV-measurable "
          f"({n_true} vs TRUE close, {len(clvs)-n_true} fallback to last in-band = biased UP)")

    # --- THE bet rule: FIRST-ENTRY GAME ONLY (core.is_bet) — decision-relevant cut ---
    bet, bet_true = r["bet_clvs"], r["bet_true"]
    print("\n=== FIRST-ENTRY GAME ONLY (core.is_bet — THE actual bet rule) ===")
    if bet:
        bpos = sum(1 for c in bet if c > 0)
        print(f"  n={len(bet)} ({bet_true} TRUE close, {len(bet)-bet_true} fallback=biased UP) "
              f"| mean CLV {statistics.mean(bet)*100:+.2f}pp | positive {bpos}/{len(bet)} "
              f"({100*bpos//len(bet)}%)")
        if len(bet) >= 15:
            blo, bhi = _ci95(bet)
            print(f"  bootstrap CI95 [{blo*100:+.2f}, {bhi*100:+.2f}]pp  {_verdict(blo, bhi)}")
        else:
            print(f"  (n<15 first-entry-game closes — too few for a CI; keep recording)")
        # The fallback rows (no captured close → last-in-band) bias UP; the TRUE-close
        # subset is the honest read. Watch this n grow toward the full bet n.
        bt = r["bet_clvs_true"]
        if bt:
            line = (f"  └ TRUE-close only: n={len(bt)} mean CLV "
                    f"{statistics.mean(bt)*100:+.2f}pp")
            if len(bt) >= 15:
                tlo, thi = _ci95(bt)
                line += f"  CI95 [{tlo*100:+.2f}, {thi*100:+.2f}]pp  {_verdict(tlo, thi)}"
            else:
                line += "  (n<15 — directional only; this is THE number once it fills)"
            print(line)
        else:
            print("  └ TRUE-close only: n=0 yet — first-entry games' closes still accruing")
        print("  ^ this is the population we ACTUALLY bet; the full breakdowns below are diagnostic.")
    else:
        print("  no first-entry game bets measurable yet — keep recording.")

    if len(clvs) < 15:
        print("\nnot enough closed opportunities yet — keep recording (need ~1-2 weeks).")
        return
    mean_clv = statistics.mean(clvs)
    pos = sum(1 for c in clvs if c > 0)
    print(f"\nCLV — FULL recorded population (diagnostic, incl. props + late-crossers):")
    print(f"  mean CLV = {mean_clv*100:+.2f}pp   | positive: {pos}/{len(clvs)} ({100*pos//len(clvs)}%)")
    lo, hi = _ci95(clvs)
    print(f"  bootstrap CI95 [{lo*100:+.2f}, {hi*100:+.2f}]pp  "
          f"{'<<< REAL edge (CLV>0)' if lo > 0 else 'spans 0 / stale-noise'}")
    print("\nCLV by confidence tier (does High actually beat the close more?):")
    for t in ("High", "Med", "Low", "?"):
        v = r["tier_clv"].get(t, [])
        if v:
            print(f"  {t:<5} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
    print("\nCLV by market:")
    for m in sorted(r["mkt"]):
        v = r["mkt"][m]
        print(f"  {m:<8} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
    bands = defaultdict(list)
    for px, clv in r["px_clv"]:
        bands[_band(px)].append(clv)
    print("\nCLV by odds band (validates the 1.50-4.00 cut):")
    for b in sorted(bands):
        v = bands[b]
        print(f"  {b:<24} n={len(v):<3} mean CLV {statistics.mean(v)*100:+.2f}pp")
    if lo > 0:
        print("\nVERDICT (full pop): CI95 lower bound > 0 — soft-book +EV edge is REAL & capturable.")
    elif hi < 0:
        print("\nVERDICT (full pop): CI95 entirely < 0 — recorded bets LOSE to the close (stale/-EV).")
    else:
        print("\nVERDICT (full pop): CI95 spans 0 — no detectable CLV edge yet (stale-line noise). Keep recording.")


if __name__ == "__main__":
    main()
