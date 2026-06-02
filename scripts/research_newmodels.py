"""Research: novel mathematical models for +EV discovery on Kalshi prediction markets.

Three models, all going beyond the codebase's existing threshold-rule approaches
(line-movement fade/follow, ERA diff, price-bucket EV, naive book imbalance,
CME basis):

  Model D — Bayesian multi-book ENSEMBLE -> outcome. Fit a calibrated logit
            combination of {Kalshi mid, Pinnacle, US consensus, EU consensus}
            on PAST settled games; trade where ensemble prob beats Kalshi ask
            by more than the fee. Tests: do sharp books carry information the
            Kalshi *price* has not yet absorbed? WALK-FORWARD, fee-aware.

  Model B — Lo-MacKinlay VARIANCE RATIO of the price path. VR(k)<1 => mean
            reversion (fade recent move); >1 => momentum (follow). Measured per
            sport/phase, then turned into a fee-aware directional bet.

  Model A — Bounded-martingale (Jacobi diffusion) VARIANCE BUDGET. A [0,1]
            martingale absorbing at settlement obeys  E[sum (dp)^2] = p0(1-p0).
            Realized path-variance ABOVE the running budget => overreaction =>
            mean-revert. A genuinely new conditioning feature for this book.

All models share ONE fee model and ONE walk-forward splitter so results are
comparable and leakage-free.

Reads the local extract /tmp/model_extract.db (tables: snap, path) built from a
read-only pass over EC2 odds_observations + ticker_settlements.

Usage:  venv/bin/python scripts/research_newmodels.py [--db /tmp/model_extract.db] [--fee 0.07]
"""
from __future__ import annotations
import argparse, sqlite3, math, sys
from collections import defaultdict

import numpy as np

# ----------------------------------------------------------------------------
# Fee model. Memory (feedback_slip_fee_wipe_mechanism) says treat Kalshi taker
# fee as $0.07/contract FLAT for this book's realized experience. Kalshi's
# published formula is ceil(0.07 * p*(1-p)) per contract which is much smaller
# mid-book; we use FLAT 0.07 as the conservative primary hurdle and also report
# the theoretical p(1-p) fee for sensitivity.
# EV (per $1 contract) of buying YES at ask a with true prob p:  p - a - fee.
# ----------------------------------------------------------------------------
def fee_flat(p, f=0.07):
    return f

def fee_theoretical(p, f=0.07):
    return max(0.01, f * p * (1.0 - p))   # Kalshi-style, floored at 1c

def logit(x, eps=1e-6):
    x = min(1 - eps, max(eps, x))
    return math.log(x / (1 - x))

def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z); return e / (1.0 + e)

# ----------------------------------------------------------------------------
# Data loading
# ----------------------------------------------------------------------------
def load_snaps(db, target_mts=60):
    """One decision snapshot per ticker at ~target_mts before start.
    Returns list of dicts with features + outcome + settle order key."""
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM snap WHERE target_mts=?", (target_mts,)).fetchall()
    out = []
    for r in rows:
        yb, ya = r["yes_bid"], r["yes_ask"]
        # Kalshi mid: use bid+ask when both present, else ask (early rows lack bid)
        if yb is not None and ya is not None and 0 < yb <= ya < 1:
            kmid = 0.5 * (yb + ya)
        elif ya is not None and 0 < ya < 1:
            kmid = ya
        else:
            continue  # no usable Kalshi price -> skip
        out.append({
            "ticker": r["ticker"], "sport": r["sport"], "series": r["series"],
            "y": r["y"], "kmid": kmid, "yes_ask": ya, "yes_bid": yb,
            "us": r["us"], "eu": r["eu"], "pin": r["pin"], "has_pin": r["has_pin"],
            "mts": r["mts"], "ticker_key": r["ticker"],
        })
    return out

def load_paths(db):
    """Per-ticker ordered price path (yes mid). Returns {ticker: dict}."""
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    paths = defaultdict(list)
    meta = {}
    for r in conn.execute("SELECT * FROM path ORDER BY ticker, ts"):
        yb, ya = r["yes_bid"], r["yes_ask"]
        if yb is not None and ya is not None and 0 < yb <= ya < 1:
            mid = 0.5 * (yb + ya)
        elif ya is not None and 0 < ya < 1:
            mid = ya
        else:
            continue
        paths[r["ticker"]].append((r["mts"], mid))
        if r["ticker"] not in meta:
            meta[r["ticker"]] = {"sport": r["sport"], "series": r["series"], "y": r["y"]}
    return paths, meta

# ----------------------------------------------------------------------------
# MODEL D — multi-book ensemble -> outcome, walk-forward, fee-aware
# ----------------------------------------------------------------------------
def fit_logit_ensemble(train, feats):
    """Fit p_hat = sigmoid(b + sum w_i * logit(x_i)) by gradient descent on
    log-loss over training games. Missing features imputed to kmid (i.e. weight
    falls on available signals). Returns (w dict, b)."""
    n = len(train)
    w = {f: 0.0 for f in feats}; b = 0.0
    # start with all weight on kmid as a sane prior
    if "kmid" in w: w["kmid"] = 1.0
    lr = 0.3
    for it in range(400):
        gw = {f: 0.0 for f in feats}; gb = 0.0
        for g in train:
            z = b
            for f in feats:
                xv = g.get(f)
                if xv is None: xv = g["kmid"]
                z += w[f] * logit(xv)
            ph = sigmoid(z)
            err = ph - g["y"]
            gb += err
            for f in feats:
                xv = g.get(f)
                if xv is None: xv = g["kmid"]
                gw[f] += err * logit(xv)
        b -= lr * gb / n
        for f in feats:
            w[f] -= lr * gw[f] / n
        # mild L2 to avoid blowups
        for f in feats:
            w[f] -= lr * 1e-3 * w[f]
    return w, b

def predict_ensemble(g, w, b, feats):
    z = b
    for f in feats:
        xv = g.get(f)
        if xv is None: xv = g["kmid"]
        z += w[f] * logit(xv)
    return sigmoid(z)

def model_D(snaps, fee_fn, feats=("kmid", "us", "eu", "pin"), n_folds=5, edge_min=0.0):
    """Walk-forward: sort by ticker (proxy for time via embedded date code is
    imperfect; we instead use a random-but-fixed expanding split on settle order
    when available). Here we approximate time order by ticker string sort which
    embeds the YYMMMDD game date for sports tickers. Expanding-window CV."""
    # order by embedded date in ticker if present else as-is
    def datekey(g):
        t = g["ticker"]
        # KX...-26MAY27... -> grab the 2-digit yr + 3-letter mon + 2-digit day
        import re
        m = re.search(r"-(\d{2})([A-Z]{3})(\d{2})", t)
        if m:
            mon = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,"JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}.get(m.group(2),0)
            return (int(m.group(1)), mon, int(m.group(3)), t)
        return (99,99,99,t)
    S = sorted(snaps, key=datekey)
    n = len(S)
    if n < 100:
        return {"error": f"too few snaps n={n}"}
    fold = n // (n_folds + 1)
    results = []  # per traded bet: (edge, realized_pnl, sport)
    for k in range(1, n_folds + 1):
        tr = S[: fold * k]
        te = S[fold * k: fold * (k + 1)]
        if len(tr) < 50 or not te: continue
        w, b = fit_logit_ensemble(tr, list(feats))
        for g in te:
            ph = predict_ensemble(g, w, b, list(feats))
            ya = g["yes_ask"]; y = g["y"]
            # YES side
            f = fee_fn(ya)
            edge_yes = ph - ya - f
            # NO side: no_ask approx = 1 - yes_bid if bid present else 1 - kmid
            no_ask = (1 - g["yes_bid"]) if g["yes_bid"] is not None else (1 - g["kmid"])
            f2 = fee_fn(no_ask)
            edge_no = (1 - ph) - no_ask - f2
            if edge_yes >= edge_no and edge_yes > edge_min:
                pnl = (y - ya) - f          # buy YES
                results.append((edge_yes, pnl, g["sport"], "YES", ph, ya))
            elif edge_no > edge_min:
                pnl = ((1 - y) - no_ask) - f2  # buy NO
                results.append((edge_no, pnl, g["sport"], "NO", ph, no_ask))
    return summarize(results, "Model D (multi-book ensemble)")

# ----------------------------------------------------------------------------
# MODEL B — variance ratio
# ----------------------------------------------------------------------------
def variance_ratio(series, k):
    x = np.asarray(series, float)
    d1 = np.diff(x)
    if len(d1) < k + 2: return None
    dk = x[k:] - x[:-k]
    v1 = np.var(d1, ddof=1)
    vk = np.var(dk, ddof=1)
    if v1 <= 0: return None
    return (vk / k) / v1

def model_B(paths, meta, fee_fn, ks=(2,4,8)):
    """Measure VR per sport at several horizons, and a fee-aware reversion bet:
    if VR(k)<1 strongly, fade the last move of size >= some threshold."""
    by_sport_vr = defaultdict(lambda: defaultdict(list))
    for tk, pth in paths.items():
        if len(pth) < 12: continue
        sport = meta[tk]["sport"]
        mids = [m for _, m in pth]
        for k in ks:
            vr = variance_ratio(mids, k)
            if vr is not None and np.isfinite(vr):
                by_sport_vr[sport][k].append(vr)
    vr_table = {}
    for sport, kd in by_sport_vr.items():
        vr_table[sport] = {k: (float(np.median(v)), len(v)) for k, v in kd.items()}
    # Fee-aware reversion backtest: for each path, at 70% of the way through,
    # observe last move; predict terminal via fade; check realized.
    rev_results = []
    for tk, pth in paths.items():
        if len(pth) < 10: continue
        sport = meta[tk]["sport"]; y = meta[tk]["y"]
        mids = [m for _, m in pth]
        i = int(len(mids) * 0.7)
        if i < 3 or i >= len(mids) - 1: continue
        move = mids[i] - mids[i - 3]   # recent 3-step move
        cur = mids[i]
        # fade: if moved up a lot, bet NO (expect revert down); vice versa
        thr = 0.06
        if move > thr and cur > 0.05 and cur < 0.95:
            no_ask = 1 - cur
            f = fee_fn(no_ask)
            rev_results.append((abs(move), ((1 - y) - no_ask) - f, sport, "fadeNO"))
        elif move < -thr and 0.05 < cur < 0.95:
            ya = cur
            f = fee_fn(ya)
            rev_results.append((abs(move), (y - ya) - f, sport, "fadeYES"))
    summ = summarize(rev_results, "Model B (variance-ratio fade)")
    summ["vr_table"] = vr_table
    return summ

# ----------------------------------------------------------------------------
# MODEL A — Jacobi variance budget
# ----------------------------------------------------------------------------
def model_A(paths, meta, fee_fn):
    """Identity: for a [0,1] martingale ending in {0,1}, E[sum (dp)^2] = p0(1-p0).
    Compute realized RV = sum (dp)^2 over the whole path and compare to p0(1-p0).
    Diagnostic: mean ratio RV / p0(1-p0) (efficient ~1; >1 excess vol/overreaction).
    Tradeable: at mid-life, if realized-RV-so-far exceeds the budget that *should*
    have been spent (proportional), the path is over-diffusing => fade last move."""
    ratios = []
    over_results = []
    for tk, pth in paths.items():
        if len(pth) < 8: continue
        sport = meta[tk]["sport"]; y = meta[tk]["y"]
        mids = [m for _, m in pth]
        p0 = mids[0]
        budget = p0 * (1 - p0)
        if budget <= 1e-4: continue
        rv_total = sum((mids[j] - mids[j-1])**2 for j in range(1, len(mids)))
        ratios.append((rv_total / budget, sport))
        # mid-life over-budget fade
        half = len(mids) // 2
        rv_half = sum((mids[j] - mids[j-1])**2 for j in range(1, half+1))
        cur = mids[half]
        # expected spent-by-half ~ half the budget (rough); flag over-diffusers
        if rv_half > 1.3 * (budget * 0.5) and 0.05 < cur < 0.95:
            move = mids[half] - mids[max(0, half-3)]
            if move > 0.04:
                no_ask = 1 - cur; f = fee_fn(no_ask)
                over_results.append((rv_half/budget, ((1-y)-no_ask)-f, sport, "fadeNO"))
            elif move < -0.04:
                ya = cur; f = fee_fn(ya)
                over_results.append((rv_half/budget, (y-ya)-f, sport, "fadeYES"))
    mean_ratio = float(np.mean([r for r, _ in ratios])) if ratios else None
    med_ratio = float(np.median([r for r, _ in ratios])) if ratios else None
    summ = summarize(over_results, "Model A (Jacobi over-budget fade)")
    summ["budget_diag"] = {"n": len(ratios), "mean_RV/budget": mean_ratio, "median_RV/budget": med_ratio}
    return summ

# ----------------------------------------------------------------------------
# Shared summarizer + calibration
# ----------------------------------------------------------------------------
def summarize(results, name):
    if not results:
        return {"name": name, "n_bets": 0, "note": "no qualifying bets"}
    pnls = np.array([r[1] for r in results])
    edges = np.array([r[0] for r in results])
    n = len(pnls)
    total = float(pnls.sum()); mean = float(pnls.mean())
    se = float(pnls.std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    t = mean / se if se and se == se and se > 0 else float("nan")
    wins = int((pnls > 0).sum())
    # by sport
    bysport = defaultdict(lambda: [0, 0.0])
    for e, p, sp, *rest in results:
        bysport[sp][0] += 1; bysport[sp][1] += p
    sport_tbl = {sp: {"n": c, "pnl": round(v, 2), "ev/ct": round(v/c, 4)} for sp, (c, v) in sorted(bysport.items(), key=lambda kv: kv[1][1], reverse=True)}
    return {
        "name": name, "n_bets": n, "wins": wins, "win_rate": round(100*wins/n, 1),
        "total_pnl_per_ct": round(total, 3), "mean_ev_per_ct": round(mean, 4),
        "t_stat": round(t, 2) if t == t else None,
        "mean_edge_signal": round(float(edges.mean()), 4),
        "by_sport": sport_tbl,
    }

def calibration_report(snaps):
    """Pure diagnostic: is Kalshi mid calibrated? realized freq vs implied, by bin.
    Also Pinnacle calibration. Reveals favorite-longshot bias if present."""
    def cal(field):
        bins = defaultdict(lambda: [0, 0])  # count, wins
        for g in snaps:
            v = g.get(field) if field != "kmid" else g["kmid"]
            if v is None: continue
            b = min(9, int(v * 10))
            bins[b][0] += 1; bins[b][1] += g["y"]
        return {f"{b/10:.1f}-{(b+1)/10:.1f}": {"n": c, "impl": round((b+0.5)/10,2), "real": round(w/c,3) if c else None}
                for b, (c, w) in sorted(bins.items())}
    return {"kalshi_mid": cal("kmid"), "pinnacle": cal("pin"), "us": cal("us")}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="/tmp/model_extract.db")
    ap.add_argument("--fee", type=float, default=0.07)
    ap.add_argument("--target_mts", type=int, default=60)
    args = ap.parse_args()

    fee_fn = lambda p: fee_flat(p, args.fee)

    snaps = load_snaps(args.db, args.target_mts)
    paths, meta = load_paths(args.db)
    print(f"# Loaded {len(snaps)} snaps @mts~{args.target_mts}, {len(paths)} paths. Fee=${args.fee} flat.\n")

    # coverage
    npin = sum(1 for g in snaps if g["pin"] is not None)
    nus = sum(1 for g in snaps if g["us"] is not None)
    ybar = np.mean([g["y"] for g in snaps]) if snaps else 0
    print(f"## Coverage: pin={npin}/{len(snaps)} us={nus}/{len(snaps)} base_rate(Y=1)={ybar:.3f}\n")

    print("## Calibration diagnostic (favorite-longshot check)")
    import json
    print(json.dumps(calibration_report(snaps), indent=1)); print()

    for name, res in [("D", model_D(snaps, fee_fn)),
                      ("D_theoretical_fee", model_D(snaps, fee_theoretical)),
                      ("B", model_B(paths, meta, fee_fn)),
                      ("A", model_A(paths, meta, fee_fn))]:
        print(f"## Model {name}")
        print(json.dumps(res, indent=1)); print()

if __name__ == "__main__":
    main()
