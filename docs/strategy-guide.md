# Kalshi Trading Bot — Strategy Guide

**Last Updated**: 2026-03-30
**Status**: 3 Live + 4 Paper, $100 invested, 7 strategies

---

## Strategy Overview

| # | Strategy | Mode | Settle | Edge Source | Min Edge |
|---|----------|------|--------|-------------|----------|
| 1 | **Mentions Trader** | LIVE | 월 1회 | Fed 트랜스크립트 빈도 | 15% |
| 2 | **Spread Scanner** | LIVE | 수 시간 | Binance 실시간 가격 | 5% |
| 3 | **Sports Trader** | LIVE | 일~주 | 스포츠북 컨센서스 오즈 | 8% |
| 4 | Market Maker | Paper | 수 시간 | 오더북 스프레드 | — |
| 5 | Shutdown Fade | Paper | 월 | 역사적 기저율 | 25c+ |
| 6 | Sum Arb | Paper | 수 시간 | YES+NO < $1 | 3c |
| 7 | Maker Pre-Order | Paper | 분 | 마켓 오픈 전 주문 | — |

---

## 1. Mentions Trader (LIVE) ⭐ 최고 알파

**원리**: Fed 기자회견에서 Powell이 특정 단어를 말할 확률을 과거 트랜스크립트로 추정. Kalshi 마켓 가격과 비교해서 미스프라이싱 발견.

**데이터**: 8개 FOMC 기자회견 트랜스크립트 (2024.01 ~ 2024.12) 파싱.
- `scripts/build_mention_data.py`로 갱신 가능
- `data/mention_frequencies.json`에 저장

**엣지 예시**:
| 키워드 | Kalshi 가격 | 실제 빈도 | 엣지 | 액션 |
|--------|-----------|----------|------|------|
| AI | YES 82c | 12% (1/8) | 70c | BUY NO |
| Gas | YES 71c | 0% (0/8) | 71c | BUY NO |
| Volatility | YES 29c | 100% (8/8) | 71c | BUY YES |
| Oil | YES 85c | 12% (1/8) | 73c | BUY NO |

**사이징 (공격적)**:
- 50%+ 엣지 → 15% bankroll
- 30%+ 엣지 → 10% bankroll
- 15%+ 엣지 → 5% bankroll

**주문 방식**: 리밋 오더 (Maker). Fair value(실제 빈도)에 배치.

**Settle**: 4/30 Fed 기자회견 후. 연 8회.

**근거**: Top 트레이더 Foster ($2.5K→$500K), catboyautist ($1K→$300K)가 같은 전략 사용.

---

## 2. Spread Scanner (LIVE) — 크립토 단기

**원리**: Binance 실시간 가격으로 Kalshi 크립토 "above/below" 마켓의 공정 확률 추정. 마켓 가격과 5%+ 차이 시 거래.

**확률 추정**: Sigmoid 모델
```
distance = (spot_price - threshold) / threshold
probability = 1 / (1 + exp(-distance × 200))
```

예: BTC $82K, 마켓 "BTC above $83K"
→ distance = -1.2% → sigmoid → YES 확률 ~8%
→ Kalshi에서 YES=30c면 → NO 매수 (edge 22%)

**설정 (백테스트 최적화)**:
- **NO-only**: 백테스트에서 NO가 YES보다 수익적 (50.5% vs 49.7%)
- **Position 2%**: Max DD 75%→33%
- **최대 12시간 settle**: 자본 회전 극대화
- **Min edge 5%**: 백테스트 combo #7

**백테스트 결과** (BTC 30일):
- 459 trades, 50.5% win rate
- $1K → $2,420 (+142%)
- Max drawdown 33%

---

## 3. Sports Trader (LIVE) — NBA/MLB/NHL

**원리**: The Odds API에서 여러 스포츠북의 컨센서스 오즈를 수집. Kalshi 게임 마켓 가격과 비교해서 8%+ 미스프라이싱 발견.

**데이터**: The Odds API (무료 tier, 월 500 요청)
- NBA, MLB, NHL 개별 경기 승패 마켓
- 여러 스포츠북 평균 → vig 제거 → 공정 확률

**프로세스**:
1. Kalshi에서 KXNBAGAME, KXMLBGAME, KXNHLGAME 마켓 수집 (134+개)
2. The Odds API에서 같은 경기 오즈 수집
3. 팀명 매칭 → 공정 확률 vs Kalshi 가격 비교
4. 8%+ 엣지 시 리밋 오더 배치

**사이징**:
- 15%+ 엣지 → 10% bankroll
- 8%+ 엣지 → 5% bankroll

**Settle**: 경기 종료 후 (당일~수일)

---

## 4. Market Maker (Paper)

**원리**: 크립토 마켓에서 양방향 호가 제시, 스프레드 수익. @debl00b ($160M vol) 영감.

- 오더북 VWAP 기반 mid price 계산
- 30초 리쿼팅 (cancel + replace)
- **동적 스프레드**: 10분+: 4c, 5-10분: 3c, 2-5분: 2c
- 인벤토리 스큐: 포지션 편중 시 호가 조정
- 최대 3개 마켓 동시 호가

**상태**: Paper — 리밋 오더 체결 시뮬레이션 필요

---

## 5. Shutdown Fade (Paper)

**원리**: 정부 셧다운 마켓에서 YES 고평가 시 NO 매수. 역사적으로 셧다운은 거의 발생 안 함.

- 키워드 검색: "shutdown", "appropriations", "funding deadline"
- 3단계 스케일인 (25c, 40c, 55c)
- 최대 $500/마켓

**상태**: Paper — 70건 거래, $11.7K 규모

---

## 6. Sum Arb (Paper)

**원리**: YES + NO < $1.00 (수수료 후) → 양쪽 동시 매수, 무위험 차익.

- 크립토 300개 마켓 스캔
- 최소 3c 갭
- **현실**: Kalshi 마켓이 효율적이라 기회 거의 없음 (최선 갭 -0.01)

---

## 7. Maker Pre-Order (Paper)

**원리**: 2-30분 내 종료 마켓에 양쪽 리밋 오더 배치.

- 합산 < $1.00 → 차익
- 자동 취소 (4분 후)

---

## Risk Management

| 파라미터 | 값 | 설명 |
|---------|---|------|
| MAX_SINGLE_TRADE_PCT | 2% | 기본 거래당 최대 (bankroll 대비) |
| 멘션 고확신 | 15% | 50%+ 엣지 시 |
| MAX_DAILY_LOSS | $200 | 일일 손실 한도 |
| MAX_CONSECUTIVE_LOSSES | 5 | 연속 손실 시 15분 정지 |
| Order type | 리밋 (전체) | Maker: -10% vs Taker: -32% (학술 연구) |
| Bankroll check | 잔고 < 거래액 시 스킵 | 마이너스 방지 |

---

## Per-Strategy Live Control

`.env`에서 전략별 Live/Paper 전환:
```
TRADING_MODE=paper              # 기본 Paper
LIVE_STRATEGIES=mentions_trader,spread_scanner,sports_trader  # 이 전략만 Live
```

나머지 전략은 자동으로 Paper 유지.

---

## Key Learnings

### 백테스트에서 배운 것
1. **NO가 YES보다 수익적** — spread scanner NO-only 모드
2. **Position 2%로 DD 절반** — 75%→33%
3. **Fee 절감이 가장 큰 임팩트** — 리밋 오더 전면 전환
4. **극단 가격 (5c~15c) 매수 금지** — 낮은 확률 ≠ 저평가

### 실전에서 배운 것
1. **v1 spread scanner -$197** — 7c YES 매수로 20/22 패배
2. **월간 마켓에 자본 묶임** — 크립토 단기로 전환
3. **Sigmoid 수식 오류** — `/current_price` → `/threshold`
4. **Paper 리밋 오더 settle 안 됨** — 쿼리에 paper_open 누락

### Top 트레이더 리서치
- **Taker -32% vs Maker -10%** (Karl Whelan, 300K 계약 분석) → 리밋 오더 전면 사용
- **catboyautist** ($1K→$300K): 멘션 트랜스크립트 분석 + 공격적 사이징
- **Foster** ($2.5K→$500K): 멘션 + 정치 이벤트 전문
- **GaetenD** ($530K): "95% 수학, 5% 직감"

---

## Architecture

```
Market Data → Strategy → Signal → Order Manager → Kalshi API
                                       ↓
Binance (crypto)     evaluate()    _is_live()?
The Odds API (sports)              → Live: real order
Fed Transcripts (mentions)         → Paper: simulated
```

**Settlement Loop** (60초):
```
Kalshi API → market finalized? → calculate PnL → update bankroll
```

---

## Files

| File | Role |
|------|------|
| `strategies/mentions_trader.py` | Fed 멘션 트레이딩 |
| `strategies/spread_scanner.py` | 크립토 Binance 기반 |
| `strategies/sports_trader.py` | 스포츠 오즈 비교 |
| `strategies/market_maker.py` | 양방향 호가 |
| `strategies/shutdown_fade.py` | 셧다운 페이드 |
| `strategies/sum_arb.py` | YES+NO 아비트라지 |
| `strategies/maker_preorder.py` | 단기 마켓 프리오더 |
| `core/settlement.py` | 자동 PnL 계산 |
| `core/external_data.py` | 멘션 빈도 데이터 |
| `scripts/build_mention_data.py` | 트랜스크립트 파싱 |
| `scripts/backtest_spread_scanner.py` | 크립토 백테스트 |
| `data/mention_frequencies.json` | Powell 키워드 빈도 |
