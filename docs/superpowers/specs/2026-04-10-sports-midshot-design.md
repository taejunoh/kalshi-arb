# Sports Midshot 전략 — Design Spec

**Date**: 2026-04-10
**Status**: Design approved, ready for plan
**Author**: Claude (브레인스토밍 세션)

## Context

Sports V3 (2026-04-08 배포, Longshot/Underdog/Consensus 3전략 독립 비교)가 시그널 수집 부족으로 사실상 정체됨.

**현재 상태** (2026-04-10 11:12 UTC EC2 실측):

| 전략 | 시그널 | DB 체결 | 비고 |
|---|---|---|---|
| sports_longshot | 4/8 4건, 4/9·4/10 0건 | 0 (4/8은 paper_cancelled 버그) | 5-25c, 3x+ payoff — NBA 플레이오프 blowout에만 걸림 |
| sports_underdog | 4/9 20건, 4/10 0건 | 2 (loss) | 2x+ payoff |
| sports_trader (consensus) | 0건 | 0 | 3% net edge — 공개 오즈 데이터로 엣지 없음 |

**문제**:
- 20+ settle 비교까지 수주~수개월 소요 예상. 특히 Longshot은 사실상 샘플 수집 불가
- 필요한 건 **극단 blowout ~ 일반 언더독 사이의 "중간 가격 밴드" 전략**

**결정** (2026-04-10 세션):
1. 기존 `sports_longshot`은 **코드 변경 없이** 메모/라벨링만 "playoff/blowout 전용"으로 재정의. Strict filter(`5-25c, 3x+`) 자체가 de-facto blowout 필터라서 추가 게이팅 불필요.
2. 신규 **`sports_midshot`** 전략 추가: `5-35c`, `payoff ≥ 2.5x`. 기존 longshot과 overlap 허용 → 동일 경기에서 longshot/midshot/underdog 셋이 교차검증.

## Goals

- 새 Midshot 전략을 기존 V3 인프라(base class, settlement, dashboard, telegram)에 추가하고 Paper 모드로 배포
- 기존 longshot/underdog/consensus 동작에 영향 없음
- 20+ settle 후 전략별 PnL 비교가 의미 있을 만큼 샘플 수집 가능 (일일 5~10건 예상)

## Non-Goals

- Longshot 코드 수정 — 그대로 둠 (playoff/blowout-only 라벨은 메모리/docs에만)
- Playoff 감지 로직 추가 — YAGNI
- Sizing 공식 차별화 — longshot과 동일(`$1.80`)로 시작, 20 settle 후 데이터 보고 조정
- Live 모드 전환 — 현 세션에서는 Paper만
- Consensus 전략 재설계 — 별도 이슈

## Design

### 필터

```
0.05 ≤ price ≤ 0.35
payoff = 1 / (price × (1 + fee_pct)) ≥ 2.5
```

- `0.05` lower bound: base class 하한 [sports_trader.py:356](../../../strategies/sports_trader.py#L356)과 일치. Dust 마켓 회피
- `0.35` upper bound: 사용자 지정
- `2.5x` payoff: 사용자 지정

**Kalshi 수수료 cap은 0.07** ([core/config.py:13](../../../core/config.py#L13)). 실제 payoff = `1 / (price + min(0.07, price))`. 수수료 반영 effective 범위:

| Price | 수수료 포함 payoff | Longshot (5-25c, 3x+) | Midshot (5-35c, 2.5x+) | Underdog (2x+, no cap) |
|---|---|---|---|---|
| 0.05 | 10.0x | ✓ | ✓ | ✓ |
| 0.10 | 5.88x | ✓ | ✓ | ✓ |
| 0.15 | 4.55x | ✓ | ✓ | ✓ |
| 0.20 | 3.70x | ✓ | ✓ | ✓ |
| 0.25 | 3.125x | ✓ (경계) | ✓ | ✓ |
| 0.30 | 2.70x | ✗ (price cap) | ✓ | ✓ |
| **0.33** | **2.50x** | ✗ | ✓ **(effective 경계)** | ✓ |
| 0.35 | 2.38x | ✗ | ✗ (payoff < 2.5) | ✓ |
| 0.43 | 2.00x | ✗ | ✗ | ✓ (경계) |
| 0.50 | 1.75x | ✗ | ✗ | ✗ |

**중요**: `price ≤ 0.35` 와 `payoff ≥ 2.5x` 조합의 effective upper는 **~0.33**. 0.33–0.35 구간은 payoff 조건이 먼저 거름. 의도한 동작.

Midshot이 Longshot 대비 추가로 커버하는 구간: **25c–33c** (약 8센트 폭). 좁아 보이지만 일반 언더독 라인 대부분 이 대역에 있어서 실제 시그널 증가 기대.

Overlap은 의도된 것. 같은 경기에서 전략별 독립 기록 → A/B/C 비교 신뢰성 확보.

### 코드 구조

기존 패턴 정확히 미러링. SportsTraderStrategy base 상속.

**신규 파일**: [strategies/sports_midshot.py](../../../strategies/sports_midshot.py)

```python
"""Sports Midshot Strategy — 5-35c contracts with 2.5x+ payoff."""
import logging
from typing import Optional

from strategies.sports_trader import SportsTraderStrategy

logger = logging.getLogger("sports_midshot")


class SportsMidshotStrategy(SportsTraderStrategy):
    """Buy cheap-to-mid contracts with moderate payoff potential."""

    TRADED_TICKERS_FILE = "data/sports_midshot_tickers.json"
    STRATEGY_NAME = "sports_midshot"

    def __init__(self, config, order_manager, kalshi_client):
        super().__init__(config, order_manager, kalshi_client)
        self._traded_tickers = {}
        self._load_traded_tickers()

    def evaluate(self, kalshi_yes, kalshi_no, fair_prob,
                 line_movement=0.0, home_adj=0.0, ticker="") -> Optional[dict]:
        """Buy contracts 0.05 ≤ price ≤ 0.35 with 2.5x+ payoff."""
        fee = self.config.KALSHI_FEE_PER_CONTRACT
        best_signal = None
        best_payoff = 0.0

        for side, price in (("yes", kalshi_yes), ("no", kalshi_no)):
            if not (0.05 <= price <= 0.35):
                continue
            fee_pct = min(fee, price, 1 - price) / price
            cost = price * (1 + fee_pct)
            payoff = 1.0 / cost
            if payoff >= 2.5 and payoff > best_payoff:
                best_signal = {"side": side, "price": price,
                               "edge": payoff, "strategy": "midshot"}
                best_payoff = payoff
        return best_signal

    def _get_trade_size(self, signal):
        return min(3.0, self._order_manager.bankroll * 0.02)

    def _get_limit_price(self, signal, fair_prob):
        return signal["price"]

    def _format_log(self, ticker, title, signal, ya, na, fair_prob, size,
                    limit_price, **kwargs):
        payoff = signal.get("edge", 0)
        return (f"[MIDSHOT] {ticker}: {title[:40]} "
                f"ask={ya:.2f}/{na:.2f} payoff={payoff:.1f}x "
                f"→ {signal['side']} ${size:.2f} LIMIT@{limit_price:.2f}")
```

**main.py 수정** (3 hunks):

1. Line 18 근처 — import 추가:
```python
from strategies.sports_midshot import SportsMidshotStrategy
```

2. Line 59 근처 — 인스턴스 생성:
```python
self.sports_midshot = SportsMidshotStrategy(
    self.config, self.order_manager, self.kalshi_client
)
```

3. Line 166 근처 — strategy loop 등록:
```python
("sports_midshot", self.sports_midshot),
```

**자동 생성**: `data/sports_midshot_tickers.json` (첫 저장 시)

### 데이터 흐름

`main._strategy_loop` → `sports_midshot.scan_and_execute` (base class) → `evaluate` (midshot override) → `_get_trade_size` → `_get_limit_price` → `order_manager.market_order(strategy="sports_midshot")` → `database.trades` INSERT → `settlement.simulate_paper_fills` → `settlement.check_and_settle` → `notifier.notify_settlement` + dashboard `/api/performance` 자동 반영.

전체 파이프라인이 `STRATEGY_NAME`으로 자동 분기되므로 dashboard/settlement/telegram 변경 **불필요**.

### Error handling

기존 base class가 처리:
- Odds API 실패: `_fetch_odds` → `[]` → skip scan
- Team 매칭 실패: `continue`
- 주문 실패: `order_manager`가 처리
- 중복 방지: `_traded_tickers` + 파일 저장
- exposure cap: `_get_live_sports_exposure` → bankroll × 30%

Midshot 고유 에러 경로 없음.

### 테스트

신규 파일 [tests/test_sports_midshot.py](../../../tests/test_sports_midshot.py):

```python
def test_midshot_accepts_20c_yes_payoff_above_threshold():
    # yes=0.20 (payoff 3.70x), no=0.80 → yes signal, edge=3.70

def test_midshot_rejects_40c_over_upper_bound():
    # yes=0.40, no=0.62 → None (both over price cap)

def test_midshot_rejects_35c_payoff_below_threshold():
    # yes=0.35 (payoff 2.38x < 2.5), no=0.66 → None (payoff fails)

def test_midshot_accepts_30c_at_effective_boundary():
    # yes=0.30 (payoff 2.70x), no=0.72 → yes signal

def test_midshot_picks_higher_payoff_side():
    # yes=0.30 (payoff 2.70x), no=0.20 (payoff 3.70x) → no side (higher payoff wins)

def test_midshot_rejects_dust_below_5c():
    # yes=0.03 → None (below 0.05 lower bound)
    # Note: base class already filters < 0.05, but assert evaluate itself handles it
```

기존 테스트 영향 없음 확인 (`pytest` 98/98 유지).

### 배포 검증 (5단계, `feedback_verification_process.md` 준수)

모든 단계 통과해야 "작동한다" 판단:

1. **로그**: EC2 `logs/bot.log` 에 `[MIDSHOT]` 출력 또는 `Scanned ... markets, 0 trades`
2. **DB**: `trades` 테이블에 `strategy='sports_midshot'` entry (필터 충족 시)
3. **Settlement**: 경기 종료 후 PnL 계산되어 `paper_settled` 상태 전환
4. **Dashboard**: `curl http://18.223.21.52:8080/api/performance | jq .sports_midshot` → 구조 확인
5. **Telegram**: 첫 settle 시 `@TjKalshiArb_bot` 알림 수신

## Critical files

| 파일 | 역할 | 변경 여부 |
|---|---|---|
| [strategies/sports_midshot.py](../../../strategies/sports_midshot.py) | 신규 전략 클래스 | NEW |
| [main.py](../../../main.py) | 봇 오케스트레이터 | 3 hunks |
| [strategies/sports_trader.py](../../../strategies/sports_trader.py) | base class | 참조 only (변경 없음) |
| [strategies/sports_longshot.py](../../../strategies/sports_longshot.py) | 기존 strict longshot | 참조 only (변경 없음, 메모리상 "blowout-only" 재정의) |
| [tests/test_sports_midshot.py](../../../tests/test_sports_midshot.py) | 단위 테스트 | NEW |
| [core/config.py](../../../core/config.py) | 설정 | 변경 없음 (SPORTS_ENABLED 이미 존재) |
| [monitoring/dashboard/api.py](../../../monitoring/dashboard/api.py) | dashboard API | 변경 없음 (strategy name 자동 픽업) |
| [core/database.py](../../../core/database.py) | DB schema | 변경 없음 |
| [core/settlement.py](../../../core/settlement.py) | settlement | 변경 없음 |

## Verification

**로컬 (pre-deploy)**:
```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
venv/bin/pytest tests/test_sports_midshot.py -v
venv/bin/pytest  # 전체 98+ 통과 확인
```

**EC2 deploy 후 (post-deploy)**:
```bash
# 1. 배포
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 \
  "cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot"

# 2. 로그 확인 (다음 sports scan 30분 기다린 후)
ssh ... "tail -100 logs/bot.log | grep -i midshot"

# 3. 시그널이 나오면 DB/dashboard 확인
curl -s http://18.223.21.52:8080/api/performance | python3 -m json.tool | grep -A5 midshot
curl -s http://18.223.21.52:8080/api/trades | python3 -c "import sys,json; [print(t) for t in json.load(sys.stdin) if 'midshot' in str(t.get('strategy',''))]"

# 4. Telegram 알림 확인 (첫 settle 후)
```

## Risks

1. **시그널 overlap으로 과다 체결**: Longshot+Midshot+Underdog 동일 경기 모두 체결 시 총 ~$5.40 한 경기. 허용 — 각자 독립 전략 비교 목적.
2. **Midshot이 Underdog과 거의 같은 결과 낼 위험**: 25c~35c 구간에서 둘 다 시그널 발생 → 비교 시 변별력 부족. 20 settle 후 데이터 봐서 필터 재조정 가능.
3. **배포 후에도 시그널 없을 가능성**: 금요일(4/10) 라인업이 극단적으로 평범하면 midshot도 0건 가능. 그 경우 실제 필터 설계를 더 느슨하게 재고.

## Follow-up

- [ ] writing-plans skill로 구현 계획 작성
- [ ] TDD로 구현 (tests first)
- [ ] 로컬 테스트 통과 → EC2 배포
- [ ] 5단계 검증 완료 후 `strategy_sports_v3.md` 메모리 업데이트
- [ ] 20 settle 후 V3 비교 레포트 (별도 세션)
