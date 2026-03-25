# Kalshi Arbitrage Bot — Handover Document

## Project Overview

Kalshi의 YES/NO 컨트랙트 가격 비효율성을 이용한 자동 차익거래 봇. 두 가지 전략을 통해 무위험 또는 저위험 수익을 추구한다.

- **위치**: `/Users/taejunoh/Desktop/LFG/kalshi-arb/`
- **언어**: Python 3.11
- **상태**: Paper Trading 실행 중 ($1,000 bankroll)
- **테스트**: 63개 전체 통과

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (진입점)
├── core/
│   ├── config.py                    # .env 기반 설정, 수수료 계산
│   ├── database.py                  # SQLite (aiosqlite) — 거래/주문 저장
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL 실시간 가격
│   ├── kalshi_client.py             # REST API v2 래퍼, 마켓 필터링, 잔고 확인
│   ├── market_scanner.py            # 활성 마켓 폴링 (5초), 60초 주기 INFO 로깅
│   └── order_manager.py             # Paper/Live 주문 실행, 서킷 브레이커
├── strategies/
│   ├── sum_arb.py                   # Yes/No Sum Arbitrage 전략 (60초 주기 스캔 요약)
│   └── maker_preorder.py            # Maker Pre-Order 전략 (5분 마켓)
├── monitoring/
│   ├── logger.py                    # 루트 로거 설정, 일별 로테이션 + CSV 트레이드 로그
│   ├── notifier.py                  # Telegram 알림 (속도 제한 포함)
│   └── dashboard/
│       ├── api.py                   # FastAPI — /status, /trades, /orders, /performance
│       └── static/index.html        # 다크 테마 대시보드 UI
├── tests/                           # 14개 테스트 파일, 63개 테스트
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
- 60초 주기로 스캔 요약 로깅 (체크한 마켓 수, 최선 갭, 티커)

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

### Kalshi Client (`core/kalshi_client.py`)
- **API 필드**: `yes_ask_dollars` / `no_ask_dollars` (문자열 소수, e.g., `"0.4500"`)
- **오더북**: `orderbook_fp` → `yes_dollars` / `no_dollars` (가격/수량 문자열 배열)
- **마켓 조회**: `series_ticker` 파라미터로 KXBTC, KXETH, KXSOL, KXXRP 직접 쿼리
- **RSA-PSS 서명**: `cryptography` 라이브러리 `padding.PSS` + `SHA256`

### Order Manager (`core/order_manager.py`)
- **Paper Mode**: 시뮬레이션 주문 실행, DB에 기록
- **Live Mode**: `cryptography + httpx`를 통한 실제 주문
- **Circuit Breaker**: 일일 $200 손실 한도, 연속 5회 손실 한도
- **Kelly Fraction**: 최적 베팅 비율 계산

### Market Scanner (`core/market_scanner.py`)
- 5초 간격 활성 마켓 폴링
- Capital Lock: 중복 진입 방지
- 60초 주기 INFO 로깅 (마켓 수, locked/available 현황)

### Price Feed (`core/price_feed.py`)
- Binance WebSocket으로 BTC/ETH/SOL 실시간 가격
- 자동 재연결 (최대 5회, 지수 백오프)
- 15분 캔들 추적

### Database (`core/database.py`)
- aiosqlite 기반 비동기 SQLite
- trades 테이블: 체결 기록
- orders 테이블: 주문 상태 추적

### Dashboard (`main.py` → `monitoring/dashboard/`)
- 데몬 스레드에서 uvicorn 실행 (asyncio 이벤트 루프 충돌 방지)
- 포트 8080 → 8081 → 8082 순차 시도
- 봇 종료 시 `should_exit` 플래그로 graceful shutdown

### Pre-flight Checks (`main.py`)
Live 모드 전환 시 자동 검증:
1. API 키 존재 확인
2. Kalshi 계정 연결 확인
3. 잔고 확인 (최소 $10)
4. REST API v2 연결 테스트

---

## Monitoring Stack

### Logs (`monitoring/logger.py`)
- 루트 로거 설정 → 모든 모듈 로거 (market_scanner, sum_arb 등) 자동 캡처
- `logs/bot.log` — 일별 로테이션, DEBUG 레벨
- `logs/trades.csv` — 모든 거래 CSV 기록
- 서드파티 노이즈 억제: aiosqlite, httpcore, httpx → WARNING 레벨

### Telegram (`monitoring/notifier.py`)
- 거래 체결 알림
- 에러 알림 (ERROR 레벨 이상)
- 일일 성과 요약 (매일 자정)
- 속도 제한: 분당 20건

### Web Dashboard (`monitoring/dashboard/`)
- FastAPI 서버 (포트 8080, 데몬 스레드)
- 실시간 봇 상태, 최근 거래, 주문 목록
- 성과 차트 (승률, PnL 추이)
- 다크 테마 UI, 10초 자동 새로고침

---

## How to Run

### 1. 환경 설정
```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
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

## Configuration Reference (`.env`)

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `TRADING_MODE` | paper / live | paper |
| `INITIAL_BANKROLL` | 초기 자본 (USD) | 100 |
| `KALSHI_API_KEY_ID` | Kalshi API 키 ID | — |
| `KALSHI_RSA_PRIVATE_KEY_PATH` | RSA 프라이빗 키 PEM 파일 경로 | — |
| `KALSHI_API_BASE_URL` | Kalshi API 베이스 URL | https://api.elections.kalshi.com |
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

14개 테스트 파일, 63개 테스트 전체 통과:
- `test_config.py` — 설정 로드, 수수료 계산
- `test_database.py` — DB CRUD 연산
- `test_price_feed.py` — WebSocket 연결, 캔들 추적
- `test_kalshi_client.py` — API 래퍼, 마켓 가격/오더북 파싱
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

## Recent Work

### 2026-03-25: API 필드 수정 및 관측성 추가

**치명적 버그 수정** — Kalshi API v2 응답 필드명 불일치 (`56e6d25`):
- `get_market_prices()`: `yes_ask` → `yes_ask_dollars` (문자열 소수, 정수 센트 아님)
- `get_order_book_depth()`: `orderbook` → `orderbook_fp`, `yes`/`no` → `yes_dollars`/`no_dollars`
- 가격 파싱: `cents / 100` → `float(dollar_string)` 으로 변경
- **영향**: 이 수정 전에는 모든 마켓에서 가격이 `None`으로 반환되어 전략이 하나도 실행되지 않음

**대시보드 포트 바인딩 수정** (`56e6d25`):
- uvicorn을 asyncio 태스크에서 실행 시 소켓 바인딩 실패 문제
- 데몬 스레드(`threading.Thread`)로 변경하여 해결
- 포트 폴백: 8080 → 8081 → 8082
- Graceful shutdown: `should_exit` 플래그 + 태스크 캔슬

**관측성 (Observability) 추가** (`56e6d25`):
- `market_scanner`: 60초 주기 INFO 로깅 — `"Scanned 300 active crypto markets (0 locked, 300 available)"`
- `sum_arb`: 60초 주기 스캔 요약 — `"[SUM ARB] Checked 300/300 markets, best gap: -0.0100 (KXETH-...)"`
- `logger.py`: 루트 로거로 변경 → 모든 모듈 로거 자동 캡처, 서드파티 노이즈 억제

**Paper Trading 시작**:
- Bankroll: $1,000 (INITIAL_BANKROLL=1000)
- 300개 활성 크립토 마켓 스캔 중
- 현재 최선 갭: -0.01 (효율적 시장, 아직 아비트라지 기회 없음)

### 2026-03-21: API 연결 디버깅 및 수정
- **RSA-PSS 서명**: Kalshi API가 PKCS1v15가 아닌 RSA-PSS 서명을 요구. `cryptography` 라이브러리의 `padding.PSS` + `SHA256` 조합으로 수정 (`d2a9400`)
- **API URL 변경**: `trading-api.kalshi.com` → `api.elections.kalshi.com` 으로 변경 확인
- **`.env` 설정 완료**: `KALSHI_API_KEY_ID` 및 `KALSHI_RSA_PRIVATE_KEY_PATH` 설정 완료
- **PEM 키 검증**: `openssl rsa -in kalshi_private_key.pem -check -noout` → RSA key OK 확인
- **Preflight 통과**: API 인증 및 마켓 조회 성공 확인

---

## Git History (23 commits)

| Commit | Description |
|--------|-------------|
| `56e6d25` | API 필드명 수정 (yes_ask_dollars), 대시보드 스레드화, 스캔 관측성 |
| `d2a9400` | RSA-PSS 서명 및 Kalshi API URL 업데이트 |
| `6304099` | Kalshi migration 핸드오버/설정 가이드 업데이트 |
| `34dc129` | 대시보드, env, 의존성 Kalshi 업데이트 |
| `086bef9` | Preflight 테스트 Kalshi 업데이트 |
| `be274d9` | 통합 테스트 Kalshi 업데이트 |
| `1a2cc95` | Main 오케스트레이터 KalshiClient 사용 |
| `e374dec` | Maker Pre-Order 시간 기반 필터링 |
| `fa5eb40` | Sum Arb Kalshi 마켓 구조 적용 |
| `b740c43` | Order Manager Kalshi 클라이언트 API 적용 |
| `b93f0e0` | Market Scanner ticker 기반으로 변경 |
| `326ab66` | KalshiClient 추가 (PolymarketClient 대체) |
| `f0bfe49` | Kalshi env 변수 및 수수료 공식 |
| `8a76fcb` | Kalshi migration 구현 계획 |
| `89215e8` | Kalshi migration 설계 스펙 |
| `5084f76` | Pre-flight checks |
| `7df76a8` | Dashboard 메인 봇 통합 |
| `381de8b` | Dashboard 프론트엔드 |
| `7134823` | Dashboard 백엔드 |
| `146dd17` | Phase 2 통합 테스트 |
| `2dee24e` | Maker Pre-Order + Notifier 통합 |
| `997a8a1` | Maker Pre-Order 전략 |
| `6e0c497` | Telegram 알림 |

---

## Known Issues

1. **Kalshi 계정 잔고 $0.06** — Paper 모드에서는 문제없으나 Live 전환 전 입금 필요
2. **API 속도 제한** — 마켓당 2 API 콜 (가격 + 오더북), 0.1초 간격 → 300 마켓 스캔에 ~60초 소요
3. **효율적 시장** — 현재 Kalshi 크립토 마켓의 YES+NO 합이 $1.00 이상으로 유지됨 (스프레드 = 마켓 메이커 수익)

---

## Future Work

1. **Paper Trading 모니터링** — 장기 실행하며 전략 파라미터 튜닝, 기회 빈도 분석
2. **Live Trading 전환** — Paper 결과 확인 후 소액 ($100~)으로 시작
3. **API 최적화** — 벌크 가격 조회, WebSocket 스트림으로 폴링 대체
4. **Rust 포팅** — 레이턴시 최적화 (핫 패스 우선)
5. **추가 전략** — 크로스 마켓 아비트라지, 이벤트 기반 전략
