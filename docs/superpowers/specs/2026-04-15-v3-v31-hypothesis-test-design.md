# V3 vs V31 Edge Source 판별 설계

**날짜**: 2026-04-15
**상태**: 설계 승인 대기
**배경 참조**: `strategy_sports_v31.md` 메모리, `reference_odds_api_freshness.md` 메모리, V4 Phase 1+2 완료 이후 10전략 paper 운영 중

---

## Context

현재 sports V3 (regions=us) 와 V31 (regions=eu) 전략은 논리가 동일하되 Odds API
region 만 다름. Paper 운영 데이터 (2026-04-15 기준):

| pair | us tickers | eu tickers | overlap | eu_only |
|---|---|---|---|---|
| longshot | 81 | 26 | 26 (100%) | 0 |
| midshot | 84 | 36 | 36 (100%) | 0 |
| underdog | 103 | 57 | 57 (100%) | 0 |

**V31 은 V3 의 순수 부분집합**. direction 도 overlap 119건 모두 same_dir.
us-only 149건 PnL = **−$89.91** (건당 −$0.60). V31 단독 PnL = +$195.57.

이 상관은 3가지 서로 다른 메커니즘으로 설명 가능:

- **가설 A (signal quality)**: Pinnacle 은 업계에서 가장 샤프한 북마커. eu regions
  컨센서스에 Pinnacle 이 포함되어 있어 미국 retail 북마커 기반 us 컨센서스보다
  정확. eu 가 엄격 threshold 를 만드는 이유는 **우월한 가격 발견 신호** 때문.
- **가설 B (stale-driven loss)**: Pinnacle 이 지연 (freshness 883s 측정됨) 되어
  봇이 과거값으로 판단. 뉴스가 반영된 Kalshi 를 역선택해 손실. 현 +$195 와 모순이라
  단독 가능성 낮음.
- **가설 C (slow filter artifact)**: Pinnacle 지연이 **노이즈 필터**로 작용 —
  15분 이상 지속된 괴리만 잡히므로 잡음성 entry 제거. us 는 짧은 튐도 따라가
  149건 losing trades 떠안음. 문제는 이 필터 효과가 **Odds API 개선 또는 Kalshi
  참가자의 Pinnacle 직접 참조 확산 시 소멸**. 장기 지속 불투명.

현 paper 수치로는 3 가설 구별 불가. 본 설계는 판별 프레임워크 + 필요한 instrument
+ decision rule 을 확립.

## Goals

2주 후 다음 중 하나의 결론 도출:

1. **A 확정** → V3 비활성, V31 size up 검토
2. **C 확정** → V3 유지 (or 둘 다 재설계), size up 금지
3. **혼재/불명** → 샘플 추가, 결정 유보

## Non-Goals

- 전략 로직 수정 (진입 threshold, sizing 등 변경 없음)
- V3 를 판별 기간 중 비활성화 (us-only entry 데이터 필요)
- V4 전략들에 대한 별도 판별 (본 설계는 V3/V31 한정; V4 는 후속)
- Live 전환 결정

## Part 1 — Decision framework

### 판별 지표 (forward 데이터 기준)

| 지표 | A 보강 | C 보강 |
|---|---|---|
| V31 entry 중 `pinnacle_age_s` ≥ 300s 비율 | < 30% | > 70% |
| V31 entry 중 `pinnacle_age_s` < 60s 비율 | > 50% | < 20% |
| V31 fresh-quartile 의 CLV 평균 | 양수, overall 과 유사 | 0 근처 또는 음수 |
| V31 stale-quartile 의 CLV 평균 | overall 과 유사 | overall 보다 유의미 큼 (stale 에 수익 집중) |
| V3 us-only 진입 시 `pinnacle_prob` vs `us_consensus_prob` 차이 | 차이 작음 (V31 이 거른 이유 = threshold noise) | 차이 크고 결과가 us 방향 (Pinnacle 이 틀림) |
| us-only CLV 평균 | adverse (음수) | adverse (음수) — 이 지표는 양쪽에 모두 부합, 판별력 없음 |

### Scoring

각 지표에 대해 A 기준 충족 시 A_score +1, C 기준 충족 시 C_score +1. 둘 다
미충족 (중간값) 이면 양쪽 모두 0. `us-only CLV` 행은 판별력 없어 점수 산출에서
제외 (표에는 진단용으로 남김).

최대 A_score = 5, 최대 C_score = 5.

| 조건 | Recommendation |
|---|---|
| A_score ≥ 4 AND C_score ≤ 1 | DISABLE_V3, size up 검토 |
| C_score ≥ 4 AND A_score ≤ 1 | HOLD, V3/V31 재설계 검토 |
| 총 settled V31 < 50 | MORE_DATA (confidence LOW) |
| 그 외 | HOLD_AND_RECHECK (confidence MEDIUM, 1주 추가) |

## Part 2 — Forward instrumentation

### 2.1 `clv_snapshots` 테이블 확장

```sql
ALTER TABLE clv_snapshots ADD COLUMN us_consensus_prob REAL;
ALTER TABLE clv_snapshots ADD COLUMN eu_consensus_prob REAL;
ALTER TABLE clv_snapshots ADD COLUMN pinnacle_age_s INTEGER;
ALTER TABLE clv_snapshots ADD COLUMN kalshi_yes_bid REAL;
ALTER TABLE clv_snapshots ADD COLUMN kalshi_yes_ask REAL;
```

- 모든 컬럼 NULL 허용 — 기존 568 row 는 백필 안 함 (없는 데이터).
- 신규 entry 부터 채워짐.
- 반대 region 추가 호출은 **진입 결정 시점 1회/trade** 만. 스캔 루프의 모든 tick
  마다가 아님.

### 2.2 Odds client 확장

[core/odds_client.py](../../core/odds_client.py) 의 consensus 계산 함수가 현재
prob 만 반환. 반환 타입을 `(prob: float, age_s: int | None, pinnacle_hash: str | None)` 로 확장.

- `age_s`: 같은 payload hash 또는 `pinnacle_hash` 가 마지막으로 "변했던" 시점부터
  경과 초. 현재 `odds_freshness_log.jsonl` 포맷이 이미 `pinnacle_hash` 저장
  → 내부에 in-memory LRU 로 `(region, sport, hash) → first_seen_ts` 기록 후 조회.
- 캐시 히트 시에도 age 는 직전 miss 시점부터 측정 (캐시는 freshness 보장 아님).

### 2.3 Entry snapshot 기록 지점

[core/clv_tracker.py](../../core/clv_tracker.py) 또는 snapshot 삽입 지점을 찾아
entry 저장 시:

- 현재 region (전략별 us/eu) 기준 consensus 외에 **반대 region 도 한 번 fetch**
  후 두 값 모두 snapshot 에 저장.
- 대부분 이미 다른 전략이 반대 region 을 호출했기 때문에 region-aware 캐시
  (`architecture_api_caching.md` 메모리) 상 추가 API 호출 거의 없음.
- snapshot 에 `pinnacle_age_s` 저장.
- Kalshi orderbook 의 top-of-book bid/ask 도 저장 (현재는 trades.price 만 있어
  mid/ask 구분 안 됨).

### 2.4 Closing snapshot pinnacle_prob

현재 closing snapshot 의 `pinnacle_prob` 이 NULL. Settle 시점에 Odds API 호출
결과로 채움. 마켓이 이미 resolve 되어 호가 갱신 중단된 경우 직전 tick 값 사용.

### 2.5 Freshness 지속 로깅

`main.py` 스캔 루프에 5분 주기 task 추가. 기존 `odds_freshness_log.jsonl` 포맷
그대로 append. 4/14 1시간 측정 이후 중단된 수집 재개.

### 2.6 비변경 사항

- 전략 진입 로직 (threshold, sizing, dedup)
- 대시보드 UI
- Telegram 알림
- 환경설정 (env flag 등)

## Part 3 — Analysis scripts

### 3.1 즉시 실행 스크립트

파일: `scripts/research/v3_v31_overlap_analysis.py`

기능:

**(a) CLV 방향성**
- 모든 `paper_settled` trade 대상. `closing_kalshi - entry_kalshi` 를 진입 방향
  기준 부호 맞춰 계산.
- 3 bucket: overlap us-side, overlap eu-side, us-only.
- 각 bucket 의 mean, median, positive rate, win rate, PnL 출력.

**(b) V31 entry Pinnacle-Kalshi gap 분포**
- V31 entry snapshot (n≈110) 의 `pinnacle_prob` vs `trades.price` gap.
- 히스토그램, 평균, 중앙값, 분위수 (25/50/75).

**(c) Pinnacle-predicts-closing**
- V31 overlap 거래 subset 에서 "entry 당시 Pinnacle 이 Kalshi 보다 더 맞은
  방향" 인지 판정 (closing 시 Kalshi 가 Pinnacle 쪽으로 이동했는가).
- 수렴률 + 평균 이동 폭.

출력: `docs/research/v3_v31_overlap_<YYYY-MM-DD>.md` — 숫자 + 1단락 해석.

### 3.2 2주 후 확장 (forward data 포함)

같은 스크립트 확장 또는 신규 `v3_v31_forward_decision.py`. 추가:

**(d) us-only 의 Pinnacle view 검증**
- V3 us-only entry 의 `pinnacle_prob` / `eu_consensus_prob`.
- 3 sub-case 분류 (Part 1 표 5행) 및 건수 집계.

**(e) Pinnacle freshness 분포**
- V31 entry / V3 entry / us-only entry 각각의 `pinnacle_age_s` 히스토그램.
- Part 1 의 300s / 60s 기준 비율.

**(f) CLV × freshness 상관**
- V31 entry 를 `pinnacle_age_s` 4분위수로 분할.
- 각 분위수의 CLV 평균, win rate, PnL.

**(g) Decision 자동 출력**
- Part 1 의 A_score / C_score 계산.
- Recommendation + confidence 출력.

## Components

| 파일 | 변경 유형 | 비고 |
|---|---|---|
| [core/db.py](../../core/db.py) | 마이그레이션 | `CREATE TABLE IF NOT EXISTS` 패턴 유지, `ALTER TABLE ADD COLUMN` 추가 |
| [core/odds_client.py](../../core/odds_client.py) | 반환 타입 확장 | consensus 호출부 모두 수정 필요 |
| [core/clv_tracker.py](../../core/clv_tracker.py) | snapshot write 확장 | entry + closing 양쪽 |
| 각 sports 전략 (v3, v31, v4 공통 entry path) | 반대 region consensus fetch 추가 | 가능한 한 공통 helper 로 |
| [main.py](../../main.py) | 5분 주기 freshness task 추가 | |
| `scripts/research/v3_v31_overlap_analysis.py` | 신규 | |
| `docs/research/` 디렉터리 | 신규 | 분석 리포트 output |

## Data flow

```
Entry 결정 시점:
  odds_client.get_consensus(region=us) → (us_prob, us_age, us_hash)
  odds_client.get_consensus(region=eu) → (eu_prob, eu_age, eu_hash)  [추가 호출]
  kalshi_client.get_orderbook(ticker) → (bid, ask)
  → clv_tracker.record_entry(trade_id, us_prob, eu_prob,
                             pinnacle_age_s=eu_age, kalshi_yes_bid, kalshi_yes_ask, ...)

Pre-close (기존 동작 유지 + pinnacle 추가):
  → record_pre_close(trade_id, yes_ask, no_ask, pinnacle_prob)

Closing (기존 동작 유지 + pinnacle 추가):
  → record_closing(trade_id, yes_ask, no_ask, pinnacle_prob)

Analysis:
  scripts/research/v3_v31_overlap_analysis.py
    - DB 읽기 (trades + clv_snapshots join)
    - 버킷팅, 통계, decision scoring
    - docs/research/<date>.md 생성
```

## Error handling

- Odds API 이중 호출 중 한쪽 실패: 실패한 쪽 값만 NULL. 전략 entry 는 기존 region
  값으로 진행 (신호 자체는 기존 로직).
- `pinnacle_age_s` 계산 실패 (hash 캐시 miss 등): NULL.
- ALTER TABLE 실패 (이미 컬럼 존재): 무시 (idempotent).

## Testing

- **Unit**: odds_client 의 age 계산 로직 (hash 변경 감지), snapshot write 의 새
  필드 포함 여부.
- **Integration**: 실제 DB 에 마이그레이션 돌려 컬럼 존재 확인. 신규 entry 한
  건이 모든 필드 채우는지.
- **Manual**: 배포 후 10분 내 V31 entry 1건 확인 후 DB 직접 쿼리.

## Verification

1. `pytest` 전체 pass.
2. EC2 배포 후 `clv_snapshots` 에 신규 컬럼 존재 확인.
3. V31 entry 발생 시 `us_consensus_prob` / `eu_consensus_prob` / `pinnacle_age_s`
   / `kalshi_yes_bid` / `kalshi_yes_ask` 5개 모두 NULL 아님 확인.
4. `odds_freshness_log.jsonl` 에 5분 주기로 새 라인 append 확인.
5. 즉시 실행 스크립트 돌려 `docs/research/v3_v31_overlap_<date>.md` 생성 확인.

## Risks

- **API 쿼터**: Odds API 이중 호출. 캐시 덕에 실질 증가 미미 예상이나, 캐시 miss
  구간에서 quota 증가 가능. monitoring 필요.
- **2주 후에도 혼재 결과**: Part 1 의 MORE_DATA 분기로 fallback. 일정 지연.
- **Pinnacle freshness 가 그 사이 변함**: Odds API 측 변경이 생기면 측정 baseline
  흔들림. `odds_freshness_log.jsonl` 에 지속 기록되므로 사후 설명 가능.
- **V3/V31 외 V4 전략 영향**: 본 설계는 V3/V31 판별용이지만 instrumentation 자체는
  모든 전략의 entry 에 적용. 부수적으로 V4 데이터도 수집됨 (후속 분석 재활용 가능).

## Post-implementation follow-up

- 2026-04-29 (2주 후) 시점에 forward 분석 실행.
- 결과에 따라 별도 spec (V3 비활성 또는 재설계) 작성.
- 메모리 업데이트: `strategy_sports_v31.md` 에 판별 결과 append.
