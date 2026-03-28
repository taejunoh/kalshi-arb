# Kalshi Trading Bot — Handover Document

## Project Overview

Kalshi 예측 시장의 가격 비효율성을 이용한 자동 트레이딩 봇. 6개 전략 + 자동 Settlement PnL 추적.

- **Repo**: `github.com/taejunoh/kalshi-arb`
- **언어**: Python 3.11
- **상태**: AWS EC2에서 Paper Trading 24/7 실행 중
- **서버**: `18.223.21.52` (us-east-2, t3.micro)
- **대시보드**: http://18.223.21.52:8080
- **테스트**: 94개 전체 통과
- **Bankroll**: $1,000 (paper)

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (6개 전략 + settlement)
├── core/
│   ├── config.py                    # .env 기반 설정, 전략별 파라미터
│   ├── database.py                  # SQLite — 거래/주문/settlement 저장
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL 실시간 가격
│   ├── kalshi_client.py             # REST API v2 (크립토 + 전체 마켓 + 오더북 레벨)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 타겟 시리즈(30초)
│   ├── order_manager.py             # Paper/Live 주문, 포지션 추적, 리쿼팅
│   ├── settlement.py                # 마켓 settle 감지 → PnL 자동 계산
│   └── external_data.py             # Fed 트랜스크립트 빈도 데이터
├── strategies/
│   ├── sum_arb.py                   # Yes/No Sum Arbitrage
│   ├── maker_preorder.py            # Maker Pre-Order (5분 마켓)
│   ├── market_maker.py              # 양방향 마켓 메이킹 (30초 리쿼팅)
│   ├── shutdown_fade.py             # 정부 셧다운 페이드 (3단계 스케일인)
│   ├── spread_scanner.py            # VWAP 스프레드 스캐너 (리밋 오더)
│   └── mentions_trader.py           # Fed 멘션 트레이더 (트랜스크립트 분석)
├── monitoring/
│   ├── logger.py                    # 루트 로거, 일별 로테이션
│   ├── notifier.py                  # Telegram 알림
│   └── dashboard/
│       ├── api.py                   # FastAPI — /status, /trades, /orders, /performance, /settlement
│       └── static/index.html        # 대시보드 UI (PnL, 승률, settlement 현황)
├── scripts/
│   └── build_mention_data.py        # Fed 트랜스크립트 파싱 → 빈도 JSON 생성
├── deploy/
│   ├── setup.sh                     # EC2 초기 설정 스크립트
│   ├── kalshi-bot.service           # systemd 서비스 파일
│   └── README.md                    # 배포 가이드
├── data/
│   └── mention_frequencies.json     # Powell 기자회견 키워드 빈도 (8개 트랜스크립트 기반)
├── tests/                           # 19개 파일, 94개 테스트
└── .env                             # 환경변수 (git 제외)
```

---

## Strategies (6개)

### 1. Sum Arb (`sum_arb.py`)
YES + NO < $1.00 → 양쪽 매수. 크립토 300개 마켓 스캔. 현재 기회 없음.

### 2. Maker Pre-Order (`maker_preorder.py`)
5분 마켓에 양쪽 리밋 오더 배치.

### 3. Market Maker (`market_maker.py`)
양방향 호가, 30초 리쿼팅, 인벤토리 스큐. @debl00b 영감.

### 4. Shutdown Fade (`shutdown_fade.py`)
셧다운 마켓 NO 매수, 3단계 스케일인. **70건, $11,667 규모**.

### 5. Spread Scanner (`spread_scanner.py`)
VWAP 기반 스프레드 탐지 → **리밋 오더** (Maker). 극단 가격 필터 (15c~85c), 양방향 오더북 필수, 최소 10% 엣지. **112건, $1,459 규모**. Settled: 2W/20L, -$197.

### 6. Mentions Trader (`mentions_trader.py`) ⭐ 최고 알파
Fed 기자회견 트랜스크립트 8개 분석 → 48개 KXFEDMENTION 마켓 자동 트레이딩. **리밋 오더** (Maker).
- Ticker suffix 매핑 (KXFEDMENTION-26APR-AI → "ai" → 빈도 12%)
- 23/48 마켓에서 15%+ 엣지 발견
- Top 엣지: AI(마켓 83c vs 실제 12%), Gas(70c vs 0%), Volatility(29c vs 100%)
- `scripts/build_mention_data.py`로 빈도 데이터 갱신 가능

---

## Settlement System (`core/settlement.py`)

Paper 거래의 실제 수익을 자동 계산:
- 60초마다 Kalshi API에서 마켓 settle 여부 확인 (`status: "finalized"`)
- 승리: `pnl = size × (1-price)/price - fee`
- 패배: `pnl = -size`
- DB 업데이트 + bankroll 반영
- `/api/settlement` 엔드포인트로 실시간 확인

**현재 결과** (2026-03-28):
| | 값 |
|---|---|
| Settled | 22건 |
| Unsettled | 160건 |
| Realized PnL | **-$197.45** |
| 승률 | 2W/20L (spread_scanner) |

---

## Top Trader 리서치 & 전략 튜닝

**학술 연구 (Karl Whelan, 300K+ 계약 분석):**
- Taker 평균 수익: -32%, Maker: -10% → **모든 전략 리밋 오더로 전환 완료**
- Favorite-longshot bias: 5c 계약은 2%만 승리 → **극단 가격 필터 추가**

**Top 트레이더 전략 적용:**
- Foster ($500K+), catboyautist ($300K+): 트랜스크립트 빈도 분석 → **mentions_trader 구현**
- @debl00b ($160M vol): 알고리즘 마켓 메이킹 → **market_maker 구현**
- @GaetenD ($530K): 데이터 기반 방향성 베팅 → **shutdown_fade 구현**

**수행한 튜닝:**
- Spread scanner: 극단 가격 필터 (15c~85c), 양방향 오더북 2레벨 필수, 최소 10% 엣지
- Spread scanner + Mentions trader: 마켓 오더 → 리밋 오더 전환 (Maker)
- 전체 마켓 스캔 836K → 타겟 시리즈 295개 (330배 성능 개선)

---

## Deployment (AWS EC2)

**서버 정보:**
- IP: `18.223.21.52`
- Instance: t3.micro, Ubuntu 24.04, us-east-2
- Service: `kalshi-bot.service` (systemd, 자동 재시작)

**운영 명령어:**
```bash
# SSH 접속
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52

# 봇 상태 확인
sudo systemctl status kalshi-bot

# 로그 확인
journalctl -u kalshi-bot -f

# 재시작
sudo systemctl restart kalshi-bot

# 코드 업데이트
cd /home/ubuntu/kalshi-arb && git pull && sudo systemctl restart kalshi-bot

# 거래 현황
curl localhost:8080/api/status
curl localhost:8080/api/settlement
```

**대시보드**: http://18.223.21.52:8080
- Realized PnL, Settled/Pending, 전략별 승률
- 실시간 거래 내역, 오픈 주문

---

## Configuration (`.env`)

| 변수 | 설명 | 현재값 |
|------|------|--------|
| `TRADING_MODE` | paper / live | paper |
| `INITIAL_BANKROLL` | 초기 자본 | 1000 |
| `SHUTDOWN_FADE_ENABLED` | 셧다운 페이드 | true |
| `SPREAD_SCANNER_ENABLED` | 스프레드 스캐너 | true |
| `SPREAD_SCANNER_INTERVAL` | 스캔 간격 | 60 |
| `SPREAD_SCANNER_MIN_WIDTH` | 최소 스프레드 | 0.05 |
| `SPREAD_SCANNER_MIN_VOLUME` | 최소 거래량 | 100 |
| `MM_ENABLED` | 마켓 메이커 | true |
| `MM_MIN_VOLUME` | MM 최소 거래량 | 100 |
| `MENTIONS_ENABLED` | 멘션 트레이더 | true |

---

## Tests (94개)

```bash
python -m pytest tests/ -v
```

19개 파일: config, database, price_feed, kalshi_client, order_manager, market_scanner, sum_arb, maker_preorder, market_maker, shutdown_fade, spread_scanner, mentions_trader, settlement, logger, notifier, dashboard_api, main, integration, preflight

---

## Git History (주요 커밋)

| Commit | Description |
|--------|-------------|
| `7788dc8` | EC2 배포 스크립트 (setup.sh, systemd, README) |
| `502abca` | 멘션 트레이더 트랜스크립트 빈도 데이터 + Maker 전환 |
| `ebf8509` | 스프레드 스캐너 극단 가격 필터, 10% 최소 엣지 |
| `ffcea19` | 대시보드 Realized PnL, 승률, settlement 표시 |
| `c9b787f` | Settlement 시뮬레이션 (자동 PnL 계산) |
| `ac33699` | paper_open 주문 대시보드 표시 |
| `18b8994` | 스프레드 스캐너 1시간 쿨다운 (영구 블록 → 시간 기반) |
| `03c858b` | 스프레드 스캐너 거래된 티커 스킵 + 디버그 로깅 |
| `7879d47` | 대시보드 워치독 자동 재시작 |
| `be47e66` | 마켓 스캔 330배 성능 개선 (836K→295, 10분→2초) |
| `0817d08` | 4개 신규 전략 + 인프라 확장 (88 tests) |
| `56e6d25` | API 필드명 수정 (yes_ask_dollars), 관측성 추가 |

---

## Known Issues

1. **Spread scanner 손실 중** — 22건 settled에서 -$197 (2W/20L). 극단 가격 필터 + Maker 전환으로 개선 중, 추가 settle 결과 관찰 필요
2. **Paper 모드 리밋 오더 체결 미시뮬레이션** — 마켓 메이커/멘션 리밋 주문이 paper_open 상태로 유지
3. **Kalshi 계정 잔고 $0.06** — Live 전환 전 입금 필요
4. **대시보드 0.0.0.0 바인딩** — 서버에서만 적용, 로컬 코드는 127.0.0.1 유지

---

## Future Work

1. **Settlement 결과 분석** — 전략별 수익성 평가 후 파라미터 조정
2. **멘션 빈도 데이터 갱신** — 2025-2026 추가 트랜스크립트 반영 (`python scripts/build_mention_data.py`)
3. **Live Trading 전환** — Kalshi 입금 → `TRADING_MODE=live` → 소액 시작
4. **Kalshi-Polymarket 아비트라지** — 크로스 플랫폼 가격 차이 활용 (15-20% 발생)
5. **날씨 마켓 모델** — GFS 앙상블 예측 vs Kalshi 온도 마켓
6. **리밋 오더 체결 시뮬레이션** — Paper 모드 현실성 향상
7. **WebSocket 스트림** — REST 폴링 → 실시간 스트림으로 레이턴시 개선
