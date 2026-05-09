"""Stateless ladder consistency framework — detection only, no I/O.

Detects monotonicity / sum-to-1 violations on multi-leg Kalshi ladders
(cumulative threshold ladders like KXBTC, bucketed exclusive-outcome
ladders like KXFEDDECISION) and produces ArbTrade / FadeTrade signals
for sub-strategies to execute.

Spec: docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md
"""
from dataclasses import dataclass
from typing import Callable, Literal


@dataclass(frozen=True)
class LadderLeg:
    ticker: str
    threshold: float
    yes_ask: float
    yes_bid: float
    no_ask: float
    no_bid: float
    yes_depth: float
    no_depth: float


@dataclass(frozen=True)
class LadderGroup:
    series: str
    event_key: str
    ladder_type: Literal["cumulative", "bucketed"]
    direction: Literal["asc", "desc"]
    legs: tuple[LadderLeg, ...]


@dataclass(frozen=True)
class ArbLeg:
    ticker: str
    side: Literal["yes", "no"]
    size_usdc: float
    price: float


@dataclass(frozen=True)
class ArbTrade:
    group_key: str
    legs: tuple[ArbLeg, ...]
    expected_edge_usdc: float


@dataclass(frozen=True)
class FadeTrade:
    group_key: str
    ticker: str
    side: Literal["yes", "no"]
    size_usdc: float
    price: float
    inversion_amount: float


def group_legs_into_ladders(
    markets: list[dict],
    suffix_parser: Callable[[str], float | None],
    ladder_type: Literal["cumulative", "bucketed"],
    event_key_extractor: Callable[[dict], str],
    direction: Literal["asc", "desc"] = "asc",
) -> list[LadderGroup]:
    """Group market dicts into LadderGroups by event_key.

    `markets`: dicts with 'ticker' and float-dollar prices in keys
    'yes_ask', 'yes_bid', 'no_ask', 'no_bid', plus 'yes_depth', 'no_depth'.
    Caller (sub-strategy) is responsible for normalizing Kalshi's cents-int
    fields to dollar floats before calling.

    `suffix_parser` receives only the trailing segment after the last '-'
    in the ticker (e.g. for 'KXBTC-26MAY09-1500-B70250' it receives 'B70250').
    Return None to skip an unparseable leg.
    """
    by_event: dict[str, list[LadderLeg]] = {}
    for m in markets:
        ticker = m.get("ticker", "")
        if not ticker:
            continue
        suffix = ticker.rsplit("-", 1)[-1]
        threshold = suffix_parser(suffix)
        if threshold is None:
            continue
        event_key = event_key_extractor(m)
        if not event_key:
            continue
        leg = LadderLeg(
            ticker=ticker,
            threshold=threshold,
            yes_ask=float(m.get("yes_ask") or 0.0),
            yes_bid=float(m.get("yes_bid") or 0.0),
            no_ask=float(m.get("no_ask") or 0.0),
            no_bid=float(m.get("no_bid") or 0.0),
            yes_depth=float(m.get("yes_depth") or 0.0),
            no_depth=float(m.get("no_depth") or 0.0),
        )
        by_event.setdefault(event_key, []).append(leg)

    out: list[LadderGroup] = []
    for event_key, legs in by_event.items():
        sorted_legs = sorted(legs, key=lambda l: l.threshold,
                             reverse=(direction == "desc"))
        series = sorted_legs[0].ticker.split("-")[0]
        out.append(LadderGroup(
            series=series, event_key=event_key, ladder_type=ladder_type,
            direction=direction, legs=tuple(sorted_legs),
        ))
    return out


def find_arb_opportunities(
    group: LadderGroup,
    fee: float,
    min_edge_usdc: float,
    min_depth_usdc: float,
    max_position_usdc: float,
) -> list[ArbTrade]:
    """Detect adjacent inversion (cumulative) or sum-to-1 violation (bucketed)
    that produces a riskless arb above the fee budget. Sub-strategy is
    responsible for de-duplicating multiple opportunities on the same group.
    """
    trades: list[ArbTrade] = []
    if group.ladder_type == "cumulative":
        legs = group.legs  # already sorted by group_legs_into_ladders
        for i in range(len(legs) - 1):
            t1, t2 = legs[i], legs[i + 1]
            # Inversion: easier threshold's yes_ask < stricter threshold's yes_bid
            inv = t2.yes_bid - t1.yes_ask
            if inv <= 0:
                continue
            # Worst-case payoff = $1; cost = yes_ask(T1) + no_ask(T2)
            cost = t1.yes_ask + t2.no_ask
            edge = 1.0 - cost - 2 * fee
            if edge < min_edge_usdc:
                continue
            if t1.yes_depth < min_depth_usdc or t2.no_depth < min_depth_usdc:
                continue
            half = max_position_usdc / 2.0
            trades.append(ArbTrade(
                group_key=f"{group.series}-{group.event_key}",
                legs=(
                    ArbLeg(ticker=t1.ticker, side="yes",
                           size_usdc=half, price=t1.yes_ask),
                    ArbLeg(ticker=t2.ticker, side="no",
                           size_usdc=half, price=t2.no_ask),
                ),
                expected_edge_usdc=round(edge * max_position_usdc, 4),
            ))
    elif group.ladder_type == "bucketed":
        # implemented in Task 4
        pass
    return trades
