# Ladder Consistency Arb (A1) — Design Spec

**날짜**: 2026-05-09
**상태**: 설계 (사용자 검토 대기)
**Sub-project of**: [Kalshi Non-Sports 수익원 포트폴리오](2026-05-09-kalshi-non-sports-strategy-portfolio.md)
**참조**:
- `strategies/sum_arb.py` (양면 arb 패턴 차용)
- `strategies/spread_scanner.py` (universe scan 패턴)
- `scripts/promotion_eval.py` (sub-strategy 별 promotion gate)
- 메모리: `feedback_always_paper_first.md`, `sport_promotion_thresholds.md`,
  `known_bugs.md` (V1 paper_filled 잔재), `adverse_selection_fix.md`

---

## Context

shutdown_fade V1 폐기 prereq 분석 결과 funding-deadline 카테고리에 직접
edge 없음을 확인 (`scripts/research/shutdown_fade_data/summary.txt`). 후속
"신박 다관점" 발산에서 4개 sub-project 후보 도출 (decomposition doc 참조).

A1 (Count-ladder 단조성 arb) 의 universe probe 결과 원래 thesis (shutdown
count) 보다 훨씬 큰 ladder universe 확인:

| series | open n | suffix | ladder type |
|---|---|---|---|
| KXBTC | 200 | B<price> | cumulative |
| KXETH | 165 | B<price> | cumulative |
| KXFEDDECISION | 70 | C<bps>, H<bps> | bucketed |
| KXTRUMPAPPROVAL\*, KX538APPROVE\* | 14+ | <int> | cumulative (asc/desc) |
| KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH | 4+ | T<int>, G<int> | cumulative |

이 발견으로 A1 이 B2 (crypto vs spot) 를 흡수 — crypto ladder 의 spot
anchor cross-check 가 B2 의 핵심 메커니즘이기 때문.

## Goals

1. Kalshi 의 ladder-구조 marketset 에서 monotonicity / sum-to-1 제약 위반
   감지 → 자동 진입.
2. Hybrid posture: depth + edge 충분 시 양면 arb (riskless), 부족 시 단일
   leg directional fade.
3. 5개 archetype (crypto / fed / approval / shutdown / +parlay deferred)
   하나의 framework 위에 4개 독립 sub-strategy 로 ship — 각자 paper/live 토글
   + circuit breaker + promotion 분리.
4. V1 의 13배 over-fill 사고 (in-memory state, no DB dedup) 재발 방지.

## Non-Goals

- KXMVE\* parlay/cross-event 마켓: 본 spec 범위 외. ladder type 이 cumulative/
  bucketed 어디에도 안 맞음 → 별도 sub-project (deferred).
- Cross-series arb (서로 다른 series 간 implied prob 일치성): 별도 sub-project.
- Live spot anchor 를 단독 fair-value model 로 사용 (B2 원안의 BS-style):
  현 spec 은 spot 을 *additional gate* 로만 사용. 본격 model 화는 후속.

## Architecture

### 모듈 layout

```
core/
  ladder_framework.py          [NEW]
    LadderLeg                       # frozen dataclass: ticker, threshold, prices, depth
    LadderGroup                     # frozen dataclass: series, event_key, type, sorted legs
    group_legs_into_ladders()       # markets dump + parser → groups
    find_arb_opportunities()        # group → list[ArbTrade]
    find_fade_opportunities()       # group → list[FadeTrade]

strategies/
  ladder_crypto.py             [NEW]   # KXBTC, KXETH
  ladder_fed.py                [NEW]   # KXFEDDECISION
  ladder_approval.py           [NEW]   # KXTRUMPAPPROVAL*, KX538APPROVE*
  ladder_shutdown.py           [NEW]   # KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH

scripts/research/
  ladder_violation_scanner.py  [NEW]   # Phase 0 prevalence 분석 (paper 이전 gate)

main.py                                # 4개 sub-strategy 등록 (parlay deferred)
docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md   # 본 doc
```

### 핵심 경계

- **Framework = 순수 stateless 함수/dataclass**. 상태 없음, side effect 없음.
  단위 테스트 쉽게.
- **Sub-strategy = universe filter + suffix parser + event_key extractor +
  scan loop**. 각자 독립 strategy_name 으로 OrderManager 등록.
- 각 sub-strategy 는 **독립 paper/live 토글 + circuit breaker +
  promotion_eval entry**. 한 archetype 망가져도 다른 sub-strategy 격리.
- MVE parlay 는 별도 sub-project. 본 spec 미포함.

## Ladder type 두 종류

### Cumulative

각 leg = "X ≥ threshold_i" 형 binary. threshold 단조 증가.

- 제약: `P(YES, T_i) ≥ P(YES, T_{i+1})` (더 엄격한 threshold 의 yes 확률 ≤)
- 위반: `yes_ask(T_i) < yes_bid(T_{i+1})`
- 예외: KXTRUMPAPPROVALBELOW 같은 "≤ X" 형은 reverse cumulative — sub-strategy
  가 direction flag (`asc`/`desc`) 를 inject

### Bucketed

각 leg = "결과 = bucket_i" 형 binary. 한 event 의 leg 들은 disjoint.

- 제약: `Σ P(YES, bucket_i) = 1`
- 위반:
  - `Σ yes_ask < 1 - fee_budget` → buy YES on all = guaranteed win
  - `Σ yes_bid > 1 + fee_budget` → buy NO on all = guaranteed win

## Trade builder math

### Cumulative 인접 inversion → arb path

Buy YES on cheaper-easier-threshold T_i (cost = `yes_ask_i`)
Buy NO on more-expensive-stricter T_{i+1} (cost = `no_ask_{i+1}` ≈ `1 - yes_bid_{i+1}`)

Outcome regions:
- BTC ≤ T_i: T1 NO win, T2 NO win → payoff $1 (NO leg only)
- T_i < BTC ≤ T_{i+1}: T1 YES win, T2 NO win → payoff $2
- BTC > T_{i+1}: T1 YES win, T2 YES win (NO loses) → payoff $1

worst-case payoff = $1.

**Riskless 조건**: `yes_ask_i + no_ask_{i+1} + 2·fee ≤ $1`
즉 inversion magnitude `(yes_bid_{i+1} - yes_ask_i) ≥ spread + 2·fee ≈ 15c`.

15c 이상 inversion 은 드물다 → fade fallback 이 핵심 수익 경로.

### Bucketed sum 위반 → arb path

`Σ yes_ask < 1 - fee_budget` 시 모든 leg yes 매수. Cost = Σ yes_ask + n·fee. payoff = $1 (한 leg 만 YES). 따라서 net = 1 - Σ yes_ask - n·fee.

n leg 합산 fee 부담 큼 → bucket 수가 작은 event 만 (≤ 5 leg) arb path 적합.

### Hybrid posture decision tree

**Cumulative (asc/desc 모두)**:
```
for each adjacent pair (T_i, T_{i+1}) sorted by threshold:
  # asc: more-strict 가 더 비싸면 inversion. desc: 반대 방향 — sub-strategy 가 sort 순서 뒤집어 inject
  inv = yes_bid(T_{i+1}) - yes_ask(T_i)
  if inv <= 0: continue

  edge = inv - 2·fee
  if edge >= MIN_ARB_EDGE_USDC AND depth(T_i, yes) ≥ MIN_DEPTH AND depth(T_{i+1}, no) ≥ MIN_DEPTH:
    → ArbTrade: buy YES T_i + buy NO T_{i+1}
    continue
  if inv >= MIN_FADE_INVERSION:
    → FadeTrade: buy YES T_i (저평가된 leg, hold to settle/convergence)
```

**Bucketed**:
```
sum_yes_ask = Σ yes_ask(leg_i)
sum_yes_bid = Σ yes_bid(leg_i)
n = len(legs)

# Arb path: full-leg 매수
if sum_yes_ask + n·fee < 1 - MIN_ARB_EDGE_USDC:
  if all legs depth(yes) ≥ MIN_DEPTH:
    → ArbTrade: buy YES on all legs (cost = sum + fees, payoff = $1)
elif sum_yes_bid - n·fee > 1 + MIN_ARB_EDGE_USDC:
  if all legs depth(no) ≥ MIN_DEPTH:
    → ArbTrade: buy NO on all legs (cost = (n - sum_yes_bid) + fees, payoff = n - 1)

# Fade fallback (single leg 선택)
implied(leg_i) = 1 - sum_other(yes_mid)
deviation(leg_i) = implied(leg_i) - yes_ask(leg_i)
target = argmax(deviation)
if deviation(target) >= MIN_FADE_INVERSION:
  → FadeTrade: buy YES target (가장 implied 대비 저평가된 leg)
```

**Direction flag (desc)**: KXTRUMPAPPROVALBELOW 처럼 "≤ X" 형 cumulative 는
sub-strategy 가 `direction="desc"` 와 함께 leg 들을 threshold **내림차순** 으로
inject. framework 의 cumulative decision tree 는 sort 순서만 보고 동일 알고리즘
적용 (즉 framework 입장에선 always asc 로 처리).

### Fade exit rule

3 조건 OR:
1. **Convergence**: 반대 leg 의 가격 변화로 단조성 회복 → 즉시 close at market
2. **Settlement**: 마켓 자연 settle → 정상 path
3. **Time-stop**: 진입 후 24h 경과 + 미해결 → market close (loss cut)

## Framework interface

```python
# core/ladder_framework.py

@dataclass(frozen=True)
class LadderLeg:
    ticker: str
    threshold: float       # parsed from suffix; sub-strategy provides parser
    yes_ask: float
    yes_bid: float
    no_ask: float
    no_bid: float
    yes_depth: float
    no_depth: float

@dataclass(frozen=True)
class LadderGroup:
    series: str
    event_key: str          # FOMC date for fed; close_date for crypto
    ladder_type: Literal["cumulative", "bucketed"]
    direction: Literal["asc", "desc"] = "asc"  # for cumulative-below variants
    legs: list[LadderLeg]                       # sorted by threshold

@dataclass(frozen=True)
class ArbTrade:
    group_key: str
    legs: list[tuple[str, str, float, float]]  # (ticker, side, size_usdc, price)
    expected_edge_usdc: float

@dataclass(frozen=True)
class FadeTrade:
    group_key: str
    ticker: str
    side: str
    size_usdc: float
    price: float
    inversion_amount: float

# detection (sub-strategy filters universe + injects parsers)
def group_legs_into_ladders(
    markets: list[dict],
    suffix_parser: Callable[[str], float | None],
    ladder_type: Literal["cumulative", "bucketed"],
    event_key_extractor: Callable[[dict], str],
    direction: Literal["asc", "desc"] = "asc",
) -> list[LadderGroup]: ...

def find_arb_opportunities(
    group: LadderGroup,
    fee: float,
    min_edge_usdc: float,
    min_depth_usdc: float,
    max_position_usdc: float,
) -> list[ArbTrade]: ...

def find_fade_opportunities(
    group: LadderGroup,
    fee: float,
    min_inversion: float,
    max_size_usdc: float,
) -> list[FadeTrade]: ...
```

Sub-strategy 책임은 `suffix_parser`, `event_key_extractor`, `direction` 만 inject + scan loop + OrderManager 호출.

## Sub-strategy specifics

### ladder_crypto (KXBTC, KXETH)

| 항목 | 값 |
|---|---|
| Universe | series_ticker IN {KXBTC, KXETH} |
| Ladder type | cumulative, asc |
| Suffix parser | `B<int>` → price threshold (KXBTC: $100 grid, KXETH: $40) |
| Event key | ticker 의 close_date 부분 (e.g., `KXBTC-26MAY09-1500`) |
| 특이사항 | (1) 한 event 에 ~50 leg, ladder 가장 큼. (2) **Spot anchor cross-check** — Coinbase WS spot price 받아 ATM leg yes_ask 와 0.50 오차 >10c 이면 추가 fade signal 발생 (B2 흡수). (3) 변동성 커서 stale orderbook 위험 → ATM ±2 leg 만 고려, 그 외 skip. |
| Config 초안 | `MIN_ARB_EDGE_USDC=0.02`, `MIN_FADE_INVERSION=0.05`, `MIN_DEPTH_USDC=30`, `MAX_POSITION_PER_GROUP=50`, `STALE_ORDERBOOK_SEC=10` |

### ladder_fed (KXFEDDECISION)

| 항목 | 값 |
|---|---|
| Universe | series_ticker = KXFEDDECISION |
| Ladder type | bucketed |
| Suffix parser | `C<int>` → -int/100 (cut bps), `H<int>` → +int/100 (hold/hike) |
| Event key | FOMC meeting date (ticker prefix) |
| 특이사항 | (1) 8 회의/yr → trigger 가 idle 한 장기간 + FOMC 2주전 reprice window. (2) bucket 약 5-10/회의, sum_yes ≈ 1. (3) C1 (FOMC window) sub-project 와 시간 충돌 — 동일 ticker 시 ladder_fed 우선 dedup (dedup priority 명시). |
| Config 초안 | 동일 + `MIN_DEPTH_USDC=30` |

### ladder_approval (KXTRUMPAPPROVAL\*, KX538APPROVE\*)

| 항목 | 값 |
|---|---|
| Universe | KXTRUMPAPPROVALYEAR, KXTRUMPAPPROVALBELOW, KX538APPROVE\*, KX538APPROVEMAX, KX538APPROVEMIN, KX538APPROVEY |
| Ladder type | cumulative; APPROVALBELOW 는 desc |
| Suffix parser | bare integer; sub-strategy 가 series → direction flag mapping |
| Event key | series + 기간 (week/year) |
| 특이사항 | (1) Approval 천천히 움직임 → 위반 구조적, fade hold 길어도 OK. (2) volume 낮음 → fade fallback 비중 큼. (3) 538\* 시리즈 daily granularity 주의. |
| Config 초안 | `MIN_DEPTH_USDC=20` (낮은 volume), 나머지 동일 |

### ladder_shutdown (KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH)

| 항목 | 값 |
|---|---|
| Universe | KXNUMSHUTDOWNS, KXGOVTSHUTLENGTH |
| Ladder type | cumulative, asc |
| Suffix parser | `T<int>` 또는 `G<int>` → int |
| Event key | ticker prefix (e.g., `KXNUMSHUTDOWNS-27JAN01`) |
| 특이사항 | **V1 잔재 가드 필수** — DB 의 121건 paper_filled 잔재 (T2: 59, T3: 7) 와 dedup. `core/order_manager.py:495` 의 `_traded_tickers` 패턴 + DB has_existing_trade (cfc1a46 fix) 두 가드 모두 적용. Universe 매우 thin → 대부분 idle. |
| Config 초안 | 동일 |

## Risk gates (4 sub-strategy 공통)

1. **Circuit breaker** per strategy_name (`OrderManager.is_circuit_breaker_active(strategy=...)`).
2. **Per-event lock** — group 단위 lock 후 동일 `event_key` 재진입 차단 (sum_arb `lock_market` 자연 확장 — group 단위로).
3. **DB has_existing_trade guard** — 진입 전 동일 (ticker, strategy_name) open trade 검사 (cfc1a46 fix).
4. **Max position per group** — `MAX_POSITION_PER_GROUP=50 USDC` (paper 초기값).
5. **Daily loss cap** per strategy — 기존 OrderManager 통합.
6. **Stale orderbook guard** — 마지막 orderbook update N초 전이면 skip (특히 crypto, default 10s).

## Sizing

**Arb path** (riskless): 고정 size = `MAX_POSITION_PER_GROUP / n_legs` per leg. edge size 무관.

**Fade path** (directional): edge-scaled (v32 underdog 패턴):
```
fade_size = base_size × clamp(inversion / 0.05, 0.5, 2.0)
base_size = MAX_SINGLE_TRADE_PCT × bankroll = 2% × $1000 = $20
→ fade size = $10 ~ $40 per fade trigger
```

**Bankroll**: 코드베이스 기존 패턴 — shared bankroll + per-strategy circuit
breaker + per-strategy daily loss cap. 명시적 fund 분할 안 함.

## Validation roadmap

### Phase 0 — Prevalence research (paper 이전 필수 gate)

`scripts/research/ladder_violation_scanner.py` (NEW):
- 4 sub-strategy universe 모두 1분 cadence scan (sports observer 동일)
- ladder_framework 의 detect 함수 호출만, 실제 trade 안 침
- 감지된 위반은 `ladder_violations` table 에 logging
- **2주간 운영 후 분석**: 일별 위반 빈도, 평균 inversion, depth 분포, 가상 ROI

**Phase 0 통과 기준** (sub-strategy 별):
- 일일 평균 위반 ≥ 1건 (arb + fade 합산)
- 가상 ROI (after fee, after spread) ≥ +1% (arb), ≥ +3% (fade)
- 미달 sub-strategy 는 paper 보류 (kill 또는 wait)

### Phase 1 — Paper deployment

순서: crypto → approval → fed → shutdown (universe 크기 + 검증성).

각 sub-strategy:
- `TRADING_MODE=paper` 또는 paper-twin 모드로 1-2주 운영
- 매일 Telegram daily summary
- DB `trades` 테이블 settle 후 누적 ROI 집계

### Phase 2 — Promotion

| 지표 | Arb path | Fade path |
|---|---|---|
| n_signals (settled) | ≥ 15 | ≥ 25 |
| ROI (after fee) | ≥ +1% | ≥ +5% |
| Worst single-trade loss | ≤ -$10 | ≤ -$20 |
| Top-of-book depth p50 | ≥ $30 (crypto) / $20 (정치) | 동일 |
| Drawdown peak-to-trough | ≤ 15% of strategy bankroll | 동일 |

`scripts/promotion_eval.py` 에 `ladder_*` 4 entry 추가.

## Testing

### Unit (`tests/test_ladder_framework.py`)

- `group_legs_into_ladders`: KXBTC/KXFEDDECISION 합성 fixture, suffix parsing
  edge case (음수 cut, hold, ASC/DESC approval).
- `find_arb_opportunities`: 인접 inversion 1c ~ 30c sweep, fee 0/0.07 toggle,
  MIN_DEPTH 통과/탈락 boundary.
- `find_fade_opportunities`: arb 실패 → fade 발생 경로.
- Bucketed `Σ yes_ask < 1` 시나리오, edge case (단일 leg, 빈 group, fee_budget
  경계).

### Integration (`tests/test_ladder_*.py`)

- 각 sub-strategy: mocked `KalshiClient` + `OrderManager` → `scan_and_execute`
  → 예상 fire 검증.
- **ladder_shutdown V1 잔재 dedup 시나리오**: DB 에 KXNUMSHUTDOWNS-T2
  paper_filled seed → 새 진입 시도 → DB guard reject 검증.
- **ladder_fed × C1 충돌 시나리오** (C1 sub-project 구현 후): 두 strategy 가
  동일 ticker fire 시도 → priority 가 ladder_fed 먼저 잠금 검증.
- Per-event lock: 같은 group_key 두 번 fire 안 됨.

### Manual paper smoke (배포 전)

- 실제 운영 5분 `ladder_violation_scanner.py` 결과 확인.
- 첫 paper fire 시 Telegram alert + DB row 검증.

## Instrumentation

### 신규 DB table `ladder_violations`

```sql
CREATE TABLE ladder_violations (
    id INTEGER PRIMARY KEY,
    ts TIMESTAMP NOT NULL,
    series TEXT NOT NULL,
    event_key TEXT NOT NULL,
    ladder_type TEXT NOT NULL,        -- 'cumulative' | 'bucketed'
    leg_a_ticker TEXT NOT NULL,
    leg_b_ticker TEXT,                -- null for sum-to-1 (전체 leg sum)
    inversion_amount REAL NOT NULL,   -- 음수면 sum-to-1 deviation
    leg_a_depth REAL,
    leg_b_depth REAL,
    action_taken TEXT NOT NULL,       -- 'arb' | 'fade' | 'skip_depth' | 'skip_edge'
    sub_strategy TEXT NOT NULL
);
CREATE INDEX idx_lv_strategy_ts ON ladder_violations(sub_strategy, ts);
```

### Telegram alerts

- 큰 위반 (>10c inversion) 감지 시 즉시 alert.
- 첫 arb 성공 시 즉시 alert.
- 일일 sub-strategy 별 summary (mention/v37 패턴).

### Dashboard

- Per-sub-strategy panel (n_violations, n_arb, n_fade, ROI, open positions).
- DEMOTED + SLEEPING 섹션에 미달 sub-strategy 표시
  (`soccer_promotion_eval_pipeline.md` 패턴).

## Kill / rollback criteria

Live 배포 후:
- 일주일 내 누적 -$30 → 즉시 paper 강등 + 분석.
- Arb path 위반율이 paper vs live 30% 이상 차이 → adverse selection 의심,
  즉시 검증.
- DB dedup 사고 (sub-strategy 별 동일 ticker 2건 이상 동일 시각 fire) → 즉시
  전 sub-strategy halt + post-mortem.

## Estimated effort

| Phase | Task | 예상 |
|---|---|---|
| 0a | `ladder_framework.py` + unit tests | 1일 |
| 0b | 4 sub-strategy 구현 + integration tests | 1.5일 |
| 0c | `ladder_violation_scanner.py` + DB table 추가 | 0.5일 |
| 0d | Phase 0 prevalence 운영 | 2주 (대기) |
| 1 | Phase 0 분석 + paper 배포 | 0.5일 + 1-2주 (대기) |
| 2 | Promotion eval 연결 + live ramp | 0.5일 + 점진 |

**총 active 작업**: ~4일. **총 elapsed**: ~4-5주 (검증 대기 포함).

## Open questions

1. **Phase 0 의 `ladder_violations` table 이 main bot.db 와 분리되어야 할지** —
   schema 추가는 가벼우나 production DB 에 research-only table 늘리는 것에
   대한 정책. (메모리 `2026-05-05_outage_postmortem.md` 의 lock race 컨텍스트)
2. **KXFEDDECISION suffix parsing** — `H0` (hold) vs `H25` (hike 25bps) 구분
   확실한가? 실제 ticker 샘플 더 봐야 함. Phase 0 운영 중 확정.
3. **MAX_POSITION_PER_GROUP=50** — paper 시 적정 size? bankroll $1000 기준 5%
   per group. 4 sub-strategy 가 동시 fire 시 최대 노출 $200. 적정성 검증 필요.
4. **Spot anchor 의 staleness 기준** — Coinbase WS 가 끊기면 어떻게? fallback
   spot source 필요? 또는 spot anchor signal 만 disable?

## Decision log

- **2026-05-09**: shutdown_fade V1 폐기 prereq 분석 → 본 sub-project 도출.
- **2026-05-09**: Universe scope = "All — 5축 generic framework" (user 선택).
- **2026-05-09**: Posture = Hybrid (arb 먼저, fade fallback) (user 선택).
- **2026-05-09**: Architecture = Approach 2 (Framework + 5 sub-strategy)
  (user 선택). Parlay deferred → 4 sub-strategy 만 본 spec.
- **2026-05-09**: 14-task implementation 완료 (commits cfa0e9a..3c14ac9 on
  branch feat/ladder-consistency-arb). 38 ladder unit/integration tests
  green, V1 dedup integration test pass, scanner --once dry run successful.

## Implementation deferrals (post-final-review)

본 v0 구현은 다음 항목을 의도적으로 deferred — Phase 0 prevalence 결과
분석 후 우선순위에 따라 후속 commit 으로 처리:

1. **Bid-ask collapse**: 4 sub-strategy 가 `client.get_market_prices()` 의
   ask-only 반환값으로 yes_bid = yes_ask 설정 → cumulative arb inversion
   체크가 ask-vs-ask 비교로 collapse. Phase 0 영향: signal overfire (보수
   적 — 실제로는 더 적게 fire). Live promotion 전 양면 호가 fetch 필요.
2. **Fade size = constant**: spec 의 edge-scaled `clamp(inv/0.05, 0.5,
   2.0)` 미적용 — flat $20 (LADDER_MAX_POSITION_PER_GROUP / 2.5). Paper 안전.
3. **`LADDER_STALE_ORDERBOOK_SEC` 미사용**: config knob 만 있고 실제 staleness
   체크 없음. 특히 ladder_crypto 에서 필요.
4. **`record_ladder_violation` sub-strategy 미호출**: 현재는 Phase 0 scanner
   에서만 logging. 라이브 sub-strategy 에서는 trade 만 기록 → dashboard 의
   "n_violations per sub_strategy" 가 scanner 데이터에만 의존.
5. **Spot anchor (Coinbase WS)**: spec open question 4 → ladder_crypto 의
   ATM proxy 가 yes_ask≈0.50 leg 사용 (Coinbase feed 불필요한 v0 cut).
   **2026-05-10 promoted to dedicated sub-project B2** (spec
   `2026-05-10-crypto-fair-value-trader-design.md`) — Coinbase/Binance WS
   spot + Deribit IV → fair-value model 별도 strategy 로 분리.
6. **ladder_crypto ticker parser 이슈** (2026-05-10 fixed): Kalshi 가
   ticker 형식 `B<int>` → `T<float>` 변경 (3-part `KXBTC-26MAY1017-T89749.99`).
   기존 `_parse_suffix` (B prefix only) + `_extract_event_key` (>=4 parts
   요구) 가 모든 현 prod 마켓 silent skip 중이었음. B2 brainstorm 중 발견,
   commit `<sha>` 에서 prefix-agnostic parser + 3/4-part 모두 처리 로
   수정. 기존 6 테스트 + 신규 2 테스트 (current format) green.
