"""Stateless ladder consistency framework — detection only, no I/O.

Detects monotonicity / sum-to-1 violations on multi-leg Kalshi ladders
(cumulative threshold ladders like KXBTC, bucketed exclusive-outcome
ladders like KXFEDDECISION) and produces ArbTrade / FadeTrade signals
for sub-strategies to execute.

Spec: docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md
"""
from dataclasses import dataclass
from typing import Literal


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
