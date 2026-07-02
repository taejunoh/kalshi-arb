# Audit Prompts — Claude 에게 데이터 기반 분석/결정을 시킬 때 사용

이 문서는 portfolio audit + LIVE/gate 결정을 일관되게 가져가기 위한 프롬프트 모음.
세션마다 `"성능 확인해"` 만 쳐도 자동으로 multi-step audit 가동되도록 메모리에 박혀있음 ([portfolio-audit-playbook](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/portfolio_audit_playbook.md)).

---

## 일상 점검 (가벼움)

| 프롬프트 | 발동 단계 | 결과 |
|---|---|---|
| `성능 확인해` / `전략 분석` / `돈 벌고 있어?` | Step 1+2 | 7/14/30d portfolio 표 + 분류 (winner / watch / demote / dormant) |
| `paper 도 봐` / `전체 봐` / `다 봐` | Step 3 추가 | LIVE+paper 전체 + dormant 사유까지 |

## 의사결정 트리거 (중요)

| 프롬프트 | 발동 단계 | 결과 |
|---|---|---|
| `by-band 분석해` / `어디서 잃어?` / `어느 가격대?` | Step 4 | 5c → 2c fine-grained band counterfactual |
| `확실한거지?` / `mistake 가능성 있어?` / `다시 확인` | Step 5 self-audit | confidence 등급 + rollback plan |
| `forward projection 보여줘` / `고치면 얼마 벌어?` | Step 6 | current vs projected 30d net 표 |

## Action 트리거

| 프롬프트 | 의미 |
|---|---|
| `진행해` | 단일 action 승인 |
| `싹 다 진행해` / `전부 실행` | 여러 action 한 번에 |
| `재추천으로 진행` | Claude 재분석 후 새 추천 실행 |

---

## 권장 워크플로우

```
1. "성능 확인해"
   → 7/14/30d snapshot, 어디가 문제인지 식별

2. (문제 strategy 발견 시) "<X> by-band 분석"
   → fine-grained 으로 bleed 위치 정확히 파악

3. (추천 받으면) "확실한거지? mistake 가능성 있어?"
   → Claude self-audit, confidence rating 매김

4. "재추천으로 진행" 또는 "싹 다 진행"
   → 실행
```

---

## 정기 점검 권장 일정

| When | 프롬프트 |
|---|---|
| **매일 13:00 UTC daily_audit 직후** | `오늘 audit 결과 어때? 추가 분석 필요?` |
| **매주 일요일** | `이번 주 portfolio 전체 review 해줘 — by-band 까지` |
| **gate 조정 전 항상** | `by-band counterfactual 먼저 보여줘` |
| **새 LIVE 후보 검토 시** | `<X> 30d by-band 어때? promotion 해도 돼?` |

---

## 자동 발동되는 안전장치 (Claude 가 anyway 함)

다음은 메모리에 박힌 protocol — 사용자가 굳이 요구 안 해도 Claude 가 자동으로 함:

1. LIVE 변경 전 7/14/30d 셋 다 봄 ([feedback-data-driven-decisions](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/feedback_data_driven_decisions.md))
2. Gate 변경 전 by-band counterfactual ([portfolio-audit-playbook step 4](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/portfolio_audit_playbook.md))
3. 변경 전 cross-session state 검증 ([feedback-verify-state-before-proposing](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/feedback_verify_state_before_proposing.md))
4. Demote 후보가 small-n (<20) 이면 "watch" 권장 (not "act")
5. Paper-also-firing 의심 체크 (mothball trader instance 까지)

만약 Claude 가 이걸 빠뜨리면 → `"확실한거지? data 다 봤어?"` 한 마디로 self-audit 강제 발동.

---

## Confidence 등급 의미

Claude 가 추천할 때 confidence 같이 줌:

| 등급 | 의미 | 사용자 대응 |
|---|---|---|
| **95%+** | mechanical proof, large n | 바로 진행 OK |
| **80-95%** | solid data, monitor next audit | 진행하되 다음 audit 시 재검증 |
| **60-80%** | smaller sample / uncertain regime | rollback plan 명시되어 있는지 확인 |
| **<60%** | do NOT deploy | "더 데이터 모은 후" 명시. 진행 X |

---

## 흔한 함정 (Claude 가 자동 회피)

1. **Aggregate ROI 가 bleeds 숨김** — cme_basis 30d -7.5% 가 "tighten" 으로 보였지만 sweet spot 이 0.81-0.82 였음
2. **Single-window 판단 misleading** — v4_edge 14d -$2 → demote 후보 같았지만 30d +$54 / +45% ROI 였음
3. **Paper 가 LIVE demote 후에도 계속 firing** — sports_underdog_v32 -$184 / 7d, mothball 필요했음
4. **Memory 가 historical** — 3주 전 "X 가 best" 메모가 regime shift 로 뒤집힐 수 있음. 항상 현재 DB 재검증
5. **Small-sample +EV 는 variance** — n<20 alpha 는 "watch" 만, "act" 금지
6. **Paper PnL 부기 아티팩트** (7/02) — live_fade 페이퍼 +$193 중 $181.5 가 부기 버그 4행이었음. 페이퍼 헤드라인 인용 전 톱행 게이트 정합 + 부기가격 실재성 + 정직 리플레이 대조
7. **Lifetime 이 게이트 변경일 걸침** (7/02) — tennis_underdog WTA -23% 가 은퇴 밴드 유령이었음. 분해는 현행-게이트 조건 + era-split 필수
8. **"멀티윈도우 일관" = 표본 중복 착시** (7/02) — n 작으면 30d = 전체 코호트, 14d = 부분집합. 창별 n 병기 필수
9. **집중도 논리는 대칭 적용** (7/02) — 지는 서브셋을 집중도로 깎으면 이기는 서브셋도 같은 검사 (WTA 손실도 ATP 수익도 둘 다 윔블던 집중이었음)

---

## 관련 메모리 entry

- [portfolio-audit-playbook](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/portfolio_audit_playbook.md) — 6-step systematic procedure (SQL 템플릿 포함)
- [feedback-data-driven-decisions](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/feedback_data_driven_decisions.md) — 핵심 룰
- [by-band-audit-2026-05-25](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/by_band_audit_2026-05-25.md) — 첫 적용 + 발견된 mistake 3건
- [retire_arc_2026-05-25](/Users/taejunoh/.claude/projects/-Users-taejunoh-Desktop-LFG-kalshi-arb/memory/retire_arc_2026-05-25.md) — 워크플로우 예시 (4-wave cleanup)
