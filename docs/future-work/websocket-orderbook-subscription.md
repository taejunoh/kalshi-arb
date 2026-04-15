# Kalshi WebSocket Orderbook 구독 — Future Work

**상태:** 2026-04-15 현재 착수 보류. V31 Live 1-2주 운영 후 realized slippage 분포로 정당성 재평가.

## Context

V31 sports 전략이 Live 로 전환되면서 depth check → balance check → place_market_order 를 순차 Kalshi REST 호출로 수행. REST 호출 창 동안 Kalshi ask 가 drift 하면:
- **2¢ 이상 움직이면** `buy_max_cost` 제한으로 주문 거부
- **1-2¢ 움직이면** 체결되지만 slippage 비용 발생

WebSocket 으로 orderbook 을 실시간 구독하면 depth check 가 메모리 읽기가 되어 API call 1회 제거.

## Latency 분석 (2026-04-15 실측)

Kalshi API 는 CloudFront (CMH POP) 뒤, 우리 EC2 (us-east-2) 와 같은 Ohio 지역.
Per-call 실측 RTT: **~45ms** (cache hot: 2ms connect + 30-40ms TTFB).

| 구간 | 현재 cache hot | 현재 cache cold | WS 도입 후 |
|---|---|---|---|
| Depth (REST → WS) | 45ms | 45ms | **<1ms** |
| Balance check (cached) | 0ms | 45ms | 0ms |
| place_market_order | 45ms | 45ms | 45ms |
| **Total** | **90ms** | **135ms** | **45-90ms** |

**실제 절감**: entry 당 ~45ms (100ms → 45ms 수준).

## PnL 재계산 (보수 가정)

Drift 분포 가정 (live-game 기준):
- 1¢ in 100ms: 10%
- 2¢ in 100ms: 3%
- 3¢+ in 100ms: 1%

V31 하루 ~30 trades 기준:

| 메트릭 | 현재 (135ms) | WS 후 (45-90ms) |
|---|---|---|
| Rejection rate | 1.3% | 0.5% |
| Adverse slippage rate | 13% | 7% |
| 일일 기회 비용 | ~$1.2/day | ~$0.5/day |
| 월 환산 | $36 | $15 |

**WebSocket 순이득**: **~$20/month** (낙관적 $50).

## 개발 비용

- Kalshi WS auth (RSA-signed WS handshake)
- Orderbook snapshot + delta 적용 상태머신
- 재접속 + gap detection (snapshot 재요청)
- 구독 관리 (트레이드 대상 312 tickers 만 subscribe, 전체 구독은 밴드위스 낭비)
- Fallback: WS 장애 시 REST polling 으로 자동 전환
- 테스트: WS mock 또는 record/replay 인프라

**예상 공수**: **2-3일** 집중 작업.

## ROI

| 항목 | 값 |
|---|---|
| 월 기대 이득 | **~$20** (최악 $10, 최상 $50) |
| 개발 시간 | 2-3일 |
| Break-even | **3-6 개월** |
| 부가 이득 | Fill notification 즉시, 실시간 orderbook (향후 전략 설계 재활용) |
| 리스크 | 복잡도 ↑, 버그 표면 ↑, 비결정적 테스트 |

## 착수 조건 (Gate)

다음 중 하나 만족해야 착수:

1. **Slippage 실측 > 1¢ 평균** — 1주일 realized_fill_price 데이터 기반
2. **Rejection rate > 5%** — buy_max_cost 거부율이 기대보다 높음
3. **V31 가설 A 확정** (2026-04-29) — edge 가 구조적으로 검증돼야 최적화 투자 정당
4. **Sports trade 볼륨 > 60/day** — 스케일이 커져야 월 절감 > $40 로 정당

위 중 2개+ 만족 시 착수 권장.

## 대안 개선 (같은 2-3일 예산)

우선순위 높음:

- **Task 10 (V3 vs V31 forward 분석 script)** — 2026-04-29 판별 자동화. WebSocket 보다 직접적 가치.
- **Daily slippage 모니터링 대시보드** — `realized_fill_price - price` 히스토그램 자동 기록 → WebSocket 착수 판단 자료
- **sports_v4_edge / sports_pinnacle_edge 샘플 보완** — 현재 n<10 판단 불가

## 참고

- 이전 분석 ($360/month) 은 latency 200ms 오추정 기반이었음
- 실측 CloudFront CMH POP 확인 후 45ms 로 보정 (docs/future-work/... 내 이 문서)
- Region 이동 (us-east-2 → us-east-1) 은 **완전 패스** — CloudFront 가 동일 POP 으로 라우팅
