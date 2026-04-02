# Kalshi Trading Bot — Handover Document

## Project Overview

Kalshi 예측 시장 자동 트레이딩 봇. 6개 전략 (3 Live + 3 Paper), 실시간 대시보드, 자동 Settlement.

- **Repo**: `github.com/taejunoh/kalshi-arb`
- **언어**: Python 3.11
- **서버**: AWS EC2 `18.223.21.52` (us-east-2, t3.micro)
- **대시보드**: http://18.223.21.52:8080
- **테스트**: 91개 전체 통과
- **총 입금**: $425 ($100 + $100 + $25 + $200)
- **Live 잔고**: ~$100 (변동)
- **Live 포지션**: 멘션 15개 + 스포츠 5개 + 크립토 수시
- **Odds API**: 20K plan ($30/월), 키: `1b51233d...`

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (6개 전략 + settlement)
├── core/
│   ├── config.py                    # .env 설정, LIVE_STRATEGIES, SPREAD_SCANNER_MIN_EDGE
│   ├── database.py                  # SQLite — 거래/주문/settlement + paper fill 조회
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL
│   ├── kalshi_client.py             # REST API v2 (마켓 + 오더북 + 잔고 + 포지션 + resting orders + settlements)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 타겟 시리즈(30초)
│   ├── order_manager.py             # 전략별 Live/Paper, yes_price 변환, bankroll 차감, 잔고 체크
│   ├── settlement.py                # Paper fill 시뮬레이션 + 마켓 settle PnL 계산
│   └── external_data.py             # Fed 트랜스크립트 빈도 데이터
├── strategies/
│   ├── mentions_trader.py           # ⭐ LIVE — Fed 멘션 (3중 중복 방지)
│   ├── spread_scanner.py            # ⭐ LIVE — 크립토 단기 (수수료 반영, 노출 한도)
│   ├── sports_trader.py             # ⭐ LIVE — 스포츠 (수수료 반영, 노출 한도, 팀 매칭)
│   ├── market_maker.py              # Paper — 양방향 호가
│   ├── shutdown_fade.py             # Paper — 셧다운 페이드
│   ├── sum_arb.py                   # 제거됨 (Kalshi YES+NO ≈ $1, 아비트라지 불가)
│   └── maker_preorder.py            # Paper — 단기 프리오더
├── monitoring/
│   ├── logger.py                    # 루트 로거, 일별 로테이션
│   ├── notifier.py                  # Telegram 알림
│   └── dashboard/
│       ├── api.py                   # /status, /trades, /orders, /performance,
│       │                            # /settlement, /live, /strategy-status,
│       │                            # /settlement-history, /odds-usage
│       └── static/index.html        # 대시보드 (Settlement History, Paper Performance 분리)
├── data/
│   ├── mention_frequencies.json     # Powell 키워드 빈도 데이터
│   ├── mentions_traded_tickers.json # 멘션 중복 방지 (영구 저장)
│   ├── spread_traded_tickers.json   # 크립토 중복 방지 (영구 저장)
│   ├── sports_traded_tickers.json   # 스포츠 중복 방지 (영구 저장, 영구 차단)
│   └── bot.db                       # SQLite 거래/주문 DB
├── scripts/
│   ├── build_mention_data.py        # Fed 트랜스크립트 파싱
│   ├── backtest_spread_scanner.py   # 크립토 백테스트
│   ├── verify_all.py                # 전체 전략 end-to-end 검증
│   ├── verify_mlb_nhl.py            # MLB/NHL 매칭 + 팀 식별 검증
│   └── check_edges.py              # 스포츠 엣지 분포 분석
├── deploy/                          # EC2 배포 (setup.sh, systemd)
├── docs/
│   ├── strategy-guide.md            # 전략 상세 가이드
│   └── backtest-results.md          # 백테스트 결과
└── tests/                           # 91개 테스트
```

---

## Strategies (6개)

### LIVE (3개 — 실제 돈)

**1. Mentions Trader** ⭐ 최고 알파
- Fed 기자회견 트랜스크립트 8개 분석 → 마켓 가격과 비교
- **공격적 사이징**: 50%+ 엣지 → 15% bankroll, 30%+ → 10%, 15%+ → 5%
- 15개 Live 포지션 보유 (~$215)
- Settle: **4/30 Fed 기자회견** 후
- **3중 중복 방지**: 파일 + Kalshi API (positions + resting orders) + 메모리

**2. Spread Scanner** — 크립토 단기
- Binance spot → sigmoid 확률 → Kalshi와 비교
- **NO-only** 모드 (백테스트 최적화)
- 엣지 임계값: `SPREAD_SCANNER_MIN_EDGE` env var (현재 3%), **수수료 반영 후 net edge**
- 12시간 이내 settle, 리밋 오더
- **총 노출 한도**: bankroll 30% 초과 시 거래 정지
- 파일 영구 저장으로 중복 방지

**3. Sports Trader** — NBA/MLB/NHL
- The Odds API 컨센서스 오즈 vs Kalshi 가격 (20K plan, $30/월)
- **1% net edge** (수수료 반영 후), 30분 스캔 간격
- **팀 식별**: `yes_sub_title` + `ODDS_TO_KALSHI_SHORT` dict (MLB/NBA/NHL 전팀)
- **안전장치**: 사이클당 최대 3건, 총 노출 bankroll 30%, 같은 경기 양팀 배팅 방지, 영구 ticker 차단
- 파일 영구 저장으로 중복 방지

### PAPER (3개 — 시뮬레이션)

4. **Market Maker** — 양방향 호가, 동적 스프레드 (2-4c)
5. **Shutdown Fade** — 셧다운 NO 매수 (117건 거래 중)
6. **Maker Pre-Order** — 2-30분 마켓 양방향 리밋

**제거됨**: Sum Arb — Kalshi YES+NO ≈ $1.00, 수수료 후 아비트라지 불가

---

## Live Performance (2026-04-01 기준)

### Sports Trader (settle된 17건)
- **8W 9L, 승률 47%**
- Revenue $187 / Cost $160 / Fees $6.59
- **Net PnL: +$20.41**
- 참고: 홈/어웨이 뒤집힘 버그 상태에서 배팅한 결과 (운)

### Spread Scanner
- ETH NO 12x $2.64 체결 (첫 거래)

### Mentions Trader
- 15개 포지션, 4/30 settle 대기

---

## Dashboard

http://18.223.21.52:8080

**헤더**: Live Balance, Live Positions, Live Fees, Live Exposure, Paper PnL, Total Trades, **Odds API Usage**
**Settlement History**: Kalshi API 실제 정산 — 전략별 W/L, 승률, Net PnL, 개별 거래 내역
**Strategy Performance (Paper)**: Paper 전략만 표시 — Live는 Settlement History에서 확인
**Live Positions**: Kalshi 실제 포지션 테이블
**Trade History**: Live/Paper 필터 + Mode 뱃지
**Open Orders**: 최근 100개

API 엔드포인트:
- `/api/status` — bankroll, daily PnL, trade count
- `/api/live` — Kalshi 실제 잔고 + 포지션
- `/api/strategy-status` — 전략별 LIVE/PAPER
- `/api/performance` — Paper 전략 성과 (live/paper 분리)
- `/api/settlement-history` — Kalshi settlement W/L/PnL (전략별)
- `/api/odds-usage` — Odds API 사용량 (used/remaining)
- `/api/settlement` — Live/Paper 분리된 PnL
- `/api/trades`, `/api/orders`

---

## Critical Bug Fixes (2026-03-31 ~ 04-02)

### Phase 1: 중복 주문 ($48 손실)
1. `_traded_tickers` 주문 후 마킹 → **주문 전**으로 이동
2. 재시작 시 resting orders 미로딩 → positions + resting orders + 파일 3중 로딩
3. `except: pass` → `logger.error(CRITICAL)` 명시적 로깅
4. DB `record_order` 에러 전파 → `_live_limit_order`에서 catch

### Phase 2: yes_price NO 주문 가격 뒤집힘
5. NO 주문 시 `yes_price = 100 - price_cents` 변환 추가
6. 잘못된 resting orders 24개 전부 취소

### Phase 3: Sports Trader 대수술
7. 게임 매칭: mascot 이름 → **도시명 매칭** (Phoenix Suns → "phoenix" in title)
8. 팀 식별: title 파싱 → **`yes_sub_title`** 사용 (Kalshi가 제공하는 정확한 데이터)
9. NHL: "ABR Mascot" 형식 (SJ Sharks, EDM Oilers) → `ODDS_TO_KALSHI_SHORT` 30팀 추가
10. 같은 도시 두 팀: `ODDS_TO_KALSHI_SHORT` exact match 우선 (NY Mets vs Yankees)
11. `_decimal_to_prob`: odds ≤ 1 → None 반환 (0.5 대신 skip)
12. 수수료 반영: net edge = gross edge - fee% (8% → **1% net edge**)
13. 사이클 한도: max 3건 + **총 노출 30% bankroll** (Kalshi API 기반)
14. 같은 경기 양팀 배팅 방지: `game_id` 추적
15. 영구 ticker 차단: 1시간 쿨다운 → 영구 set
16. live bankroll 차감: `_live_limit_order`에서 size + fee 차감

### Phase 4: Paper Trading 개선
17. Paper market order 수수료 미차감 → `bankroll -= (size + fee)`
18. Paper limit order bankroll 미예약 → 주문 시 즉시 차감
19. sum_arb 수수료 과소 계산 → 실제 트레이드 크기 기준 계산
20. **Paper limit order fill 시뮬레이션** 추가 (market_maker, maker_preorder 작동)

### Phase 5: sum_arb 제거
21. Kalshi YES+NO ≈ $1.00, 수수료 후 아비트라지 불가 → 전략 + 테스트 제거

---

## Safety Mechanisms

1. **3중 중복 방지** (mentions) — 파일 + Kalshi API + 메모리
2. **파일 영구 저장** (전 전략) — 재시작해도 복구
3. **주문 전 마킹** — 모든 전략에서 `_traded_tickers`를 주문 전에 갱신 + 저장
4. **yes_price 변환** — NO 주문 시 `100 - price_cents` 자동 변환
5. **수수료 반영 엣지** — sports/spread 모두 net edge 기준 거래
6. **총 노출 한도** — sports: Kalshi API 기반 30%, spread: 메모리 기반 30%
7. **사이클 한도** — sports: max 3건/사이클 + 같은 경기 양팀 방지
8. **영구 ticker 차단** — sports: 한 번 거래한 마켓 다시 안 함
9. **실제 잔고 체크** — Live 주문 전 Kalshi API로 잔고 확인
10. **Bankroll 차감** — Live limit order에서도 size + fee 차감
11. **API 실패 시 차단** — `_get_live_sports_exposure()` 실패 → `inf` 반환 → 거래 차단
12. **Circuit Breaker** — 일일 $200 손실 한도, 연속 5패 시 15분 정지

---

## Key Learnings

### 버그에서 배운 것
1. **yes_price 뒤집힘** → NO 주문 전부 잘못된 가격, 24개 resting orders 1센트로 생성
2. **중복 주문 $48 손실** → `_traded_tickers`를 주문 후가 아닌 전에 마킹해야 함
3. **홈/어웨이 뒤집힘** → mascot 이름으로 매칭 → 60%+ 가짜 엣지 → 39건 잘못된 배팅
4. **수수료 미반영** → 10% gross edge가 수수료 후 3% net → 실제로는 수익 안 남
5. **사이클 한도 없음** → 한 번에 39건 배팅 → $196 소진
6. **NHL 매칭 실패** → "SJ Sharks" 형식 미지원 → NHL 전체 0건
7. **Odds API 무료 티어** → 500건/월, 5분 스캔으로 14시간 만에 소진
8. **Paper limit fill 없음** → market_maker, maker_preorder 0건 거래

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
curl localhost:8080/api/settlement-history
curl localhost:8080/api/odds-usage
```

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

## Paper Strategy Backlog (Live 전환 전 수정 필요)

| # | 전략 | 문제 | 심각도 |
|---|---|---|---|
| 4 | Market Maker | requote 시 기존 주문 상태 미확인 → 포지션 2배 가능 | 높음 |
| 5 | Market Maker | MM_MAX_POSITION 미검증 → 한도 초과 가능 | 높음 |
| 6 | Maker Preorder | 마켓 잠금 누락 (조기 return 시) | 중간 |
| 7 | Market Maker | 인벤토리 스큐 고정 1c → 대규모 포지션 헷징 불가 | 중간 |
| 8 | Shutdown Fade | levels_filled 조기 증가 (주문 실패해도) | 중간 |

---

## Next Steps

1. **4/30 Fed 기자회견** — 멘션 15개 포지션 settle → 수익 확인
2. **스포츠/크립토 모니터링** — 수정된 코드로 실적 추적
3. **수익 시 스케일업** — bankroll 추가 → 더 많은 거래
4. **멘션 빈도 갱신** — 6월 기자회견 전 최신 트랜스크립트 반영
5. **Paper → Live 전환 평가** — shutdown_fade, market_maker 실적 확인 후 판단
6. **Kalshi-Polymarket 아비트라지** — 크로스 플랫폼 (15-20% 발생)
