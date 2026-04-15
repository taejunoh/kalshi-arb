# shutdown_fade 재설계 — Future Work

**상태:** V1 폐기 (2026-04-15). 재설계 착수 대기 중.

## V1 폐기 사유

### 1. Thesis 결함 — 이질적 마켓을 단일 heuristic 으로 묶음

V1 은 `SHUTDOWN_KEYWORDS = ["shutdown", "government shutdown", "appropriations",
"funding deadline"]` 로 스캔해 긁힌 모든 마켓에 동일한 `[0.25, 0.35, 0.50]` YES
threshold 로 NO 진입. 실제 긁힌 마켓:

| 카테고리 | 예시 | 의미론 |
|---|---|---|
| Rhetoric 베팅 | `KXFEDMENTION-*-SHUT` | Fed 가 shutdown 을 언급하는지 여부. 실제 shutdown 발생 여부와 무관 |
| Count threshold | `KXNUMSHUTDOWNS-27JAN01-T{N}` | 2027년까지 shutdown **N회 이상** 발생 여부 |
| Funding deadline (원래 thesis 대상) | 가상의 `KXSHUTDOWN-*` 류 | 이진 이벤트 fade |

세 범주는 base rate, resolve timing, price dynamics 가 전부 다름. 동일 threshold
로 묶으면 edge 자체를 정의할 수 없음.

### 2. 실행 결함 — 재시작 시 중복 진입

- `_active_positions` dict 가 in-memory only ([strategies/shutdown_fade.py:20](../../strategies/shutdown_fade.py#L20) 당시).
- 봇 재시작 시 level 0 부터 재 laddering.
- `evaluate` 가 DB 의 기존 open paper_filled trade 조회 안 함 → 가드 무효화.

**실측:** 3 ticker × 3 scale levels = 정상 상한 9건. 실제 **121건** 체결 (3/26 ~
4/13, 봇 재배포/재시작 반복 기간과 일치). 상한의 약 13배 초과.

## 재설계 착수 시 필수 선행 연구

**순서 엄수.** Paper 배포 전 반드시 (1)→(4) 완료.

### (1) Kalshi API 로 과거 resolved 마켓 수집

```
/trade-api/v2/markets?status=settled&series_ticker=<prefix>&limit=200
```

대상 series prefix 후보: `KXSHUTDOWN`, `KXNUMSHUTDOWNS`, `KXFEDMENTION`,
`KXFUNDING`, `KXCR` (continuing resolution), `KXDEBTCEILING`. 실제 존재하는
series 는 API 로 확인 필요.

### (2) 카테고리별 분리

raw dump 을 rhetoric / count threshold / funding deadline 으로 분류. 각각 독립
분석.

### (3) 카테고리별 통계

- 실제 resolve 분포 (YES / NO)
- Pre-close YES 경로 (close 1 day / 1 week 전 분포 vs 최종 resolve)
- 샘플 크기 — thin sample 이면 edge 주장 불가

### (4) 재설계 범위 결정

- Funding deadline fade base rate 가 강하게 비대칭 (예: >75% NO) → 해당 카테고리
  한정 재설계
- Rhetoric / count 는 fade heuristic 부적합 → 포함하지 않거나 별도 전략으로 분리
- 전 카테고리 edge 불확실 → **폐기**

## 재설계 시 반드시 지킬 실행 가드

(메모리에 누적된 failure mode 기반 checklist — 위반 시 과거 사고 재발)

### 상태 영속화
- 포지션 상태를 in-memory dict 로 두지 말 것.
- `bot_state` 테이블 write-through 또는 매 scan 마다 DB 에서 reconstruct.
- 참고: `architecture_bankroll_persistence.md` 메모리의 6-곳 write-through 패턴.

### 중복 진입 가드
- `evaluate` 는 반드시 DB 쿼리로 현재 open/unsettled 포지션 확인 후 판단.
- 참고: [strategies/sports_trader.py:495](../../strategies/sports_trader.py#L495) 의 `_traded_tickers` 패턴.

### Paper first + 유효성 검증
- 새 heuristic 은 **먼저 전략 유효성 검증 → Paper 최소 1~2주 → 결과 충분 누적 후**
  Live 검토.
- 참고: `feedback_always_paper_first.md` 메모리 (3회 누적 실수).

### 배포 후 감사
- 배포 후 5단계 verification (log → DB → settle → dashboard → telegram).
- 큰 변경은 code-reviewer agent 자가 감사.

## 현재 DB 에 남아있는 V1 잔여 trade

- 121건 `paper_filled` 상태 (KXFEDMENTION 55, KXNUMSHUTDOWNS-T2 59, T3 7).
- 별도 정리 불필요 — 마켓 resolve 시 기존 settle 파이프라인이 자연 처리.
- KXFEDMENTION: 2026-04-30 close
- KXNUMSHUTDOWNS-*: 2027-01-01 close → 장기 대기

결과 정산 후 V1 누적 성과 (win rate, PnL) 를 본 문서에 추가해 재설계 baseline
으로 활용.
