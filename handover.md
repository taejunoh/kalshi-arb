# Kalshi Arbitrage Bot — Handover Document

## Project Overview

Kalshi의 YES/NO 컨트랙트 가격 비효율성을 이용한 자동 차익거래 봇. 두 가지 전략을 통해 무위험 또는 저위험 수익을 추구한다.

- **위치**: `/Users/taejunoh/Desktop/LFG/polymarket-arb/`
- **언어**: Python 3.11
- **상태**: 개발 완료, Paper Trading 준비 완료
- **테스트**: 53개 전체 통과

---

## Architecture

```
polymarket-arb/
├── main.py                          # 봇 오케스트레이터 (진입점)
├── core/
│   ├── config.py                    # .env 기반 설정, 수수료 계산
│   ├── database.py                  # SQLite (aiosqlite) — 거래/주문 저장
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL 실시간 가격
│   ├── kalshi_client.py             # REST API v2 래퍼, 마켓 필터링, 잔고 확인
│   ├── market_scanner.py            # 활성 마켓 폴링 (5초), 캐싱, 자본 락
│   └── order_manager.py             # Paper/Live 주문 실행, 서킷 브레이커
├── strategies/
│   ├── sum_arb.py                   # Yes/No Sum Arbitrage 전략
│   └── maker_preorder.py            # Maker Pre-Order 전략 (5분 마켓)
├── monitoring/
│   ├── logger.py                    # 일별 로그 로테이션 + CSV 트레이드 로그
│   ├── notifier.py                  # Telegram 알림 (속도 제한 포함)
│   └── dashboard/
│       ├── api.py                   # FastAPI — /status, /trades, /orders, /performance
│       └── static/index.html        # 다크 테마 대시보드 UI
├── tests/                           # 14개 테스트 파일, 53개 테스트
├── docs/setup-guide.md              # Kalshi 계정 설정 가이드
├── requirements.txt                 # 의존성
└── .env.example                     # 환경변수 템플릿
```

---

## Strategies

### 1. Yes/No Sum Arbitrage (`strategies/sum_arb.py`)

**원리**: YES 가격 + NO 가격 < $1.00 (수수료 포함)이면 양쪽 모두 매수. 결과에 관계없이 차익 발생.

- 마켓 스캐너가 5초 간격으로 활성 마켓 폴링
- `best_ask_yes + best_ask_no < 1.0 - total_fees` 조건 충족 시 시그널 발생
- Kelly fraction 기반 포지션 사이징
- 최소 수익률 임계값: 0.5% (설정 가능)

### 2. Maker Pre-Order (`strategies/maker_preorder.py`)

**원리**: 5분 마켓 오픈 전에 양쪽 리밋 오더 배치. 합산 < $1.00이면 차익 확보.

- Binance 가격 피드로 예상 결과 방향 파악
- 오픈 전 YES/NO 양쪽에 maker 주문 배치
- 마켓 종료 시 미체결 주문 자동 취소
- 주문 모니터링 루프 (2초 간격)

---

## Key Components

### Config (`core/config.py`)
- `.env` 파일에서 모든 설정 로드
- Kalshi 수수료 계산 (컨트랙트당 $0.07 고정)
- `TRADING_MODE=paper|live` 로 모드 전환

### Order Manager (`core/order_manager.py`)
- **Paper Mode**: 시뮬레이션 주문 실행, DB에 기록
- **Live Mode**: `cryptography + httpx`를 통한 실제 주문
- **Circuit Breaker**: 일일 $200 손실 한도, 연속 5회 손실 한도
- **Kelly Fraction**: 최적 베팅 비율 계산

### Market Scanner (`core/market_scanner.py`)
- 5초 간격 활성 마켓 폴링
- 30초 캐싱으로 API 호출 최소화
- Capital Lock: 중복 진입 방지

### Price Feed (`core/price_feed.py`)
- Binance WebSocket으로 BTC/ETH/SOL 실시간 가격
- 자동 재연결 (최대 5회, 지수 백오프)
- 15분 캔들 추적

### Database (`core/database.py`)
- aiosqlite 기반 비동기 SQLite
- trades 테이블: 체결 기록
- orders 테이블: 주문 상태 추적

### Pre-flight Checks (`main.py`)
Live 모드 전환 시 자동 검증:
1. API 키 존재 확인
2. Kalshi 계정 연결 확인
3. 잔고 확인 (최소 $10)
4. REST API v2 연결 테스트

---

## Monitoring Stack

### Logs (`monitoring/logger.py`)
- `logs/bot_YYYY-MM-DD.log` — 일별 로테이션
- `logs/trades.csv` — 모든 거래 CSV 기록
- DEBUG/INFO/WARNING/ERROR 레벨

### Telegram (`monitoring/notifier.py`)
- 거래 체결 알림
- 에러 알림 (ERROR 레벨 이상)
- 일일 성과 요약 (매일 자정)
- 속도 제한: 분당 20건

### Web Dashboard (`monitoring/dashboard/`)
- FastAPI 서버 (포트 8080, 봇과 통합)
- 실시간 봇 상태, 최근 거래, 주문 목록
- 성과 차트 (승률, PnL 추이)
- 다크 테마 UI, 10초 자동 새로고침

---

## How to Run

### 1. 환경 설정
```bash
cd /Users/taejunoh/Desktop/LFG/polymarket-arb
cp .env.example .env
# .env 파일에 KALSHI_API_KEY_ID, KALSHI_RSA_PRIVATE_KEY_PATH 등 입력
```

### 2. 의존성 설치
```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Paper Trading 실행
```bash
# .env에서 TRADING_MODE=paper 확인
source venv/bin/activate
python main.py
```

### 4. 대시보드 확인
브라우저에서 `http://localhost:8080` 접속

### 5. Live Trading 전환
```bash
# .env 수정
TRADING_MODE=live
KALSHI_API_KEY_ID=your_key_id
KALSHI_RSA_PRIVATE_KEY_PATH=/path/to/your/private_key.pem
```

---

## Git History (20 commits)

| Commit | Description |
|--------|-------------|
| `c6db6a0` | 프로젝트 스캐폴딩 |
| `5ea4a63` | Config 모듈 |
| `f4b3fc9` | Logger 모듈 |
| `9a9956c` | Database 모듈 |
| `6ae3b5f` | Binance Price Feed |
| `af0248b` | Kalshi REST API v2 클라이언트 |
| `3668f64` | Order Manager + Circuit Breaker |
| `2b5bb4e` | Market Scanner |
| `eeb9382` | Sum Arb 전략 |
| `230b28f` | Main 오케스트레이터 |
| `1d007d9` | Setup Guide |
| `dd8d269` | 통합 테스트 |
| `6e0c497` | Telegram 알림 |
| `997a8a1` | Maker Pre-Order 전략 |
| `2dee24e` | Maker Pre-Order + Notifier 통합 |
| `146dd17` | Phase 2 통합 테스트 |
| `7134823` | Dashboard 백엔드 |
| `381de8b` | Dashboard 프론트엔드 |
| `7df76a8` | Dashboard 메인 봇 통합 |
| `5084f76` | Pre-flight checks |

---

## Configuration Reference (`.env`)

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `TRADING_MODE` | paper / live | paper |
| `INITIAL_BANKROLL` | 초기 자본 (USD) | 100 |
| `KALSHI_API_KEY_ID` | Kalshi API 키 ID | — |
| `KALSHI_RSA_PRIVATE_KEY_PATH` | RSA 프라이빗 키 PEM 파일 경로 | — |
| `KALSHI_API_BASE_URL` | Kalshi API 베이스 URL | https://trading-api.kalshi.com |
| `KALSHI_FEE_PER_CONTRACT` | 컨트랙트당 수수료 | 0.07 |
| `SUM_ARB_MIN_GAP` | Sum Arb 최소 갭 | 0.03 |
| `MAKER_TARGET_SUM` | Maker 타겟 합산 | 0.96 |
| `KELLY_FRACTION` | Kelly 비율 | 0.25 |
| `MAX_SINGLE_TRADE_PCT` | 최대 단일 거래 비율 | 0.10 |
| `MAX_DIRECTIONAL_LOSS` | 최대 방향성 손실 | 50 |
| `MAX_DAILY_LOSS` | 일일 손실 한도 | 200 |
| `MAX_CONSECUTIVE_LOSSES` | 연속 손실 한도 | 5 |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 봇 토큰 | — |
| `TELEGRAM_CHAT_ID` | 텔레그램 채팅 ID | — |

---

## Tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

14개 테스트 파일, 53개 테스트 전체 통과:
- `test_config.py` — 설정 로드, 수수료 계산
- `test_database.py` — DB CRUD 연산
- `test_price_feed.py` — WebSocket 연결, 캔들 추적
- `test_kalshi_client.py` — API 래퍼, 마켓 필터링
- `test_order_manager.py` — 주문 실행, 서킷 브레이커
- `test_market_scanner.py` — 마켓 폴링, 캐싱
- `test_sum_arb.py` — Sum Arb 시그널 감지
- `test_maker_preorder.py` — Pre-Order 로직
- `test_logger.py` — 로그 로테이션
- `test_notifier.py` — Telegram 알림
- `test_dashboard_api.py` — API 엔드포인트
- `test_main.py` — 메인 루프
- `test_integration.py` — 통합 테스트
- `test_preflight.py` — Pre-flight 검증

---

## Future Work

1. **Paper Trading 검증** — 실행 후 전략 파라미터 튜닝
2. **Live Trading 전환** — Pre-flight 통과 후 소액으로 시작
3. **Rust 포팅** — 레이턴시 최적화 (핫 패스 우선)
4. **추가 전략** — 크로스 마켓 아비트라지, 이벤트 기반 전략
