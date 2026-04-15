# V3 vs V31 가설 테스트 — Follow-up 가이드

배포일: 2026-04-15
Live 활성화: 2026-04-15 13:27 UTC (V31 3개 + mentions 기존)
Spec: [docs/superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md](../superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md)
Plan: [docs/superpowers/plans/2026-04-15-v3-v31-hypothesis-test.md](../superpowers/plans/2026-04-15-v3-v31-hypothesis-test.md)

---

## 1️⃣ 단기 검증 — 새 컬럼 실제로 채워지는지 (24시간 내)

### 언제 하나

배포 후 **sports entry 최소 1건 발생 뒤**. NBA/NHL/MLB 게임 시작 2–6시간 전이 진입 피크이므로 **EDT 저녁 시간대 이후** 에 확인하면 거의 확실히 데이터 있음.

### 나한테 뭐라고 하면 되나

> "V3/V31 entry 컬럼 채워졌는지 확인해"

또는 동등한 표현: "새 instrumentation 잘 들어오는지 봐", "Task 9 검증 마저 해"

### 내가 할 것

```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "python3 -c '
import sqlite3
c = sqlite3.connect(\"/home/ubuntu/kalshi-arb/data/bot.db\")
for r in c.execute(\"\"\"
SELECT t.strategy, s.us_consensus_prob, s.eu_consensus_prob,
       s.pinnacle_age_s, s.kalshi_yes_ask, s.snapshot_time
FROM trades t JOIN clv_snapshots s ON s.trade_id=t.id
WHERE s.snapshot_at=\"entry\" AND s.us_consensus_prob IS NOT NULL
ORDER BY s.id DESC LIMIT 10\"\"\"): print(r)
'"
```

+ freshness 로그 growth 확인:
```bash
ssh -i ~/Desktop/LFG/pem/kalshi-bot.pem ubuntu@18.223.21.52 "wc -l ~/kalshi-arb/data/odds_freshness_log.jsonl; tail -1 ~/kalshi-arb/data/odds_freshness_log.jsonl"
```

**성공 기준**:
- 신규 row ≥ 1건, 모든 5개 컬럼 non-NULL
- freshness log 라인 수: 배포 당시 378 → 이후 5분에 6라인씩 증가 (시간당 약 72)

**실패 시 나에게 보고될 내용**: 어떤 컬럼이 NULL 인지, 원인 추정 (Odds API 실패 / 캐시 miss / 로직 오류 등). 수정 필요하면 follow-up 수정 commit.

---

## 2️⃣ 본격 판별 — 2주 후 A/C 결정 (2026-04-29 이후)

### 언제 하나

**2026-04-29** 목표. 조금 이르게 (1주, 4-22 경) 돌려도 되지만 샘플 부족으로 `confidence=LOW / MORE_DATA` 뜰 가능성 큼. 샘플 여유로 2주 꽉 채우는 걸 권장.

### 나한테 뭐라고 하면 되나

> "V3 V31 forward 분석 돌려"

또는: "2주 지났다 결정하자", "Task 10", "가설 테스트 결정할 시간"

### 내가 할 것

1. **Task 10 구현** — [plan 의 Task 10](../superpowers/plans/2026-04-15-v3-v31-hypothesis-test.md) 에 명시된 확장:
   - (d) us-only entry 의 Pinnacle view 분류 (3-case)
   - (e) `pinnacle_age_s` 히스토그램 (V31 / us / us-only 별)
   - (f) CLV × freshness 분위수 상관
   - (g) A_score / C_score / Recommendation 자동 산출

2. **실행**: EC2 최신 DB snapshot 으로 분석

3. **리포트 생성**: `docs/research/v3_v31_forward_<date>.md`

4. **Recommendation 에 따라**:
   - `DISABLE_V3`: V3 (sports_longshot/midshot/underdog) 비활성 spec 작성 → 구현 → 배포
   - `HOLD`: 1주 더 샘플 누적 후 재확인
   - `MORE_DATA`: 최소 샘플 채우는 데 필요한 기간 제시

### 결과 해석 cheatsheet

[spec Part 1](../superpowers/specs/2026-04-15-v3-v31-hypothesis-test-design.md) 의 판별 지표에서 각 기준 통과 여부로 A_score / C_score (각 최대 5).

| 조건 | 의미 |
|---|---|
| `A_score ≥ 4 AND C_score ≤ 1` | **가설 A 확정** — Pinnacle 이 구조적으로 샤프함. V3 비활성 + V31 size up 안전 |
| `C_score ≥ 4 AND A_score ≤ 1` | **가설 C 확정** — edge 는 stale filter artifact. Odds API 개선 시 소멸. size up 위험, V3/V31 재설계 검토 |
| V31 settled < 50 | `MORE_DATA` — 판단 보류, 샘플 더 |
| 그 외 | `HOLD_AND_RECHECK` — 1주 추가 대기 |

---

## 3️⃣ 기타 (선택)

### 데이터 이상 조기 경보

매일 또는 주 2-3회 체크하고 싶으면:
> "V31 오늘 몇 건 들어왔어? pinnacle_age 평균은?"

나는 `SELECT COUNT(*), AVG(pinnacle_age_s) FROM clv_snapshots WHERE ...` 쿼리로 확인. pinnacle_age_s 평균이 **급격히 감소 (Odds API 개선 신호)** 하거나 **급격히 증가 (feed 이상)** 하면 조기 경보.

### V31 잠정 size up 테스트

2주 기다리는 동안 중간 확인이 필요하면:
> "V31 size 조심스럽게 키워봐도 돼?"

나는 샘플/CLV/현재 per-trade edge 를 체크하고 "지금은 위험 / 조금 OK / 괜찮음" 판단 드림. 기본 답변은 **2주 전에는 size 건드리지 말 것** 이지만 시장 상황에 따라 조정 가능.

---

## 요약 — 한 줄 버전

| 시점 | 내가 치는 말 |
|---|---|
| 오늘~내일 | "V3/V31 entry 컬럼 채워졌는지 확인해" |
| 2026-04-29 | "V3 V31 forward 분석 돌려" |
| 중간 점검 | "V31 오늘 상황 어때?" |
