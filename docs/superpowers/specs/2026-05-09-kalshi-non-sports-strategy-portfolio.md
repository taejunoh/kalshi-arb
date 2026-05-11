# Kalshi Non-Sports 수익원 포트폴리오 — Decomposition

**날짜**: 2026-05-09
**상태**: Decomposition (sub-project 4개로 분할). 첫 sub-project 선택 대기.
**배경 참조**: `docs/future-work/shutdown-fade-redesign.md` (V1 폐기 결정),
`scripts/research/shutdown_fade_data/summary.txt` (4-step prereq 분석 결과)

---

## Context

shutdown_fade V1 폐기 prereq 분석 (2026-05-09) 결과:

- 정부-전체 funding deadline 마켓 (KXGOVTSHUTDOWN, KXSHUTDOWNBY 등 7 prefix) settled = 0, open = 0
- DHS funding proxy n=19, base rate NO=63%, fade ROI **−12 ~ −23%** (모든 threshold 음수)
- budget_resolution n=19, base rate YES=58% (fade 방향 wrong), ROI −57 ~ −63%
- KXFEDMENTION (rhetoric) n=102, fade ROI −28 ~ −38%

→ **shutdown_fade V1 폐기 영구화** + 별도 "신박 다관점" 발산 결과 4개 후보 선택:

| ID | 이름 | Archetype |
|---|---|---|
| C1 | FOMC/CPI/Jobs 발표 window | Event-driven |
| B2 | Kalshi crypto vs Coinbase spot | Cross-source info arb |
| A1 | Count-ladder 단조성 arb | 구조적 mispricing |
| C2 | SCOTUS opinion-day parser | Event-driven |

본 문서는 이 4개를 **독립 sub-project** 로 분해. 각각 별도 spec → plan →
implementation cycle. 본 doc 은 분할 + 우선순위 + 의존성만 정의.

## Sub-project A1 — Count-ladder 단조성 arb

**Edge thesis**: 누적 임계값 multi-ticker (e.g., KXNUMSHUTDOWNS-T1 "≥1회",
T2 "≥2회", T3 "≥3회") 의 implied probability 는 단조 비증가
(P≥1 ≥ P≥2 ≥ P≥3) 제약. 위반 시 직접 ladder arb (양면 동시 fill).
KXGOVTSHUTLENGTH-G* (≥N days), KXNUMSHUTDOWNS-T*, 향후 추가될 multi-bucket
시리즈 모두 적용.

**데이터**: 단일 series 내 cross-ticker orderbook (Kalshi only). 외부 data 불요.

**검증**: 현 마켓 단조성 위반 빈도 측정 (실시간 sweep 1주). 빈도 충분 +
spread 이후 +ROI → paper-twin → live.

**인프라 비용**: 단일 신규 모듈 ~200 lines. spread_scanner 와 sum_arb 패턴
자연 확장. **1-2일.**

**의존성**: 없음. 독립 출시 가능.

**Open question**: (1) Kalshi 가 ladder 구조 시리즈 명시적으로 listing 하는지 —
ticker suffix 패턴 (T1/T2/G95/G80 등) 자동 grouping 알고리즘 필요. (2) 위반
빈도가 0 에 가까우면 즉시 폐기.

## Sub-project B2 — Kalshi crypto vs Coinbase spot

**Edge thesis**: KXBTC/KXETH/KXSOL/KXXRP 의 binary "주중 종가 > $X" /
"이번 주 max > $X" 마켓 implied prob 을 Coinbase realtime spot + 잔여
시간 + lognormal vol 모델로 fair value. 봇 spread 이상 deviation 시 trade.

**데이터**: Coinbase WebSocket (무료, key 불요), 각 series 의 history candle.

**검증**: 과거 settled crypto 마켓 candle vs spot 회귀 → 모델 calibration →
backtest ROI → paper-twin.

**인프라 비용**: spot feed client + BS-style fair pricing + spread-aware
trader. **2-3일.** `core/kalshi_client.py:11` 의 `CRYPTO_SERIES_PREFIXES`
이미 정의되어 frame 좋음.

**의존성**: 없음. (Pinnacle / Odds API 인프라 무관)

**Open question**: (1) 단일 series 부터 시작할지 (KXBTCMAX vs KXBTC weekly
close 등 어느 게 첫 target). (2) vol 모델 — empirical (과거 실현 vol) vs
implied (Deribit option chain). (3) 작은 bankroll 에 적합한 max position
size.

## Sub-project C1 — FOMC/CPI/Jobs 발표 window

**Edge thesis**: 8:30 ET CPI / 14:00 ET FOMC / 8:30 ET Jobs 등 시간확정
macro release 시 Kalshi rate-cut, Fed-mention, recession-call 마켓 reprice.
Pre-event consensus 진입 + post-event reaction fade. V1 의 "funding deadline"
의 시간확정성 결여 문제를 정확히 보완.

**데이터**: FRED 캘린더 (무료) 또는 manual cron. 마켓 candle (이미 보유).

**검증**: 과거 N개 FOMC/CPI 일자 앞뒤 Kalshi 마켓 candle 회귀로 reaction
크기 + 방향 측정. 표본 thin (FOMC 8회/yr, CPI 12회/yr) 이지만 명확.

**인프라 비용**: 캘린더 + window scheduler + pre/post trader. **2일.**
candlestick API + paper-twin 인프라 재사용.

**의존성**: A1 / B2 와 무관. 단, mentions_trader (KXFEDMENTION) 와 시간
window 가 겹칠 수 있음 — 체결 dedup 가드 필요.

**Open question**: (1) Target 마켓 선정 — KXFEDMENTION (mentions 이미 cover)
vs KXFEDDECISION (rate cut binary) vs KXCPI* vs KXJOBS*. 첫 cycle 하나만.
(2) Pre-event direction (long consensus) vs post-event fade (mean revert).
(3) Window 길이 (5분 / 30분 / 4시간).

## Sub-project C2 — SCOTUS opinion-day parser

**🚫 2026-05-11 폐기 (shutdown_fade V1 패턴 반복)**

원안 thesis: SCOTUS opinion 화/목 10:00 ET drop → PDF parse + 5-30초
reaction. Kalshi 의 케이스 마켓에 latency arb.

**Universe probe 결과 (2026-05-11):**
- 직접 SCOTUS-case 시리즈 18개 후보 중 현재 open=0 (KXNEWSCOTUSCONF 10개
  만 open — 2029-01 close 의 카운트 마켓, opinion-day reactive 아님)
- 과거 KXMOOREUS, KXLOPER, BIDENVNEBRASKA, SFFA 등 SCOTUS 판결 마켓 모두
  settled — Kalshi 가 prior term 에는 listing 했으나 OT2025-26 (현 term)
  케이스 마켓 listing 없음
- 광범위 legal events (KXIMPEACH, KXJUDGECOUNT, KXAGANNOUNCE) 25 open
  존재 하나 모두 count/long-horizon prediction 형 → low-latency 인프라
  이점 없음

**핵심 결론**:
1. C2 의 trade vector (opinion-day reactive 케이스 마켓) 의 universe 가
   사실상 0 in May 2026
2. Reframe (broader legal events) 도 archetype mismatch — count/threshold
   마켓은 PDF parser 인프라가 무용
3. 가장 가까운 archetype 인 mentions_trader (KXFEDMENTION rhetoric) 은
   prereq 분석에서 -38% fade ROI 음수 EV 확인 (shutdown_fade prereq doc)
4. 4-5일 인프라 투자해도 paper 검증 불가능 (universe 없음)

**재평가 trigger**: Oct 2026 (OT2026-27 term 시작) Kalshi 가 case
markets listing 재개하는지 + 마켓 빈도/유동성 충분한지. 그 시점에 다시
브레인스토밍.

**대체**: 5번째 sub-project 부산으로 시간 재배분.

## 추천 실행 순서 (2026-05-11 갱신 — C2 폐기 후)

| 순위 | sub-project | 결과 |
|---|---|---|
| 1 | **A1 Count-ladder** | ✅ 머지 (PR #1, 2026-05-09) — paper-only |
| 2 | **C1 FOMC/CPI** | ✅ 머지 (PR #2, 2026-05-10) — paper-only |
| 3 | **B2 Crypto vs spot** | ✅ PR #3 생성 (2026-05-10) — paper-only |
| 4 | ~~C2 SCOTUS~~ | 🚫 폐기 (2026-05-11) — universe 0, reframe 도 archetype mismatch |

**달성**: 3/4 sub-project 완성. 5번째 sub-project 부산 진행 (별도 결정).

## 동시 cleanup — shutdown_fade V1 폐기 영구화

본 portfolio 의 분기점이 된 결정. 별도 작업으로 처리:

- `docs/future-work/shutdown-fade-redesign.md` 에 prereq 분석 결과 + 폐기
  영구화 결정 추가
- `strategies/shutdown_fade.py` 헤더 갱신 ("재설계 대기 중" → "폐기 영구화")
- 메모리 entry 영구 기록 + 재평가 trigger (정부-전체 shutdown 마켓이 Kalshi
  에 listing 되는 경우 한정)

위 4개 sub-project 와 무관하게 즉시 처리 가능. 블로킹 없음.

## 다음 단계 결정

본 decomposition 승인 후, **첫 sub-project 한 개를 선택** → brainstorming
skill 의 full design flow (clarifying Qs 1개씩 → 2-3 approach → design
section 별 승인 → spec doc) → writing-plans skill 로 implementation plan.

추천: **A1 부터 시작** (가장 짧은 cycle, 검증 빠름, 무위험 가설 검증).
