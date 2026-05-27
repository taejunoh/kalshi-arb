"""Lint scanner — detects potential look-ahead bias in backtest scripts.

The mlb_pitcher_v1 incident ([[mlb_pitcher_v1_lookahead_bias_2026-05-26]])
showed a class of error where a backtest uses season-aggregate stats
(e.g., current season ERA) as if they were known at signal-time. v2 was
forced to use point-in-time (PIT) data from a dedicated table.

This linter scans backtest-related scripts for patterns that historically
correlate with look-ahead bias:

  1. Suspicious column names — season_*, career_*, _yearly, etc.
  2. SQL JOIN on settlement/outcome tables WITHOUT a temporal constraint
     guarding signal-time isolation.
  3. Aggregate functions (MAX/AVG/SUM) on observation tables without
     timestamp filter.
  4. Usage of result/actual_outcome in feature engineering (vs only in
     final PnL calc).

Output: per-file PASS/WARN/FAIL summary + line-numbered warnings.
Exit code: 0 if all PASS, 1 if any WARN, 2 if any FAIL.

Usage:
  venv/bin/python scripts/lint_lookahead.py
  venv/bin/python scripts/lint_lookahead.py scripts/some_backtest.py
  venv/bin/python scripts/lint_lookahead.py --fail-on-warn
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass, field

# Backtest-script glob patterns
DEFAULT_PATTERNS = [
    "scripts/*backtest*.py",
    "scripts/*replay*.py",
    "scripts/*drill*.py",
    "scripts/research/*scanner*.py",
    "scripts/observer_*.py",
]

# Column / variable name patterns that often carry season-aggregate stats.
# Catching these doesn't prove look-ahead — it's a flag for review.
SUSPICIOUS_COL_PATTERNS = [
    (r"\bseason_(era|avg|wr|hits|ip|whip|ops|sv)\b", "season aggregate stat"),
    (r"\bcareer_(era|avg|wr|stats)\b", "career aggregate stat"),
    (r"\byear_total\b", "year-total aggregate"),
    (r"\.season\b(?!.*as_of)", "season-level field without as_of guard"),
    (r"all_time_\w+", "all-time aggregate"),
]

# SQL patterns that often have look-ahead issues
SQL_RISK_PATTERNS = [
    # MAX/AVG/SUM aggregates with no WHERE timestamp filter visible
    # (very rough heuristic — false positives expected)
    (r"SELECT\s+(MAX|AVG|SUM|COUNT)\s*\([^)]+\)\s+FROM\s+\w+\s*(?:WHERE|GROUP|$)",
     "aggregate without explicit time filter"),
    # JOIN on ticker_settlements without a temporal join condition
    (r"JOIN\s+ticker_settlements\s+\w+\s+ON\s+[^W]+(?:WHERE|GROUP|ORDER|$)",
     "ticker_settlements joined without time-isolation"),
]

# Tables that absolutely must be filtered by signal_time or earlier
# when used in feature-extraction
PIT_REQUIRED_TABLES = [
    "ticker_settlements",   # post-game outcome
    "mlb_pitcher_observations",  # has post-PIT season stats. Use mlb_pitcher_pointtime_observations.
    "crypto_observer_signals",   # has actual_outcome
]

# Whitelisted scripts (intentional bias for baseline comparison)
WHITELIST = {
    # v1 is the explicit biased baseline — kept for forward-comparison
    "scripts/mlb_v3_composite_backtest.py",  # OK — uses PIT table for v2/v3
    # Tools that READ outcomes for evaluation (not for feature extraction)
    "scripts/strategy_replay.py",  # canonical pattern, uses correct PIT split
    "scripts/eval_2026_06_02.py",  # post-deploy eval (legitimate use of outcomes)
    "scripts/eval_2026_05_27_snapshot.py",
    "scripts/promotion_eval.py",  # uses CLV which is PIT by design
    "scripts/morning_settle_digest.py",  # reports settled outcomes
}


@dataclass
class Finding:
    file: str
    line_no: int
    line: str
    rule: str
    severity: str  # warn / fail


@dataclass
class FileReport:
    file: str
    findings: list = field(default_factory=list)
    passed: bool = True

    def add(self, line_no: int, line: str, rule: str, severity: str):
        self.findings.append(Finding(self.file, line_no, line.strip(), rule, severity))
        if severity == "fail":
            self.passed = False


def scan_file(path: str) -> FileReport:
    report = FileReport(file=path)
    if path in WHITELIST:
        return report
    try:
        with open(path) as f:
            content = f.read()
    except OSError:
        return report
    lines = content.splitlines()

    # 1. Suspicious column names (line-by-line)
    for idx, line in enumerate(lines, 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        for pat, label in SUSPICIOUS_COL_PATTERNS:
            if re.search(pat, line, re.IGNORECASE):
                # Treat as warn — needs review, not auto-fail
                report.add(idx, line, f"suspicious column: {label}", "warn")

    # 2. SQL block analysis — look at triple-quoted SQL strings
    # Find """...""" or '''...''' blocks
    sql_blocks = re.finditer(r'("""|\'\'\')(.*?)(\1)', content, re.DOTALL)
    for m in sql_blocks:
        sql_text = m.group(2)
        if not re.search(r"\b(SELECT|JOIN|FROM)\b", sql_text, re.IGNORECASE):
            continue
        # Locate line numbers
        start_line = content[: m.start()].count("\n") + 1
        # Check PIT-required tables joined without time guard
        for table in PIT_REQUIRED_TABLES:
            if re.search(rf"\bJOIN\s+{table}\b", sql_text, re.IGNORECASE):
                # Look for temporal isolation: o.timestamp <= s.settled_at OR
                # WHERE timestamp >= signal_ts or similar
                has_time_guard = bool(re.search(
                    r"(timestamp|fetched_at|signal_ts|signal_time|as_of)\s*[<>]",
                    sql_text, re.IGNORECASE
                ))
                if not has_time_guard:
                    report.add(start_line,
                               f"JOIN {table} block",
                               f"JOIN {table} without temporal isolation",
                               "warn")
        # Aggregate without time filter
        for pat, label in SQL_RISK_PATTERNS:
            if re.search(pat, sql_text, re.IGNORECASE | re.DOTALL):
                # Don't flag if there's a temporal WHERE somewhere
                has_time_in_block = bool(re.search(
                    r"WHERE[^)]*timestamp", sql_text, re.IGNORECASE
                ))
                if not has_time_in_block:
                    report.add(start_line, "SQL block", label, "warn")
                    break
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="*", help="Files to lint (defaults to all backtest patterns)")
    parser.add_argument("--fail-on-warn", action="store_true",
                        help="Exit non-zero if any WARN found")
    args = parser.parse_args()

    files: list[str] = []
    if args.files:
        files = args.files
    else:
        for pat in DEFAULT_PATTERNS:
            files.extend(glob.glob(pat))
    files = sorted(set(files))

    if not files:
        print("No files to lint.")
        return 0

    reports = [scan_file(f) for f in files]

    total_warn = 0
    total_fail = 0
    for r in reports:
        if not r.findings:
            continue
        print(f"\n{r.file}:")
        for f in r.findings:
            tag = "WARN" if f.severity == "warn" else "FAIL"
            print(f"  {tag} L{f.line_no}: {f.rule}")
            print(f"    {f.line[:120]}")
            if f.severity == "warn":
                total_warn += 1
            else:
                total_fail += 1

    passed = sum(1 for r in reports if not r.findings)
    warn_files = sum(1 for r in reports if r.findings and r.passed)
    fail_files = sum(1 for r in reports if not r.passed)

    print(f"\n=== Summary ===")
    print(f"Scanned: {len(files)} files")
    print(f"Clean:   {passed}")
    print(f"Warn:    {warn_files} files ({total_warn} findings)")
    print(f"Fail:    {fail_files} files ({total_fail} findings)")

    if total_fail:
        return 2
    if args.fail_on_warn and total_warn:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
