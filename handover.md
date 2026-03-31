# Kalshi Trading Bot — Handover Document

## Project Overview

Kalshi 예측 시장 자동 트레이딩 봇. 7개 전략 (3 Live + 4 Paper), 실시간 대시보드, 자동 Settlement.

- **Repo**: `github.com/taejunoh/kalshi-arb`
- **언어**: Python 3.11
- **서버**: AWS EC2 `18.223.21.52` (us-east-2, t3.micro)
- **대시보드**: http://18.223.21.52:8080
- **테스트**: 95개 전체 통과
- **총 입금**: $225 ($100 + $100 + $25)
- **Live 잔고**: $51.65
- **Live 포지션**: 12개 (멘션 마켓, ~$173 투자)

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (7개 전략 + settlement)
├── core/
│   ├── config.py                    # .env 설정, LIVE_STRATEGIES 전략별 모드
│   ├── database.py                  # SQLite — 거래/주문/settlement
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL
│   ├── kalshi_client.py             # REST API v2 (마켓 + 오더북 + 잔고 + 포지션)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 타겟 시리즈(30초)
│   ├── order_manager.py             # 전략별 Live/Paper, 잔고 체크, 포지션 추적
│   ├── settlement.py                # 마켓 settle 감지 → PnL 자동 계산
│   └── external_data.py             # Fed 트랜스크립트 빈도 데이터
├── strategies/
│   ├── mentions_trader.py           # ⭐ LIVE — Fed 멘션 (트랜스크립트 분석)
│   ├── spread_scanner.py            # ⭐ LIVE — 크립토 단기 (Binance 확률)
│   ├── sports_trader.py             # ⭐ LIVE — 스포츠 (오즈 API 비교)
│   ├── market_maker.py              # Paper — 양방향 호가
│   ├── shutdown_fade.py             # Paper — 셧다운 페이드
│   ├── sum_arb.py                   # Paper — YES+NO 아비트라지
│   └── maker_preorder.py            # Paper — 단기 프리오더
├── monitoring/
│   ├── logger.py                    # 루트 로거, 일별 로테이션
│   ├── notifier.py                  # Telegram 알림
│   └── dashboard/
│       ├── api.py                   # /status, /trades, /orders, /performance,
│       │                            # /settlement, /live, /strategy-status
│       └── static/index.html        # 대시보드 (Live/Paper 분리, 포지션 표시)
├── scripts/
│   ├── build_mention_data.py        # Fed 트랜스크립트 파싱
│   └── backtest_spread_scanner.py   # 크립토 백테스트
├── deploy/                          # EC2 배포 (setup.sh, systemd)
├── data/mention_frequencies.json    # Powell 키워드 빈도 데이터
├── docs/
│   ├── strategy-guide.md            # 전략 상세 가이드
│   └── backtest-results.md          # 백테스트 결과
└── tests/                           # 95개 테스트
```

---

## Strategies (7개)

### LIVE (3개 — 실제 돈)

**1. Mentions Trader** ⭐ 최고 알파
- Fed 기자회견 트랜스크립트 8개 분석 → 마켓 가격과 비교
- **공격적 사이징**: 50%+ 엣지 → 15% bankroll, 30%+ → 10%, 15%+ → 5%
- 12개 Live 포지션 보유 (~$173)
- Top 엣지: AI(82c vs 12%), Gas(71c vs 0%), Volatility(29c vs 100%)
- Settle: **4/30 Fed 기자회견** 후
- 시작 시 Kalshi 기존 포지션/resting orders 로딩 (중복 방지)

**2. Spread Scanner** — 크립토 단기
- Binance spot → sigmoid 확률 → Kalshi와 비교
- **NO-only** 모드 (백테스트 최적화)
- 5%+ 엣지, 12시간 이내 settle, 리밋 오더
- 백테스트: 459 trades, 50.5% win, $1K→$2.4K/월, DD 33%

**3. Sports Trader** — NBA/MLB/NHL
- The Odds API 컨센서스 오즈 vs Kalshi 가격
- 8%+ 엣지, 134+ 게임 마켓, 리밋 오더
- 매일/매주 settle → 빠른 회전

### PAPER (4개 — 시뮬레이션)

4. **Market Maker** — 양방향 호가, 동적 스프레드 (2-4c)
5. **Shutdown Fade** — 셧다운 NO 매수, 110건 $18.3K
6. **Sum Arb** — YES+NO < $1 아비트라지 (기회 없음)
7. **Maker Pre-Order** — 2-30분 마켓 양방향 리밋

---

## Per-Strategy Live/Paper Control

```env
TRADING_MODE=paper                    # 기본 Paper
LIVE_STRATEGIES=mentions_trader,spread_scanner,sports_trader  # 이것만 Live
```

Live 전략은:
- 실제 Kalshi API로 주문
- 시작 시 실제 잔고와 bankroll 싱크
- 기존 포지션/resting orders 로딩 (중복 방지)
- 주문 전 실제 Kalshi 잔고 확인

---

## Live Investment Status

| | 금액 |
|---|---|
| 총 입금 | $225 |
| Live 잔고 | $51.65 |
| 멘션 포지션 (12개) | ~$173 |
| 수수료 지출 | ~$4.77 |
| Settle 예정 | 4/30 Fed 기자회견 |

---

## Dashboard

http://18.223.21.52:8080

**헤더**: Live Balance, Live Positions, Live Fees, Live Exposure, Paper PnL, Total Trades
**전략 카드**: 7개 전략 전부 표시, LIVE/PAPER 뱃지, Realized PnL, Win/Loss, 승률
**Live Positions**: Kalshi 실제 포지션 테이블 (ticker, side, quantity, exposure, fees)
**Trade History**: 전략별 필터, Live/Paper 구분
**Open Orders**: 최근 100개 (메모리 최적화)

API 엔드포인트:
- `/api/status` — bankroll, daily PnL, trade count
- `/api/live` — Kalshi 실제 잔고 + 포지션
- `/api/strategy-status` — 전략별 LIVE/PAPER
- `/api/performance` — 7개 전략 성과
- `/api/settlement` — Live/Paper 분리된 PnL
- `/api/trades`, `/api/orders`

---

## Backtest Results (Spread Scanner)

| Config | Trades | Win% | $1K→ | Max DD |
|--------|--------|------|------|--------|
| Baseline | 877 | 49.7% | $7,168 | 75.8% |
| **Combo #7 (적용됨)** | 459 | 50.5% | $2,420 | 33.0% |

Combo #7: NO-only + limit fee + position 2%

---

## Deployment

```bash
# SSH
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52

# 상태
sudo systemctl status kalshi-bot

# 로그
journalctl -u kalshi-bot -f

# 코드 업데이트 + 재시작
cd /home/ubuntu/kalshi-arb && git checkout -- . && git pull && sudo systemctl restart kalshi-bot

# 현황
curl localhost:8080/api/status
curl localhost:8080/api/live
curl localhost:8080/api/settlement
```

---

## Safety Mechanisms

1. **전략별 Live/Paper** — `LIVE_STRATEGIES` env var로 개별 전환
2. **실제 잔고 체크** — Live 주문 전 Kalshi API로 잔고 확인
3. **포지션 로딩 (`__init__`)** — 생성자에서 Kalshi 전체 포트폴리오 로딩 (중복 방지). `scan_and_execute` 전에 완료되어 race condition 없음
4. **Bankroll 싱크** — 시작 시 Kalshi 잔고로 자동 동기화
5. **Circuit Breaker** — 일일 $200 손실 한도, 연속 5패 시 15분 정지
6. **리밋 오더 전체** — Maker: -10% vs Taker: -32%
7. **극단 가격 필터** — 15c 미만/85c 초과 스킵

---

## Key Learnings

### 버그에서 배운 것
1. **API 필드명 오류** → 모든 가격 None → 거래 0건 수 시간 방치
2. **Sigmoid 수식 오류** → 확률 전부 ~0 → 잘못된 거래
3. **v1 spread scanner** → 7c YES 매수 → 20/22 패배 (-$197)
4. **재시작 시 포지션 미로딩** → 중복 투자 $73 (3번 수정 후 해결)
5. **대시보드 39K orders** → 브라우저 메모리 폭주
6. **Live/Paper PnL 미분리** → 혼란
7. **UNIQUE constraint** → Kalshi 포트폴리오의 position=0 마켓도 스킵 필요
8. **Race condition** → `scan_and_execute`에서 포지션 로딩 시 첫 사이클이 먼저 실행 → `__init__`으로 이동

### Top 트레이더 리서치
- catboyautist ($1K→$300K): 멘션 트랜스크립트 + 공격적 사이징
- Foster ($2.5K→$500K): 멘션 전문, 100x NATO trade
- theduckguesses ($100→$240K): 전략 비공개, 극도로 공격적 복리
- Karl Whelan 연구: Maker -10% vs Taker -32%

---

## Git History (주요)

| Commit | Description |
|--------|-------------|
| `63421ba` | 포트폴리오 로딩을 __init__으로 이동 (race condition 해결) |
| `86a4396` | 전체 포트폴리오 스킵 (position=0 마켓 포함) |
| `10e1f89` | resting orders 로딩 + bankroll 싱크 |
| `f96a334` | 대시보드 Live/Paper PnL 분리 |
| `8cd6572` | 대시보드: Live 잔고, 포지션, LIVE/PAPER 뱃지 |
| `e43fc4b` | 중복 주문 방지 (_traded_tickers) |
| `901c2e6` | 실제 잔고 체크 후 Live 주문 |
| `6c2f37c` | 전략별 Live/Paper 모드 (LIVE_STRATEGIES) |
| `5aaa4cf` | 공격적 멘션 사이징 (15% bankroll on 50%+ edge) |
| `f8da76f` | 스포츠 트레이더 (NBA/MLB/NHL + The Odds API) |
| `397485f` | 백테스트 combo #7 적용 (NO-only, 2%, 5% edge) |
| `cddab7f` | Critical 버그 수정 (sigmoid, settlement, bankroll floor) |
| `d8cca1a` | 크립토 단기 마켓 전환 (Binance 확률) |
| `0817d08` | 4개 신규 전략 + 인프라 확장 |

---

## Next Steps

1. **4/30 Fed 기자회견** — 멘션 12개 포지션 settle → 수익 확인
2. **크립토/스포츠 Live 거래 모니터링** — $51.65로 매일 거래
3. **수익 시 스케일업** — 추가 입금 → 더 많은 마켓
4. **멘션 빈도 갱신** — 6월 기자회견 전 최신 트랜스크립트 반영
5. **문화/엔터 마켓** — Spotify/Billboard (GaetenD $530K 전략)
6. **Kalshi-Polymarket 아비트라지** — 크로스 플랫폼 (15-20% 발생)
