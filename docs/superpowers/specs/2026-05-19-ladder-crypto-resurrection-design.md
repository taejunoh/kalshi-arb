# Ladder Crypto Resurrection (Phase 0) — Design Spec

**날짜**: 2026-05-19
**상태**: 설계 (사용자 검토 대기)
**Sub-project of**: 새 알파 소스 portfolio (B → A → D 순차 구축)
**참조**:
- `core/ladder_framework.py` — 기존 math (T-cumulative + B-bucketed) 재사용
- `strategies/ladder_crypto.py` — retired 상태 (commit 6627000)
- `docs/superpowers/specs/2026-05-09-ladder-consistency-arb-design.md` — 원래 ladder 설계
- 메모: `ladder_crypto retired 2026-05-13`, `crypto_observer portfolio 2026-05-13`

---

## Context

ladder_crypto 는 2-step 으로 retired:
1. **Pre-fix (-77.5% ROI, n=54)**: B-bucket 을 T-cumulative 로 잘못 다뤘음. d30efb5 에서 split 수정.
2. **Post-fix (structurally silent)**: math 는 맞지만 fire 0. **Phantom-quote pollution** 이 원인 — Kalshi 가 보여주는 top-of-book yes_ask 가 1-contract 짜리 stale resting order 인 경우가 잦아, framework 가 arb signal 을 감지해도 실제 그 가격에 채울 수 없음. `min_depth_usdc` 게이트는 *전체 orderbook 합계* 만 보고 top-of-book 의 진짜 fillable 사이즈는 확인 안 함.

현 portfolio (sports 중심 + consensus mispricing 기반 13+ 전략) 가 전부 tail-driven 으로 누적 우상향 실패. 완전 새로운 paradigm (B: mechanical complementarity → A: market making → D: mean reversion) 순차 구축 합의. **B 부활의 핵심 = phantom-quote pollution 해결**.

## Goals

1. Phantom-quote 문제 해결: 실제 orderbook level 을 walk 하여 "$X 채울 때 진짜 평균 fill price" 시뮬레이션.
2. Edge threshold 상향 ($0.05 → $0.15) 로 작은 phantom inversion 노이즈 자연 필터링.
3. **Phase 0 paper-only** 로 1-2주 prevalence 측정. fire 0. `ladder_violations` 에 walked-edge 기반 기록.
4. 결정 criteria 명시 → Phase 1 (paper trader) / Phase 2 (live) / 폐기 분기.

## Non-Goals

- ❌ KXFED, KXTRUMPAPPROVAL, KXSHUTDOWN ladder 부활 — 별도 sub-project (B 다음 cycle).
- ❌ Kalshi WS orderbook 구독 — Phase 2 transition 시 재평가. Phase 0 은 REST 만.
- ❌ Daily expiry (KXBTCD/KXETHD) — hourly 가 primary test bed.
- ❌ Pre-fire probe (test order 후 나머지 leg fire) — Phase 1 paper 단계에서 필요시 추가.
- ❌ Live fire — Phase 0 = 100% paper logging.
- ❌ Edge-scaled/Kelly sizing — Phase 0 은 fixed $25 walked notional.

## Architecture

### 모듈 layout

```
core/
  ladder_framework.py             [기존, 변경 없음]
  orderbook_walk.py               [NEW] — stateless walker utility
strategies/
  ladder_crypto.py                [수정] — Phase 0 paper logging 모드
db/migrations/
  20260519_ladder_violations_walked.sql  [NEW]
scripts/
  ladder_crypto_phase0_summary.py [NEW] — daily summary cron
tests/
  test_orderbook_walk.py          [NEW]
  test_ladder_crypto_phase0.py    [NEW]
```

### Component 1 — Orderbook walker (`core/orderbook_walk.py`)

Stateless utility. 다른 strategy 에서도 재사용 가능.

```python
@dataclass(frozen=True)
class WalkResult:
    avg_fill_price: float          # walked 평균 (≥ top_ask)
    max_fillable_usdc: float       # 실제 채울 수 있는 최대 notional
    levels_consumed: int           # 사용된 ladder level 수
    top_ask: float                 # top-of-book
    top_vs_walked_gap_cents: float # (avg_fill_price - top_ask) * 100

def walk_orderbook(
    client,
    ticker: str,
    side: Literal["yes", "no"],
    target_notional_usdc: float,
) -> Optional[WalkResult]:
    """Kalshi REST /markets/{ticker}/orderbook 호출 → ladder walk.

    Returns None if book empty or API error.
    Returns max_fillable < target_notional if book too thin.
    """
```

Kalshi orderbook endpoint 응답 형식은 구현 시작 시 1차 확인 필요. 예상: `{"yes": [[price_cents, size], ...], "no": [[price_cents, size], ...]}` (best 가격부터 정렬).

### Component 2 — Ladder strategy Phase 0 mode (`strategies/ladder_crypto.py`)

기존 `LadderCryptoStrategy._handle_group` 수정:

```python
async def _handle_group(self, group):
    cfg = self.config
    if group.ladder_type == "bucketed":
        sum_mid = sum((l.yes_ask + l.yes_bid) / 2.0 for l in group.legs)
        if not (BUCKETED_SUM_MID_MIN <= sum_mid <= BUCKETED_SUM_MID_MAX):
            return

    arb_candidates = find_arb_opportunities(group, ...)  # 기존 호출
    for arb in arb_candidates:
        walked = await self._walk_arb(arb)  # 신규
        await self._log_phase0(group, arb, walked)  # 신규
        # NO market_order — Phase 0 paper only

    fade_candidates = find_fade_opportunities(group, ...)
    for fade in fade_candidates:
        walked = await self._walk_fade(fade)
        await self._log_phase0_fade(group, fade, walked)
```

`_walk_arb` 로직:
1. 각 leg 에 대해 `walk_orderbook(ticker, side, $25)` 호출
2. 모든 leg `max_fillable_usdc >= $25` 인지 확인 (하나라도 부족하면 `would_fire=False`)
3. `walked_cost = Σ(walked.avg_fill_price * size_per_leg)`
4. `walked_edge_usdc = $1 - walked_cost - n*fee` (B-bucketed) 또는 등가식 (T-cumulative)
5. `top_vs_walked_max_gap_cents = max(walk.top_vs_walked_gap_cents for each leg)`
6. `all_fillable := all(leg.max_fillable_usdc >= LADDER_PHASE0_PROBE_NOTIONAL_USDC for leg in arb.legs)`
7. `would_fire := walked_edge_usdc >= LADDER_PHASE0_MIN_WALKED_EDGE_USDC AND all_fillable AND max_gap_cents <= LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS`

### Component 3 — `ladder_violations` 테이블 확장

기존 컬럼 유지 + 다음 컬럼 추가 (idempotent `ALTER TABLE IF NOT EXISTS` patterns 적용):

```sql
ALTER TABLE ladder_violations ADD COLUMN walked_edge_usdc REAL;
ALTER TABLE ladder_violations ADD COLUMN walked_legs_json TEXT;
ALTER TABLE ladder_violations ADD COLUMN top_vs_walked_max_gap_cents REAL;
ALTER TABLE ladder_violations ADD COLUMN would_fire INTEGER DEFAULT 0;
ALTER TABLE ladder_violations ADD COLUMN phase TEXT DEFAULT 'phase0_paper';
```

`walked_legs_json` 구조 예시:
```json
[
  {"ticker": "KXBTC-26MAY19-1500-B66250", "side": "yes",
   "top_ask": 0.04, "walked_avg": 0.045, "max_fillable_usdc": 30.0},
  {"ticker": "KXBTC-26MAY19-1500-B66350", "side": "yes",
   "top_ask": 0.05, "walked_avg": 0.05, "max_fillable_usdc": 50.0}
]
```

### Component 4 — Daily summary cron (`scripts/ladder_crypto_phase0_summary.py`)

매일 EST 09:00 systemd timer. Telegram message:

```
ladder_crypto Phase 0 — daily (2026-05-19)
  BTC violations (would_fire): 3 (edge avg $0.18)
  ETH violations (would_fire): 1 (edge $0.21)
  phantom-rate (gap >5¢): 12% of detected candidates
  cumulative (14d window): 14 fires / Phase 1 gate=10 ✅
```

Cumulative count 가 Phase 1 gate (≥10 in 14d) 통과 시 Telegram에 별도 알람.

### Data flow

```
scan_loop (30s) → market_scanner.get_unlocked_markets() →
  filter KXBTC/KXETH current+next hourly →
    group_legs_into_ladders →
      find_arb_opportunities / find_fade_opportunities (기존) →
        for each candidate:
          walk_orderbook per leg →
            compute walked_edge →
              threshold check ($0.15 + gap ≤ 2¢ + all fillable) →
                INSERT INTO ladder_violations (phase='phase0_paper', would_fire=?)
```

## Parameters

```python
# config.py additions
LADDER_PHASE0_MIN_WALKED_EDGE_USDC = 0.15
LADDER_PHASE0_MAX_TOP_WALKED_GAP_CENTS = 2.0
LADDER_PHASE0_PROBE_NOTIONAL_USDC = 25.0
LADDER_PHASE0_TARGET_EXPIRIES_AHEAD = 2  # 가장 빨리 close 되는 hourly expiry + 그 다음 1개
# (이미 close 된 expiry / 24h+ 미래 expiry 둘 다 scan universe 에서 제외)
```

## Error handling

| 케이스 | 동작 |
|---|---|
| Orderbook REST API 실패 (timeout, 5xx) | 해당 group 전체 skip. 로그 X. 다음 scan 재시도. |
| Orderbook 응답이 empty (`{"yes": [], "no": []}`) | `max_fillable_usdc = 0`, `would_fire = False` 로 INSERT. |
| 호가 staleness (REST 시점 < scan 시점) | v0 에서 검증 skip (REST 호출 시점이 ground truth). |
| 모든 leg fillable 확인 도중 1개라도 `max_fillable < target` | `would_fire = False`. 나머지 leg 도 walk 해서 logging (분석용). |
| `walked_edge < 0` 인데 `find_arb_opportunities` 가 candidate 로 반환 | INSERT with `would_fire = False`. top vs walked gap 통계 수집. |
| DB INSERT 실패 (lock, disk full) | 로그 ERROR, scan loop 계속 (단일 group 실패가 전체 멈춤 X). |

## Rate limit

- 최악 시나리오: KXBTC + KXETH × current+next hourly = 4 expiry. expiry 당 ~30 strikes (B-bucket dominant). 모든 strike orderbook 호출 = 120 calls. 30초 scan 주기 → 4 req/s.
- Kalshi 공개 rate limit ~10 req/s 안전 마진 확보.
- 만약 candidate violations 가 흔하지 않으면, walk 은 candidate 발생 시만 호출 → 대부분 scan 에서 walk call 0.

## Testing

### Unit (orderbook_walk)
- 빈 책: `walk_orderbook(...) → None`
- 1-level 책 (top=$0.04, size=10): target $25 → `max_fillable=$0.40` (size 10 × $0.04), `levels_consumed=1`
- Multi-level 책: target 충족 시 누적 avg + gap 계산 정확성
- API error mock: None 반환

### Unit (Phase 0 strategy)
- arb candidate 감지됐는데 walk 결과 phantom (gap=$0.10): `would_fire=False`, gap 컬럼 정확히 저장
- 모든 gate 통과: `would_fire=True`, edge 양수 INSERT
- B-bucket sum 범위 밖: 기존 `_handle_group` 가 skip — 변경 없음

### Integration
- 기존 sports / crypto observer scan 과 동시 실행 시 race condition 없음 (별도 scan_loop iteration)
- DB migration idempotent: 두 번 실행해도 컬럼 중복 추가 X

## Phase 0 → 다음 단계 결정 트리

| 14일 누적 결과 | 다음 단계 |
|---|---|
| `would_fire=True AND walked_edge>0` rows ≥ 10 | → Phase 1 paper trader (별도 PR 로 config 토글 `LADDER_CRYPTO_PHASE='phase1_paper'` 추가 → `_handle_group` 이 `market_order` 호출하되 `paper_twin=True` 로 paper_filled DB row 생성. 새 strategy class 만들지 않음) |
| 위 카운트 ∈ [3, 9] | → 1주 더 연장 + threshold/notional 조정 검토 |
| 위 카운트 ≤ 2 | → **폐기**. crypto ladder 에 진짜 알파 없음 최종 결론. |
| 카운트 ≥ 10 인데 `p90(top_vs_walked_gap) > 5¢` | → phantom 빈도 높음. Phase 1 전에 Kalshi WS (b 옵션) 빌드 필요 결정. |
| **Early abort**: 처음 48h 에서 candidate(would_fire=True or False 무관) = 0 | → 코드 버그 검증 (post-fix structurally silent 패턴 반복 의심). |

## 핵심 KPI (수동 query / dashboard)

1. `count(*) WHERE phase='phase0_paper' AND would_fire=1` 일별 추이
2. `avg(walked_edge_usdc) WHERE would_fire=1` — 평균 기회 크기
3. `percentile(top_vs_walked_max_gap_cents, 90)` — phantom 강도
4. `count distinct event_key` — 분산 vs 집중
5. Top 5 violation `event_key` + their walked_edge — concentration check

## Open questions (구현 단계에서 해결)

1. Kalshi `/markets/{ticker}/orderbook` 정확한 응답 schema 확인 — `client.get_order_book_depth` 가 어떻게 호출 중인지 1차 read.
2. Walked notional $25 가 실측 적절한지 — 구현 후 첫 24h 데이터로 $50/$100 재조정 가능.
3. T-cumulative 의 walked_edge 정의 (현재 framework: `1 - (t1.yes_ask + t2.no_ask) - 2*fee`) — walk 대상이 t1.yes / t2.no 두 leg 만이므로 적용 동일.

## Migration plan

1. PR 1: `core/orderbook_walk.py` + tests (independent, no behavior change)
2. PR 2: DB migration + ladder_violations schema 확장
3. PR 3: `strategies/ladder_crypto.py` Phase 0 모드 (paper logging) + tests
4. PR 4: `scripts/ladder_crypto_phase0_summary.py` + systemd timer
5. Deploy → 14일 paper run → 결정 트리 적용

## 위험 / 가설

- **가설 1**: phantom-quote 가 정말로 retired 원인이다. 만약 다른 원인 (e.g., scan 빈도 부족, scanner universe filtering) 이면 walk 추가해도 silent 동일.
  - 대응: Early abort 48h gate. Candidate 자체가 0 이면 framework 진입 자체가 안 됨 → 다른 원인.
- **가설 2**: KXBTC/ETH 에 진짜 ladder alpha 가 남아있다. 만약 다른 봇들이 다 잡았다면 walked_edge 양수 violations = 거의 0.
  - 대응: Phase 0 14일 = 폐기 결론 빠르게 도출. WS (b) 빌드 비용 회피.
- **가설 3**: $0.15 threshold 가 적절. 너무 높으면 모든 candidate 차단, 너무 낮으면 phantom 다시 포함.
  - 대응: Phase 0 데이터는 모든 candidate (would_fire=False 포함) 저장 → 사후 threshold 튜닝 가능.

## 성공 정의

Phase 0 의 성공 = 명확한 결론 (PASS / FAIL / 폐기) 14일 안에 도출. 측정 단위 = "결정에 도달했는가", 아직 PnL 아님.
