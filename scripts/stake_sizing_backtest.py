"""Stake sizing by edge tier — does larger edge imply higher EV/$1?

If yes, stake-by-edge improves absolute PnL by concentrating $ on higher-EV trades.
If no, flat sizing is equally efficient.

Tests on MLB v2.1 cohort (cached rows) — bucketed by era_diff (edge proxy).
"""
import json, os

CACHE = "/tmp/mlb_combo_rows.json"
FEE = 0.07

with open(CACHE) as f:
    rows = json.load(f)
print(f"Loaded base rows: {len(rows)}")

# Filter to v2.1 cohort
v21 = [r for r in rows if r["sig"].get("ERA_DIFF_2") and r["sig"].get("BOOKS_DEEP")]
print(f"v2.1 cohort: n={len(v21)}\n")

def stat(sub, label):
    if not sub: print(f"  {label:<22} n=0"); return
    n = len(sub)
    w = sum(1 for r in sub if r["won"])
    p = sum(r["pnl"] for r in sub)
    print(f"  {label:<22} n={n:>3}  WR={100*w/n:>5.1f}%  EV={p/n:+7.3f}  PnL_flat={p:+7.2f}")

# Use era_diff as edge proxy (only signal we have continuous values for)
print("=== EV by era_diff band (v2.1 cohort) ===")
for lo, hi in [(2.0,2.5),(2.5,3.0),(3.0,4.0),(4.0,6.0)]:
    sub = [r for r in v21 if lo <= r["era_diff"] < hi]
    stat(sub, f"era_diff {lo:.1f}-{hi:.1f}")

# Now compute weighted P&L under different sizing schemes
print("\n=== Sizing comparison (total $stake fixed at n × $4) ===")
def total_pnl_with_sizes(sizes):
    return sum(size * (r["pnl"]) for r, size in zip(v21, sizes))

base_size = 4.0
n = len(v21)
total_capital = n * base_size

# Scheme A: flat
flat_sizes = [base_size] * n
pnl_flat = total_pnl_with_sizes(flat_sizes)

# Scheme B: linear by era_diff (clip 2.0-5.0 → 1×-3×)
def tier_size_linear(r, base=base_size, low=2.0, high=5.0, ratio_low=1.0, ratio_high=3.0):
    diff = max(low, min(high, r["era_diff"]))
    ratio = ratio_low + (ratio_high - ratio_low) * (diff - low) / (high - low)
    return base * ratio
lin_sizes = [tier_size_linear(r) for r in v21]
# Normalize so total = total_capital (apples-to-apples comparison)
sum_lin = sum(lin_sizes)
lin_sizes = [s * total_capital / sum_lin for s in lin_sizes]
pnl_lin = total_pnl_with_sizes(lin_sizes)

# Scheme C: step tiers (era_diff <2.5: 1×, 2.5-3.5: 1.5×, 3.5+: 2×)
def tier_size_step(r, base=base_size):
    if r["era_diff"] >= 3.5: return base * 2.0
    if r["era_diff"] >= 2.5: return base * 1.5
    return base
step_sizes = [tier_size_step(r) for r in v21]
sum_step = sum(step_sizes)
step_sizes = [s * total_capital / sum_step for s in step_sizes]
pnl_step = total_pnl_with_sizes(step_sizes)

# Scheme D: aggressive (era_diff <2.5: 0×, 2.5-3.5: 1×, 3.5+: 3×) — concentration
def tier_size_aggro(r, base=base_size):
    if r["era_diff"] < 2.5: return 0
    if r["era_diff"] < 3.5: return base * 1.0
    return base * 3.0
aggro_sizes = [tier_size_aggro(r) for r in v21]
sum_aggro = sum(aggro_sizes)
if sum_aggro > 0:
    aggro_sizes = [s * total_capital / sum_aggro for s in aggro_sizes]
pnl_aggro = total_pnl_with_sizes(aggro_sizes)

print(f"  total capital: ${total_capital:.0f} (n={n} × $4 base)")
print(f"  FLAT $4    : total PnL = ${pnl_flat:>7.2f}  EV/$1 = {pnl_flat/total_capital:+.3f}")
print(f"  LINEAR 1-3x: total PnL = ${pnl_lin:>7.2f}  EV/$1 = {pnl_lin/total_capital:+.3f}")
print(f"  STEP tiers : total PnL = ${pnl_step:>7.2f}  EV/$1 = {pnl_step/total_capital:+.3f}")
print(f"  AGGRO (skip<2.5, 3x>=3.5): total PnL = ${pnl_aggro:>7.2f}  EV/$1 = {pnl_aggro/total_capital:+.3f}")

# Risk-adjusted: how do scheme variances compare?
import statistics as st
def variance_under_sizing(sizes):
    pnls = [s * r["pnl"] for r, s in zip(v21, sizes)]
    return st.stdev(pnls) if len(pnls) >= 2 else 0

print("\n=== Risk (per-trade PnL stdev) ===")
print(f"  FLAT    stdev: ${variance_under_sizing(flat_sizes):.2f}")
print(f"  LINEAR  stdev: ${variance_under_sizing(lin_sizes):.2f}")
print(f"  STEP    stdev: ${variance_under_sizing(step_sizes):.2f}")
print(f"  AGGRO   stdev: ${variance_under_sizing(aggro_sizes):.2f}")

# Sharpe-like
def sharpe(sizes):
    pnls = [s * r["pnl"] for r, s in zip(v21, sizes)]
    m = sum(pnls) / len(pnls)
    s = st.stdev(pnls) if len(pnls) >= 2 else 1
    return m / s if s > 0 else 0

print("\n=== Sharpe-like (mean / stdev) ===")
print(f"  FLAT    : {sharpe(flat_sizes):+.3f}")
print(f"  LINEAR  : {sharpe(lin_sizes):+.3f}")
print(f"  STEP    : {sharpe(step_sizes):+.3f}")
print(f"  AGGRO   : {sharpe(aggro_sizes):+.3f}")
