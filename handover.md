# Kalshi Trading Bot — Handover Document

## Project Overview

Kalshi 예측 시장 자동 트레이딩 봇. 7개 전략 (3 Live + 4 Paper), 실시간 대시보드, 자동 Settlement.

- **Repo**: `github.com/taejunoh/kalshi-arb`
- **언어**: Python 3.11
- **서버**: AWS EC2 `18.223.21.52` (us-east-2, t3.micro)
- **대시보드**: http://18.223.21.52:8080
- **테스트**: 95개 전체 통과
- **총 입금**: $425 ($100 + $100 + $25 + $200)
- **Live 잔고**: $200.00
- **Live 포지션**: 14개 멘션 마켓 (~$211 투자)
- **Odds API**: 20K plan ($30/월), 키: `1b51233d...`

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (7개 전략 + settlement)
├── core/
│   ├── config.py                    # .env 설정, LIVE_STRATEGIES, SPREAD_SCANNER_MIN_EDGE
│   ├── database.py                  # SQLite — 거래/주문/settlement
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL
│   ├── kalshi_client.py             # REST API v2 (마켓 + 오더북 + 잔고 + 포지션 + resting orders)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 타겟 시리즈(30초)
│   ├── order_manager.py             # 전략별 Live/Paper, yes_price 변환, 잔고 체크
│   ├── settlement.py                # 마켓 settle 감지 → PnL 자동 계산
│   └── external_data.py             # Fed 트랜스크립트 빈도 데이터
├── strategies/
│   ├── mentions_trader.py           # ⭐ LIVE — Fed 멘션 (3중 중복 방지)
│   ├── spread_scanner.py            # ⭐ LIVE — 크립토 단기 (파일 영구 저장)
│   ├── sports_trader.py             # ⭐ LIVE — 스포츠 (파일 영구 저장, 30분 스캔)
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
├── data/
│   ├── mention_frequencies.json     # Powell 키워드 빈도 데이터
│   ├── mentions_traded_tickers.json # 멘션 중복 방지 (영구 저장)
│   ├── spread_traded_tickers.json   # 크립토 중복 방지 (영구 저장)
│   ├── sports_traded_tickers.json   # 스포츠 중복 방지 (영구 저장)
│   └── bot.db                       # SQLite 거래/주문 DB
├── scripts/
│   ├── build_mention_data.py        # Fed 트랜스크립트 파싱
│   └── backtest_spread_scanner.py   # 크립토 백테스트
├── deploy/                          # EC2 배포 (setup.sh, systemd)
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
- 14개 Live 포지션 보유 (~$211)
- Settle: **4/30 Fed 기자회견** 후
- **3중 중복 방지**: 파일 + Kalshi API (positions + resting orders) + 메모리

**2. Spread Scanner** — 크립토 단기
- Binance spot → sigmoid 확률 → Kalshi와 비교
- **NO-only** 모드 (백테스트 최적화)
- 엣지 임계값: `SPREAD_SCANNER_MIN_EDGE` env var (현재 3%)
- 12시간 이내 settle, 리밋 오더
- 파일 영구 저장으로 중복 방지

**3. Sports Trader** — NBA/MLB/NHL
- The Odds API 컨센서스 오즈 vs Kalshi 가격 (20K plan, $30/월)
- 8%+ 엣지, 30분 스캔 간격 (`SPORTS_SCAN_INTERVAL=1800`)
- 파일 영구 저장으로 중복 방지

### PAPER (4개 — 시뮬레이션)

4. **Market Maker** — 양방향 호가, 동적 스프레드 (2-4c)
5. **Shutdown Fade** — 셧다운 NO 매수
6. **Sum Arb** — YES+NO < $1 아비트라지
7. **Maker Pre-Order** — 2-30분 마켓 양방향 리밋

---

## Per-Strategy Live/Paper Control

```env
TRADING_MODE=paper                    # 기본 Paper
LIVE_STRATEGIES=mentions_trader,spread_scanner,sports_trader
```

Live 전략은:
- 실제 Kalshi API로 주문
- 시작 시 실제 잔고와 bankroll 싱크
- 기존 포지션/resting orders 로딩 (중복 방지)
- 주문 전 실제 Kalshi 잔고 확인
- `_traded_tickers` 파일 영구 저장

---

## Live Investment Status

| | 금액 |
|---|---|
| 총 입금 | $425 |
| Live 잔고 | $200.00 |
| 멘션 포지션 (14개) | ~$211 |
| 수수료 지출 | ~$10.56 |
| Settle 예정 | 4/30 Fed 기자회견 |
| Odds API 비용 | $30/월 |

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

## Critical Bug Fixes (2026-03-31)

### 1. yes_price NO 주문 가격 뒤집힘 (CRITICAL)
- **문제**: Kalshi API의 `yes_price`는 항상 YES 가격인데, NO 주문 시 NO 가격을 그대로 보냄
- **증상**: GAS NO 99c 주문 → yes_price=99 → 실제 NO 비용 1c (절대 체결 안 됨)
- **영향**: 전 전략의 모든 NO 리밋 오더 가격이 뒤집혀 있었음
- **수정**: `order_manager.py` — NO일 때 `yes_price = 100 - price_cents`
- **조치**: 잘못된 resting orders 24개 전부 취소

### 2. 중복 주문으로 $48 손실
- **문제**: `_traded_tickers.add(ticker)`가 주문 **이후** try 블록 안에 있어서, DB UNIQUE 에러 시 ticker 미등록 → 5분마다 반복 주문
- **증상**: SHOC에 $47.51, GOOD에 $1.14 중복 투자 ($51.65 → $3.00)
- **수정**: `_traded_tickers` 마킹을 주문 **전**으로 이동 (전 전략)

### 3. 재시작 시 중복 방지 없음
- **문제**: `_traded_tickers`가 메모리 only → 봇 재시작하면 빈 상태로 시작 → 같은 마켓에 또 주문
- **수정**: 3중 방지 (mentions) / 파일 영구 저장 (spread, sports)
  - `data/mentions_traded_tickers.json` (파일 + Kalshi positions + resting orders)
  - `data/spread_traded_tickers.json`
  - `data/sports_traded_tickers.json`

### 4. `except Exception: pass` 에러 삼킴
- **문제**: init에서 positions/resting orders 로딩 실패 시 에러를 무시하고 빈 상태로 진행
- **수정**: `logger.error(CRITICAL: ...)` 로 명시적 로깅

### 5. sports_trader 홈팀 감지 ternary 버그
- **문제**: Python ternary 연산자 우선순위로 home/away 확률이 뒤바뀔 수 있음
- **수정**: 명시적 if/else 블록으로 재작성

### 6. DB record_order 에러 전파
- **문제**: `_live_limit_order`에서 DB 에러가 exception으로 전파 → 전략의 try/except에서 catch → ticker 미등록
- **수정**: `_live_limit_order` 내에서 DB 에러를 catch + warning 로그

---

## Safety Mechanisms

1. **3중 중복 방지** (mentions) — 파일 + Kalshi API + 메모리
2. **파일 영구 저장** (spread, sports) — 재시작해도 복구
3. **주문 전 마킹** — 모든 전략에서 `_traded_tickers`를 주문 전에 갱신
4. **yes_price 변환** — NO 주문 시 `100 - price_cents` 자동 변환
5. **실제 잔고 체크** — Live 주문 전 Kalshi API로 잔고 확인
6. **Bankroll 싱크** — 시작 시 Kalshi 잔고로 자동 동기화
7. **Circuit Breaker** — 일일 $200 손실 한도, 연속 5패 시 15분 정지
8. **리밋 오더 전체** — Maker: -10% vs Taker: -32%
9. **극단 가격 필터** — 5c 미만 스킵
10. **DB 에러 방어** — `_live_limit_order`에서 DB 실패 catch

---

## Key Learnings

### 버그에서 배운 것
1. **yes_price 뒤집힘** → NO 주문 전부 잘못된 가격, 24개 resting orders 1센트로 생성 → 전부 취소
2. **중복 주문 $48 손실** → `_traded_tickers`를 주문 후가 아닌 전에 마킹해야 함
3. **재시작 15회** → 배포/수동 재시작이 원인, 매 재시작마다 중복 주문 발생
4. **except: pass 안티패턴** → 에러를 삼키면 문제가 보이지 않음
5. **API 필드명 오류** → 모든 가격 None → 거래 0건 수 시간 방치
6. **Sigmoid 수식 오류** → 확률 전부 ~0 → 잘못된 거래
7. **v1 spread scanner** → 7c YES 매수 → 20/22 패배 (-$197)
8. **Odds API 무료 티어** → 500건/월, 5분 스캔으로 14시간 만에 소진

### Top 트레이더 리서치
- catboyautist ($1K→$300K): 멘션 트랜스크립트 + 공격적 사이징
- Foster ($2.5K→$500K): 멘션 전문, 100x NATO trade
- theduckguesses ($100→$240K): 전략 비공개, 극도로 공격적 복리
- Karl Whelan 연구: Maker -10% vs Taker -32%

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

## Git History (주요)

| Commit | Description |
|--------|-------------|
| `ef6db1c` | CRITICAL: yes_price 뒤집힘 수정 + spread/sports 파일 영구 저장 + 홈팀 감지 수정 |
| `35a0269` | spread scanner 엣지 임계값 env var화 (SPREAD_SCANNER_MIN_EDGE) |
| `7d2b9b7` | mentions _traded_tickers 파일 영구 저장 + init 에러 로깅 |
| `f5f08fc` | 중복 주문 방지: 주문 전 마킹 + resting orders 로딩 + DB 에러 catch |
| `63421ba` | 포트폴리오 로딩을 __init__으로 이동 (race condition 해결) |
| `86a4396` | 전체 포트폴리오 스킵 (position=0 마켓 포함) |
| `10e1f89` | resting orders 로딩 + bankroll 싱크 |

---

## .env (EC2 현재)

```env
KALSHI_API_KEY_ID=89aaa939-...
KALSHI_RSA_PRIVATE_KEY_PATH=kalshi_private_key.pem
TRADING_MODE=paper
LIVE_STRATEGIES=mentions_trader,spread_scanner,sports_trader
MAX_SINGLE_TRADE_PCT=0.10
SPREAD_SCANNER_ENABLED=true
SPREAD_SCANNER_INTERVAL=60
SPREAD_SCANNER_MIN_EDGE=0.03
SPORTS_ENABLED=true
SPORTS_SCAN_INTERVAL=1800
ODDS_API_KEY=1b51233d6560646844ba7da10199ed06
MENTIONS_ENABLED=true
```

---

## Next Steps

1. **4/30 Fed 기자회견** — 멘션 14개 포지션 settle → 수익 확인
2. **크립토/스포츠 Live 거래 모니터링** — $200으로 매일 거래
3. **수익 시 스케일업** — 추가 입금 → 더 많은 마켓
4. **멘션 빈도 갱신** — 6월 기자회견 전 최신 트랜스크립트 반영
5. **문화/엔터 마켓** — Spotify/Billboard (GaetenD $530K 전략)
6. **Kalshi-Polymarket 아비트라지** — 크로스 플랫폼 (15-20% 발생)
