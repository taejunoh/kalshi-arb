# 내 베팅 — 표시함 픽 조회 및 수정

**날짜:** 2026-08-12
**상태:** 승인됨

## 문제

앱에서 픽을 "베팅함"으로 표시하면 그 픽은 피드에서 사라지고, 사용자에게 남는 단서는
`PicksScreen`의 빈 상태에 뜨는 카운트 한 줄(`표시함 26 · 무시함 3`)뿐이다. 목록도, 상세도,
정정 수단도 없다. 결과:

- **오표시를 되돌릴 수 없다.** "베팅함"은 `pick_state`에 영구 기록되고 `undismiss_group`이
  의도적으로 `state='bet'`을 건드리지 않으므로, 실수로 표시한 픽은 영영 피드로 돌아오지 않는다.
- **기록값을 고칠 수 없다.** 스테이크·실체결 배당을 잘못 입력해도 `app_bet` 행을 수정하는
  경로가 없다. 이 값들은 캘린더 PnL의 입력이다.
- **`GET /mybets`가 이미 있는데 앱이 쓰지 않는다.** API는 미정산 베팅 + 일별 PnL 캘린더를
  반환하지만 `app/src/api/client.ts`에 메서드조차 없고 대응 화면도 없다.

## 범위

`app_bet` 원장을 기준으로 한 새 탭 "내 베팅"에서 조회하고, 세 필드(북·스테이크·실체결 배당)를
수정하고, 오표시를 되돌린다(표시 해제).

**범위 밖:** 무시함(dismissed) 픽 목록 — 이미 5초 실행취소가 있다. 새 베팅을 처음부터
입력하는 경로 — `CaptureSheet`가 담당한다. 정산 로직 변경 — 해제된 행 skip 외에는
손대지 않는다.

## 원장 규칙과의 관계 (load-bearing)

`scripts/softbook_app_db.py`의 규칙: *app_bet 행은 베팅 기록이다 — 측정 위생을 위해 삭제하거나
재가격하지 말고 세그먼트하라.* 2026-06-11에 12행을 삭제했다가 전량 복구를 요구받은 사건에서
나온 규칙이다.

따라서 "되돌리기"는 **삭제가 아니라 플래그**로 구현한다. 행은 영구히 남고, 집계에서만 빠지고,
목록에는 해제 표시로 계속 보인다. 되돌리기 자체도 되돌릴 수 있어야 한다 — 실제 실패 모드는
판단 착오가 아니라 오터치이고(PR#76의 5초 실행취소와 같은 근거), 되돌릴 수 없는 표시 해제는
삭제의 축소판이 된다.

## 데이터 모델

`app_bet`에 2열 추가. `_ensure_app_bet_columns()`의 기존 idempotent ALTER 패턴에 얹으므로
별도 마이그레이션 스크립트가 필요 없다.

| 컬럼 | 타입 | 의미 |
|---|---|---|
| `not_placed` | `INTEGER DEFAULT 0` | 1 = 표시는 했지만 실제로 성립하지 않은 베팅 |
| `not_placed_at` | `TEXT` | ISO8601 UTC. 되돌릴 때 NULL |

### 이름을 `voided`로 하지 않는 이유

`softbook_results.py`에서 `'void'`는 이미 **정산 결과**의 한 값이다 — 경기가 취소·연기됐거나
워크오버·기권이라 베팅이 무효가 된 경우(`softbook_results.py:307`, `:318`). "사용자가 실제로
걸지 않았음"을 같은 단어로 표현하면 두 개념이 영구히 뒤섞인다. `not_placed`는 두 사유를 모두
덮으면서(내가 표시만 했다 / 북이 베팅을 거절했다) 정산 어휘와 충돌하지 않는다.

### `not_placed`가 전파되어야 하는 곳

빠뜨리면 각각 다음 증상이 난다. **`softbook_push_worker.py`가 특히 중요하다** — 이걸 빼면
"베팅 안 했다고 처리한 픽이 이겼다"는 푸시가 날아온다.

| 파일 | 라인 | 조건 추가 | 누락 시 증상 |
|---|---|---|---|
| `softbook_app_settle.py` | 70 | `AND not_placed=0` | 안 건 베팅이 정산돼 PnL에 들어감 |
| `softbook_app_settle.py` | 148 | `AND not_placed=0` | 무결과 경보가 안 건 베팅을 물고 늘어짐 |
| `softbook_push_worker.py` | 131 | `AND a.not_placed=0` | 안 건 베팅의 정산 결과 푸시 발송 |
| `softbook_live_grade.py` | 80 | `AND not_placed=0` | 라이브 진행 추적 대상에 남음 |
| `softbook_app_api.py` | 580 | `AND not_placed=0` | 캘린더 PnL 오염 |
| `softbook_app_api.py` | 587 | `AND not_placed=0` | 미정산 목록 오염 |

추가로 `pick_state` — 표시 해제 시 해당 픽의 `'bet'` 행을 삭제해 픽이 활성 피드로 복귀한다.

### 정산된 베팅의 표시 해제

**허용한다.** 오표시된 베팅이 자동 정산까지 탔다면 그 날짜의 PnL은 *이미* 틀린 상태이고,
표시 해제가 그것을 바로잡는 유일한 수단이다. 다만 과거 숫자가 조용히 움직여서는 안 되므로,
정산분 해제는 확인 다이얼로그에 변경될 날짜와 금액을 명시한다:
`"8월 9일 PnL이 -$45.00 변경됩니다"`.

## API

`scripts/softbook_app_api.py`.

### `GET /mybets` (확장)

현재는 미정산 행만 반환한다. 확장:

```
{
  "calendar":   { "2026-08-09": 45.0, ... },  // not_placed 제외 (동작 변경)
  "open":       [ ... ],                       // settled=0 AND not_placed=0
  "settled":    [ ... ],                       // settled=1 AND not_placed=0, settled_at DESC, 최근 90일
  "not_placed": [ ... ]                        // not_placed=1, not_placed_at DESC
}
```

행에는 `id`, `pnl`, `won`, `settled_at`, `not_placed`를 포함한다. `id`는 수정·해제 호출의 키다.

`open`은 기존 계약대로 `commence_time` 오름차순을 유지한다. `settled`는 90일로 창을 두어
페이로드가 무한히 자라지 않게 한다 — 그보다 오래된 기록은 대시보드 History의 몫이다.

### `PATCH /bets/{id}`

```
{ "book": str|null, "stake": float|null, "fill_odds": float|null }
```

**세 필드만** 수정한다. 나머지는 서버가 거부한다(422). 근거:

- `event_id`/`market`/`side`/`point`/`player`를 바꾸는 것은 수정이 아니라 다른 베팅이다.
  바꾸고 싶으면 기존 행을 표시 해제하고 새로 기록해야 원장이 정직하다.
- `rec_odds`는 사용자 입력이 아니라 추천 시점에 시스템이 기록한 관측치다. 수정 가능하게 하면
  CLV 계산이 오염된다.
- `settled`/`won`/`pnl`/`settled_at`은 정산 워커의 소유다.

정산된 행(`settled=1`)의 PATCH는 거부한다(409) — 스테이크를 바꾸면 이미 계산된 PnL과
어긋나고, 그 상황에서 사용자가 원하는 것은 대개 표시 해제다. 응답에서 그렇게 안내한다.

`not_placed=1` 행의 PATCH도 거부한다(409). 먼저 표시를 복원해야 한다.

### `POST /bets/{id}/unmark`

"이 베팅은 성립하지 않았다"로 표시한다. `not_placed=1`, `not_placed_at=now()` 설정 후 해당
`(event_id, market, side, point, player)`의 `pick_state` 'bet' 행을 삭제한다. 이미
`not_placed=1`이면 no-op으로 `{"ok": true}`(멱등).

`pick_state` 삭제는 `undismiss_group`과 달리 **단일 행만** 건드린다 — bet 상태에는
reverse-middle 자동 처리가 없으므로 쓸어담을 그룹이 존재하지 않는다.

응답: `{"ok": true, "pnl_delta": float|null, "settled_day": str|null}` — 앱이 확인
다이얼로그 이후 캘린더 변화를 즉시 반영할 수 있게 한다. 미정산 행이면 둘 다 `null`.

### `DELETE /bets/{id}/unmark`

표시 해제를 되돌린다. `not_placed=0`, `not_placed_at=NULL`, `pick_state`를 `'bet'`으로 복원.
`not_placed=0`이면 no-op.

## 앱

### `MyBetsScreen` (신규, 하단 탭 3번째)

`Feather` `file-text` 아이콘, 제목 "내 베팅". `RootNavigator`의 `Tabs.Navigator`에 추가하고
`TabParamList`를 확장한다.

세 구획을 `SectionList`로 렌더한다:

- **미정산** — 경기(`matchup`), 라벨, 북, 실배당, 스테이크, 경기 시작 시각
- **정산됨** — 위 + PnL(승 `color.positive` / 패 `color.negative`)
- **베팅 안 함** — 위 + 취소선, `color.faint`, "되돌리기" 액션

상단에 이번 달 PnL 요약 한 줄. 빈 상태는 "기록된 베팅 없음".
`useFocusEffect`로 재조회하고 `onRefresh`로 당겨서 새로고침 — `PicksScreen`과 같은 패턴.

### `BetEditSheet` (신규, 모달)

`CaptureSheet`와 같은 입력 스타일을 쓰되 별도 화면으로 둔다(`CaptureSheet`는 이미 스캔 모드와
픽 모드 두 갈래를 담고 있어서, 세 번째 모드를 넣으면 한 파일이 세 가지 일을 하게 된다).

- 읽기 전용 헤더: 경기 + 라벨 + 마켓 (수정 불가임을 시각적으로 명시)
- 입력 3개: 북(텍스트), 스테이크(`decimal-pad`), 실체결 배당(미국식, `parseOddsToDecimal`)
- 저장 → `PATCH /bets/{id}` → `popToTop`
- 하단 파괴적 액션: **"베팅 안 했음"** → 확인 다이얼로그 → `POST /bets/{id}/unmark`
  - 정산분이면 다이얼로그에 PnL 변경 문구를 넣는다
  - 성공 시 "이 픽이 피드로 돌아갑니다" 안내

### `api/client.ts`

```ts
getMyBets:   () => request<MyBetsResponse>("/mybets"),
patchBet:    (id, body) => request<{ok: boolean}>(`/bets/${id}`, {method: "PATCH", ...}),
unmarkBet:   (id) => request<UnmarkResponse>(`/bets/${id}/unmark`, {method: "POST"}),
remarkBet:   (id) => request<{ok: boolean}>(`/bets/${id}/unmark`, {method: "DELETE"}),
```

`api/types.ts`에 `AppBet`, `MyBetsResponse`, `UnmarkResponse`, `BetPatch` 추가.

## 오류 처리

- **PATCH 409 (정산됨 / 이미 해제됨)** — 시트에 사유와 함께 표시. 정산됨이면
  "베팅 안 했음" 액션은 남긴다(그게 그 상황의 올바른 출구다).
- **unmark 실패** — 낙관적 업데이트를 하지 않는다. 표시 해제는 5초 실행취소가 아니라 확인
  다이얼로그로 게이트되므로, 서버 확인 후 반영하는 편이 정직하다. 실패 시 알럿 + 재조회.
- **자격증명 없음** — `PicksScreen`과 동일하게 오류 화면 + 재시도 버튼.

## 테스트

### API (`tests/test_softbook_app_api.py`)

1. unmark → 해당 픽이 `GET /picks`의 활성 피드에 다시 나타난다
2. unmark → `GET /mybets` 캘린더에서 그 날짜 PnL이 빠진다
3. unmark → 정산 워커(`settle_app_bets`)가 그 행을 건너뛴다
4. **unmark → 푸시 워커가 정산 결과 알림을 보내지 않는다** (전파 누락 회귀 방지)
5. unmark → 라이브 그레이더가 그 행을 건너뛴다
6. `PATCH`가 화이트리스트 밖 필드(`market`, `rec_odds`)를 422로 거부한다
7. 정산된 행 `PATCH` → 409
8. unmark → remark 왕복 후 `pick_state`가 `'bet'`으로 복원된다
9. unmark 멱등성 (두 번 호출해도 `pick_state`가 이상해지지 않는다)
10. `GET /mybets`가 `open`/`settled`/`not_placed`를 올바로 분류한다

### 앱

1. `MyBetsScreen` — 세 구획이 각각 렌더되고, 빈 구획은 나타나지 않는다
2. `MyBetsScreen` — "베팅 안 함" 행에 취소선 스타일이 적용된다
3. `BetEditSheet` — 저장이 세 필드만 담아 `patchBet`을 호출한다
4. `BetEditSheet` — "베팅 안 했음"이 확인 다이얼로그를 거친다
5. 정산분 확인 다이얼로그에 PnL 변경 문구가 들어간다

**UI 텍스트 검증은 정규식이 아니라 정확 일치로 한다.** PR#75에서 깨진 JSX 리터럴이
`/🟣 BET/` 정규식을 통과해 6일간 OTA 3회를 탔다. 추가로 "깨진 소스 텍스트가 화면에 없음"을
검증한다.

## 배포

`app_bet` 컬럼 추가는 스키마 변경이지만 `_ensure_app_bet_columns`가 기동 시 처리하므로
API 재시작만으로 적용된다. 배포는 `git push ec2 master`.

앱 변경은 JS 전용(새 화면 + 탭 하나)이므로 **OTA로 나간다** — `eas update --channel production`.
`app.json`을 건드리지 않으므로 fingerprint가 바뀌지 않는다. OTA는 앱 재실행 2회가 필요하다
(1회차 다운로드, 2회차 적용).
