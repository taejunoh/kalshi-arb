# Kalshi Trading Bot — Handover Document

## Project Overview

Kalshi 예측 시장 자동 트레이딩 봇. 9개 전략 (1 Live + 6 Paper + 2 비활성), Telegram 알림, 실시간 대시보드.

- **Repo**: `github.com/taejunoh/kalshi-arb`
- **언어**: Python 3.11
- **서버**: AWS EC2 `18.223.21.52` (us-east-2, t3.micro)
- **대시보드**: http://18.223.21.52:8080
- **테스트**: 98개 전체 통과
- **총 입금**: $425 ($100 + $100 + $25 + $200)
- **Live 잔고**: ~$90
- **Odds API**: 20K plan ($30/월)
- **Telegram**: @TjKalshiArb_bot — settle/trade 알림

---

## Architecture

```
kalshi-arb/
├── main.py                          # 봇 오케스트레이터 (9개 전략 + settlement)
├── core/
│   ├── config.py                    # .env 설정, LIVE_STRATEGIES
│   ├── database.py                  # SQLite — 거래/주문/settlement + paper fill
│   ├── price_feed.py                # Binance WebSocket — BTC/ETH/SOL + 변동성 추적
│   ├── kalshi_client.py             # REST API v2 (마켓 + 잔고 + 포지션 + settlements)
│   ├── market_scanner.py            # 이중 폴링: 크립토(5초) + 타겟 시리즈(30초)
│   ├── order_manager.py             # Live/Paper, yes_price 변환, Telegram 알림
│   ├── settlement.py                # Paper fill 시뮬레이션 + settle PnL + Telegram 알림
│   ├── econ_data.py                 # BLS CPI/GDP 데이터 파이프라인
│   └── external_data.py             # Fed 트랜스크립트 빈도 데이터
├── strategies/
│   ├── mentions_trader.py           # ⭐ LIVE — Fed 멘션 (유일한 검증된 엣지)
│   ├── spread_scanner.py            # Paper — 크립토 V2 (Normal CDF, 양방향)
│   ├── sports_trader.py             # Paper — 스포츠 V2 consensus (base class)
│   ├── sports_longshot.py           # Paper — 저가 고배율 (<25c, 3x+)
│   ├── sports_underdog.py           # Paper — 언더독 배팅 (2x+)
│   ├── econ_trader.py               # 비활성화 — CPI 모델 결함 (37건 Live 실수)
│   ├── market_maker.py              # Paper — 양방향 호가
│   ├── shutdown_fade.py             # Paper — 셧다운 페이드
│   └── maker_preorder.py            # Paper — 단기 프리오더
├── monitoring/
│   ├── logger.py                    # 루트 로거
│   ├── notifier.py                  # Telegram 알림 (trade + settlement)
│   └── dashboard/
│       ├── api.py                   # REST API (settlement-history, odds-usage 등)
│       └── static/index.html        # 대시보드
├── data/
│   ├── mentions_traded_tickers.json # 멘션 중복 방지
│   ├── sports_traded_tickers.json   # 스포츠 consensus 중복 방지
│   ├── sports_longshot_tickers.json # 스포츠 longshot 중복 방지
│   ├── sports_underdog_tickers.json # 스포츠 underdog 중복 방지
│   ├── spread_traded_tickers.json   # 크립토 중복 방지
│   ├── econ_traded_tickers.json     # CPI 중복 방지
│   └── bot.db                       # SQLite DB
├── scripts/
│   ├── build_mention_data.py        # Fed 트랜스크립트 파싱
│   ├── verify_all.py                # 전체 전략 검증
│   ├── verify_mlb_nhl.py            # 스포츠 매칭 검증
│   └── check_edges.py              # 스포츠 엣지 분석
└── tests/                           # 98개 테스트
```

---

## Strategies

### LIVE (1개)

**1. Mentions Trader** ⭐ 유일한 검증된 엣지
- Fed 기자회견 트랜스크립트 8개 → 42 키워드 빈도 분석
- 30-50%+ 엣지에서 공격적 사이징 (5-15% bankroll)
- 15개 포지션 (~$215), **4/30 settle 대기**
- 다음 기회: 6/18 FOMC → 5월 중순 마켓 오픈
- 연 8회 FOMC × ~48 마켓 = 연 ~384건

### PAPER — 스포츠 3전략 비교 (4/8~)

**2. Sports Consensus** (sports_trader.py)
- V2: 라인 이동 + 3% net edge → **시그널 0건** (엣지 못 찾음)
- V1 수정 후 실적: 2W 8L, -$25

**3. Sports Longshot** (sports_longshot.py) — NEW
- <25c 계약, 3x+ 페이오프, $2-3 고정 사이즈
- 첫 스캔: 2건 (DAL YES@18c 4x, DET NO@6c 8.3x)

**4. Sports Underdog** (sports_underdog.py) — NEW
- 컨센서스 반대 방향, 2x+ 페이오프
- 첫 스캔: 3건 (DAL YES@18c, LAC YES@31c, DET NO@6c)

**5. Spread Scanner V2** (Paper)
- Normal CDF + 변동성 + 모멘텀, 양방향
- V1 실적: 5W 11L -$18.94 → 모델 재설계됨

**6-8. Market Maker / Shutdown Fade / Maker Preorder** (Paper)

### 비활성화

**9. Econ Trader** — CPI 모델 결함 (backward-looking), 37건 Live 실수. ECON_ENABLED=false.

---

## Live Performance (2026-04-08 기준)

| 전략 | 전체 | 수정 후 | Net PnL |
|---|---|---|---|
| **Sports** | 10W 17L (37%) | 2W 8L (20%) | +$0.94 |
| **Spread V1** | 5W 11L (31%) | — | -$18.94 |
| **Econ** | 미settle | — | 37건 대기 |
| **Mentions** | 미settle | — | 15건 4/30 대기 |

**핵심 교훈**: 공개 데이터(Odds API, BLS)로는 수수료 후 엣지 없음. 정보 비대칭(트랜스크립트 분석)만 수익.

---

## Telegram 알림

- **Live 거래**: 📊 strategy, ticker, side, price, size
- **Settlement**: ✅ WIN / ❌ LOSS — strategy, PnL, bankroll
- Bot: @TjKalshiArb_bot, chat_id: 8578917235

---

## .env (EC2)

```env
LIVE_STRATEGIES=mentions_trader
TRADING_MODE=paper
SPORTS_ENABLED=true
SPORTS_SCAN_INTERVAL=1800
SPREAD_SCANNER_ENABLED=true
SPREAD_SCANNER_MIN_EDGE=0.03
ECON_ENABLED=false
MENTIONS_ENABLED=true
ODDS_API_KEY=1b51233d...
TELEGRAM_BOT_TOKEN=8628167598:AAEl4f-...
TELEGRAM_CHAT_ID=8578917235
```

---

## 전략 진화 이력

| 버전 | 기간 | 결과 | 교훈 |
|---|---|---|---|
| Sports V1 (bug) | 3/31 | 9W 11L +$26 | 랜덤 배팅 + 저가 고배율 = 수익 |
| Sports V1 (fix) | 4/2-4/5 | 2W 8L -$25 | 컨센서스 엣지 = 수수료 후 0 |
| Sports V2 | 4/8 | 0건 | 라인 이동 + 홈코트 → 홈코트 이중계산 버그 |
| Sports V3 | 4/8~ | 비교 중 | Longshot/Underdog/Consensus 독립 비교 |
| Spread V1 | 3/31-4/2 | 5W 11L -$19 | sigmoid 트렌드 무시, NO-only 편향 |
| Spread V2 | 4/2~ | Paper 중 | Normal CDF, 양방향, 변동성 |
| Econ V1 | 4/8 | 37건 무검증 Live | backward-looking 모델 결함 |
| Mentions V1 | 3/31~ | 15건 대기 | 유일한 정보 비대칭 전략 |

---

## Next Steps

1. **4/30 Fed settle** — 멘션 15개 포지션 수익 확인
2. **스포츠 V3 비교** — Longshot vs Underdog vs Consensus settle 비교
3. **5월 중순** — 6/18 FOMC 마켓 오픈 시 멘션 재배팅
4. **Spread V2 검증** — Paper 20건+ 후 판단
5. **새 도메인 탐색** — Spotify/Billboard 마켓 오픈 시 진입 (현재 0개)
