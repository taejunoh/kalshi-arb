# Kalshi Arbitrage Bot — Handover Document

## Project Overview

Kalshi 예측 시장의 가격 비효율성을 이용한 자동 트레이딩 봇. 6개 전략을 통해 차익거래, 마켓 메이킹, 방향성 베팅을 수행한다.

- **위치**: `/Users/taejunoh/Desktop/LFG/kalshi-arb/`
- **언어**: Python 3.11
- **상태**: Paper Trading 실행 중 ($1,000 bankroll, 6개 전략 활성)
- **테스트**: 88개 전체 통과

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (진입점)
├── core/
│   ├── config.py                    # .env 기반 설정, 수수료 계산, 전략별 파라미터
│   ├── database.py                  # SQLite (aiosqlite) — 거래/주문 저장
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL 실시간 가격
│   ├── kalshi_client.py             # REST API v2 래퍼 (크립토 + 전체 마켓 + 오더북 레벨)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 전략 마켓(30초, 타겟 시리즈)
│   ├── order_manager.py             # Paper/Live 주문, 포지션 추적, 리쿼팅, 서킷 브레이커
│   └── external_data.py             # 멘션 빈도 데이터 (API + 정적 JSON 폴백)
├── strategies/
│   ├── sum_arb.py                   # Yes/No Sum Arbitrage (크립토)
│   ├── maker_preorder.py            # Maker Pre-Order (5분 마켓)
│   ├── market_maker.py              # 양방향 마켓 메이킹 (리쿼팅 30초)
│   ├── shutdown_fade.py             # 정부 셧다운 페이드 (3단계 스케일인)
│   ├── spread_scanner.py            # 스프레드 스캐너 (VWAP 기반)
│   └── mentions_trader.py           # 멘션 마켓 트레이더 (연설 빈도 분석)
├── monitoring/
│   ├── logger.py                    # 루트 로거, 일별 로테이션, 서드파티 노이즈 억제
│   ├── notifier.py                  # Telegram 알림 (속도 제한 포함)
│   └── dashboard/
│       ├── api.py                   # FastAPI — /status, /trades, /orders, /performance
│       └── static/index.html        # 다크 테마 대시보드 UI
├── tests/                           # 18개 테스트 파일, 88개 테스트
├── docs/setup-guide.md              # Kalshi 계정 설정 가이드
├── requirements.txt                 # 의존성
└── .env.example                     # 환경변수 템플릿
```

---

## Strategies (6개)

### 1. Yes/No Sum Arbitrage (`strategies/sum_arb.py`)
**원리**: YES + NO < $1.00 (수수료 포함) → 양쪽 매수, 무위험 차익.
- 크립토 마켓 300개 스캔 (5초 간격)
- 최소 갭: 3센트 (설정 가능)
- **현황**: 효율적 시장으로 기회 없음 (최선 갭: -0.01)

### 2. Maker Pre-Order (`strategies/maker_preorder.py`)
**원리**: 5분 마켓에 양쪽 리밋 오더 배치, 합산 < $1.00 → 차익.
- 4,341개 주문 배치 (paper mode)

### 3. Market Maker (`strategies/market_maker.py`) ⭐ NEW
**원리**: 양방향 호가 제시, 스프레드에서 수익. @debl00b ($160M vol) 영감.
- 유동성 높은 크립토 마켓 최대 3개 동시 호가
- 오더북 레벨 기반 mid price 계산 (VWAP)
- 30초 리쿼팅 (cancel + replace)
- 인벤토리 스큐: 포지션 편중 시 호가 조정
- **현황**: 178개 주문 활성 리쿼팅 중

### 4. Shutdown Fade (`strategies/shutdown_fade.py`) ⭐ NEW
**원리**: 정부 셧다운 마켓에서 YES > 25c일 때 NO 매수. @GaetenD ($530K) 영감.
- 키워드 검색: "shutdown", "appropriations", "funding deadline"
- 3단계 스케일인 (25c, 40c, 55c)
- 최대 포지션: $500/마켓
- **현황**: 15 trades 실행, $2,500 총 규모 (KXFEDMENTION, KXNUMSHUTDOWNS)

### 5. Value Spread Scanner (`strategies/spread_scanner.py`) ⭐ NEW
**원리**: 넓은 스프레드 마켓에서 VWAP 대비 저평가 측 매수. @Foster/$Domahhhh 영감.
- 295개 전략 마켓 스캔 (60초 간격)
- 스코어링: `spread_width × volume / days_to_close`
- 오더북 VWAP 근사로 fair value 추정
- 최소 스프레드: 5센트, 최소 거래량: 100
- **현황**: 31 trades 실행, $329 총 규모 (주로 KXFED 금리 마켓)

### 6. Mentions Trader (`strategies/mentions_trader.py`) ⭐ NEW
**원리**: "Will X say Y?" 마켓에서 연설 빈도 데이터 기반 트레이딩. @Foster/$theduckguesses 영감.
- 제목 패턴 매칭: `"Will .* (mention|say)"`
- 외부 API 또는 정적 JSON에서 빈도 데이터 조회
- 최소 엣지: 15%
- **현황**: 48개 멘션 마켓 발견, 빈도 데이터 미설정으로 트레이딩 대기 중

---

## Key Components

### Kalshi Client (`core/kalshi_client.py`)
- **크립토 마켓**: `get_active_markets()` — KXBTC/KXETH/KXSOL/KXXRP 시리즈
- **전체 마켓**: `get_all_active_markets()` — 커서 기반 페이지네이션 (836K+ 마켓)
- **오더북 레벨**: `get_orderbook_levels(ticker)` → `{"yes": [(price, qty), ...], "no": [...]}`
- **배치 취소**: `batch_cancel_orders(order_ids)` — 순차 취소 + 에러 처리
- **API 필드**: `yes_ask_dollars` / `no_ask_dollars` (문자열 소수)
- **RSA-PSS 서명**: `cryptography` 라이브러리

### Market Scanner (`core/market_scanner.py`)
- **크립토 풀**: 5초 간격 폴링 (300개 마켓)
- **전략 풀**: 30초 간격 타겟 시리즈 쿼리 (295개 마켓, ~2초 소요)
  - 타겟 시리즈: KXFEDMENTION, KXNUMSHUTDOWNS, KXGOVSHUT, KXINX, KXCPI, KXGDP, KXFED 등 16개
  - 최초 전체 스캔(836K, 10분) → 타겟 스캔(295, 2초)으로 330배 개선
- `search_markets(keyword)` — 제목 키워드 검색
- `filter_markets(regex)` — 정규식 매칭
- `run_in_executor`로 동기 HTTP를 비동기 이벤트 루프에서 비차단 실행

### Order Manager (`core/order_manager.py`)
- **Paper/Live 이중 모드**: 시뮬레이션 또는 실제 주문
- **포지션 추적**: `_positions[(strategy, ticker)]` → `{side, qty, avg_price, size_usdc}`
- **리쿼팅**: `requote(old_order_id, ...)` — 취소 + 신규 배치 (마켓 메이커용)
- **Circuit Breaker**: 일일 $200 손실 한도, 연속 5회 손실 → 15분 일시정지
- **Kelly Fraction**: 최적 포지션 사이징

### External Data (`core/external_data.py`)
- `FrequencyDataProvider`: 멘션 빈도 데이터 제공
- API 우선 조회 → 정적 JSON 폴백
- httpx 비동기 클라이언트 (5초 타임아웃)

### Dashboard (`monitoring/dashboard/`)
- 데몬 스레드에서 uvicorn 실행
- FastAPI: `/api/status`, `/api/trades?strategy=X`, `/api/orders`, `/api/performance`
- 다크 테마 UI, 10초 자동 새로고침

---

## Configuration Reference (`.env`)

### Core
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `TRADING_MODE` | paper / live | paper |
| `INITIAL_BANKROLL` | 초기 자본 (USD) | 100 |
| `KALSHI_API_KEY_ID` | Kalshi API 키 ID | — |
| `KALSHI_RSA_PRIVATE_KEY_PATH` | RSA 프라이빗 키 PEM 경로 | — |
| `KALSHI_API_BASE_URL` | API 베이스 URL | https://api.elections.kalshi.com |
| `KALSHI_FEE_PER_CONTRACT` | 컨트랙트당 수수료 | 0.07 |

### Risk & Sizing
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `KELLY_FRACTION` | Kelly 비율 | 0.25 |
| `MAX_SINGLE_TRADE_PCT` | 최대 단일 거래 비율 | 0.10 |
| `MAX_DAILY_LOSS` | 일일 손실 한도 | 200 |
| `MAX_CONSECUTIVE_LOSSES` | 연속 손실 한도 | 5 |

### Strategy: Sum Arb / Maker Pre-Order
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `SUM_ARB_MIN_GAP` | 최소 갭 | 0.03 |
| `MAKER_TARGET_SUM` | 타겟 합산 | 0.96 |

### Strategy: Market Maker ⭐
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `MM_ENABLED` | 활성화 | false |
| `MM_MIN_VOLUME` | 최소 거래량 | 1000 |
| `MM_SPREAD_CENTS` | 스프레드 (센트) | 4 |
| `MM_REQUOTE_INTERVAL` | 리쿼팅 간격 (초) | 30 |
| `MM_MAX_POSITION` | 마켓당 최대 포지션 ($) | 200 |
| `MM_MAX_MARKETS` | 최대 동시 호가 마켓 수 | 3 |

### Strategy: Shutdown Fade ⭐
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `SHUTDOWN_FADE_ENABLED` | 활성화 | false |
| `SHUTDOWN_FADE_THRESHOLD` | YES 진입 임계값 | 0.25 |
| `SHUTDOWN_FADE_SCALE_LEVELS` | 스케일인 단계 수 | 3 |
| `SHUTDOWN_FADE_MAX_POSITION` | 마켓당 최대 포지션 ($) | 500 |

### Strategy: Spread Scanner ⭐
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `SPREAD_SCANNER_ENABLED` | 활성화 | false |
| `SPREAD_SCANNER_MIN_WIDTH` | 최소 스프레드 | 0.05 |
| `SPREAD_SCANNER_MIN_VOLUME` | 최소 거래량 | 100 |
| `SPREAD_SCANNER_INTERVAL` | 스캔 간격 (초) | 60 |

### Strategy: Mentions Trader ⭐
| 변수 | 설명 | 기본값 |
|------|------|--------|
| `MENTIONS_ENABLED` | 활성화 | false |
| `MENTIONS_MIN_EDGE` | 최소 엣지 (%) | 0.15 |
| `MENTIONS_FREQUENCY_API_URL` | 빈도 데이터 API URL | — |

---

## Tests

```bash
source venv/bin/activate
python -m pytest tests/ -v
```

18개 테스트 파일, 88개 테스트 전체 통과:
- `test_config.py` — 설정 로드, 수수료 계산
- `test_database.py` — DB CRUD 연산
- `test_price_feed.py` — WebSocket 연결, 캔들 추적
- `test_kalshi_client.py` — API 래퍼, 마켓 가격/오더북 파싱
- `test_order_manager.py` — 주문 실행, 서킷 브레이커
- `test_market_scanner.py` — 마켓 폴링, 캐싱
- `test_sum_arb.py` — Sum Arb 시그널 감지
- `test_maker_preorder.py` — Pre-Order 로직
- `test_market_maker.py` — mid 계산, 호가, 리쿼팅, 인벤토리 스큐, 서킷 브레이커
- `test_shutdown_fade.py` — 키워드 매칭, 스케일인, 최대 포지션
- `test_spread_scanner.py` — 스프레드 계산, 스코어링, VWAP, 저평가 탐지
- `test_mentions_trader.py` — 제목 파싱, 시그널 생성, 엣지 계산
- `test_logger.py` — 로그 로테이션
- `test_notifier.py` — Telegram 알림
- `test_dashboard_api.py` — API 엔드포인트
- `test_main.py` — 메인 루프
- `test_integration.py` — 통합 테스트
- `test_preflight.py` — Pre-flight 검증

---

## Recent Work

### 2026-03-25: 4개 신규 전략 + 인프라 확장

**4개 전략 추가** (`0817d08`):
- Market Maker: 양방향 호가 + 30초 리쿼팅 + 인벤토리 스큐
- Shutdown Fade: 키워드 검색 + 3단계 스케일인
- Spread Scanner: 오더북 VWAP + 스프레드 스코어링
- Mentions Trader: 제목 정규식 파싱 + 외부 빈도 데이터

**인프라 확장** (`0817d08`):
- `kalshi_client.py`: `get_all_active_markets()` (페이지네이션), `get_orderbook_levels()`, `batch_cancel_orders()`
- `market_scanner.py`: 이중 풀 (크립토 5초 + 전략 30초), `search_markets()`, `filter_markets()`
- `order_manager.py`: 포지션 추적 `_positions`, `requote()` 메서드
- `config.py`: 15개 신규 환경변수 (전략별 on/off + 파라미터)
- `external_data.py`: `FrequencyDataProvider` (API + 정적 JSON)

**성능 최적화** (`be47e66`):
- 전체 마켓 스캔 (836K, 10분) → 타겟 시리즈 쿼리 (295, 2초) — **330배 개선**
- `run_in_executor`로 동기 HTTP를 비동기 이벤트 루프에서 비차단 실행

**스프레드 스캐너 튜닝**:
- `SPREAD_SCANNER_INTERVAL`: 300 → 60초
- `SPREAD_SCANNER_MIN_WIDTH`: 0.08 → 0.05
- `SPREAD_SCANNER_MIN_VOLUME`: 500 → 100

### 2026-03-25: API 필드 수정 및 관측성 추가

**치명적 버그 수정** (`56e6d25`):
- `yes_ask` → `yes_ask_dollars`, `orderbook` → `orderbook_fp`
- 가격 파싱: `cents / 100` → `float(dollar_string)`

**대시보드 수정** (`56e6d25`):
- uvicorn 데몬 스레드 + 포트 폴백 (8080→8081→8082)

### 2026-03-21: API 연결 디버깅
- RSA-PSS 서명 수정 (`d2a9400`)
- API URL: `api.elections.kalshi.com`

---

## Paper Trading 결과 (2026-03-25, 첫 1시간)

| 전략 | 거래 수 | 총 규모 | 주문 수 |
|------|--------|---------|---------|
| Shutdown Fade | 15 | $2,500 | — |
| Spread Scanner | 31 | $329 | — |
| Market Maker | — | — | 178 (리쿼팅) |
| Maker Pre-Order | — | — | 4,341 |
| Sum Arb | 0 | $0 | — |
| Mentions Trader | 0 | $0 | — |

---

## Git History (25 commits)

| Commit | Description |
|--------|-------------|
| `be47e66` | 전체 마켓 스캔 → 타겟 시리즈 쿼리 (330배 성능 개선) |
| `0817d08` | 4개 신규 전략 + 인프라 확장 (88 tests) |
| `7a63658` | 핸드오버 문서 업데이트 |
| `56e6d25` | API 필드명 수정, 대시보드 스레드화, 스캔 관측성 |
| `d2a9400` | RSA-PSS 서명 및 Kalshi API URL 업데이트 |
| `6304099` | Kalshi migration 핸드오버/설정 가이드 |
| `34dc129` | 대시보드, env, 의존성 Kalshi 업데이트 |
| `086bef9` ~ `6e0c497` | Kalshi migration (17 commits) |

---

## Known Issues

1. **Kalshi 계정 잔고 $0.06** — Live 전환 전 입금 필요
2. **대시보드 간헐적 종료** — uvicorn 데몬 스레드가 가끔 죽음, 봇 재시작으로 복구
3. **멘션 트레이더 데이터 미설정** — `MENTIONS_FREQUENCY_API_URL` 또는 `data/mention_frequencies.json` 필요
4. **Paper 모드 리밋 오더 체결 미시뮬레이션** — 마켓 메이커 주문이 `paper_open` 상태로 유지, 실제 체결 시뮬레이션 없음

---

## Future Work

1. **Paper Trading 분석** — 1주일 실행 후 전략별 PnL 분석, 파라미터 튜닝
2. **멘션 데이터 구축** — C-SPAN 트랜스크립트 또는 공개 연설 API 연동
3. **리밋 오더 체결 시뮬레이션** — Paper 모드에서 시장가가 리밋 가격 도달 시 자동 체결
4. **대시보드 안정화** — uvicorn 스레드 모니터링 + 자동 재시작
5. **Live Trading 전환** — 소액 ($100~)으로 시작, 전략별 순차 활성화
6. **WebSocket 스트림** — REST 폴링 → Kalshi WebSocket으로 레이턴시 개선
7. **Rust 포팅** — 핫 패스 레이턴시 최적화
