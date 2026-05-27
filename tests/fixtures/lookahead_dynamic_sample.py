"""Test fixture — synthetic dynamic look-ahead bias patterns.

Used by tests verifying ast_scan() in scripts/lint_lookahead.py. Each
function intentionally exhibits a pattern that should trigger a WARN.
Filename matches `*backtest*` glob.
"""


def dynamic_outcome_in_signal_backtest(df, actual_outcome, signal):
    # BAD: function uses actual_outcome AND signal — possible leakage
    feature = actual_outcome  # uses future outcome
    if signal > 0 and feature == "yes":
        return 1
    return 0


def pandas_shift_negative_backtest(df):
    # BAD: shift(-1) brings future row's value into current
    df["next_close"] = df["close"].shift(-1)
    return df


def pandas_iloc_future_backtest(df):
    # BAD: iloc with future index
    for i in range(len(df) - 1):
        feature = df.iloc[i + 1]  # access tomorrow's row from today's loop
        df.loc[i, "feature"] = feature["close"]
    return df
