# 내 베팅 (표시함 픽 조회·수정) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 앱에서 "표시함"으로 처리된 베팅을 `app_bet` 원장 기준으로 조회하고, 북/스테이크/실체결 배당을 수정하고, 오표시를 되돌릴 수 있게 한다.

**Architecture:** 되돌리기는 행 삭제가 아니라 `app_bet.not_placed` 플래그다 (원장 규칙: 행 삭제·재가격 금지). 이 플래그를 6개 쿼리 지점이 필터하고, 플래그를 세울 때 `pick_state`의 `'bet'` 행을 지워 픽이 활성 피드로 복귀한다. 앱에는 `/mybets`를 읽는 새 탭과 3필드 편집 모달을 추가한다.

**Tech Stack:** FastAPI + pydantic v2 + SQLite (백엔드), Expo/React Native + TypeScript + jest-expo + @testing-library/react-native (앱)

## Global Constraints

- **원장 규칙 (load-bearing):** `app_bet` 행은 절대 DELETE 하지 않고 `fill_odds`/`stake`를 측정 목적으로 재작성하지 않는다. 상태 변경은 플래그로만 한다. (`scripts/softbook_app_db.py:15`)
- **컬럼명은 `not_placed` / `not_placed_at`.** `voided`는 금지 — `softbook_results.py`의 `'void'`(경기 취소·기권으로 인한 정산 무효)와 충돌한다.
- **수정 가능 필드는 `book`, `stake`, `fill_odds` 세 개뿐.** `rec_odds`는 CLV 입력이라 불변, `event_id`/`market`/`side`/`point`/`player`는 바꾸면 다른 베팅, `settled`/`won`/`pnl`/`settled_at`은 정산 워커 소유.
- **UI 텍스트 테스트는 정규식이 아니라 정확 일치로 검증한다.** PR#75에서 깨진 JSX 리터럴이 `/🟣 BET/` 정규식을 통과해 6일간 배포됐다.
- **SQL 날짜 비교는 반드시 `datetime()`으로 감싼다.** ISO-T 타임스탬프는 문자열 비교가 깨진다.
- **앱 UI 문자열은 한국어.** 기존 화면과 동일.
- **`git add`는 파일 경로를 명시한다.** `git add .` 금지 — 사용자의 병행 작업물이 섞인다.
- **Python 테스트:** 리포지토리 루트에서 `./venv/bin/pytest`. **앱 테스트:** `npm --prefix app test -- <경로>`.
- **pydantic v2 문법** (`model_config = ConfigDict(...)`, `model_dump(exclude_unset=True)`). 기존 코드의 `.dict()`는 v1 잔재이므로 따라 하지 않는다.

## File Structure

**백엔드 (수정)**
- `scripts/softbook_app_db.py` — 컬럼 2개 + 헬퍼 4개 (`get_app_bet`, `update_app_bet`, `set_not_placed`, `clear_bet_state`)
- `scripts/softbook_app_settle.py` — 쿼리 2곳 필터
- `scripts/softbook_push_worker.py` — 쿼리 1곳 필터
- `scripts/softbook_live_grade.py` — 쿼리 1곳 필터
- `scripts/softbook_app_api.py` — `/mybets` 확장, `PATCH /bets/{id}`, `POST`/`DELETE /bets/{id}/unmark`

**백엔드 (테스트 수정)**
- `tests/test_softbook_app_db.py`, `tests/test_softbook_app_settle.py`, `tests/test_app_settle_stuck.py`, `tests/test_softbook_push.py`, `tests/test_softbook_live_grade.py`, `tests/test_softbook_app_api.py`

**앱 (생성)**
- `app/src/screens/MyBetsScreen.tsx` + `.test.tsx` — 3구획 목록
- `app/src/screens/BetEditSheet.tsx` + `.test.tsx` — 3필드 편집 + 표시 해제

**앱 (수정)**
- `app/src/api/types.ts`, `app/src/api/client.ts`, `app/src/navigation/types.ts`, `app/src/navigation/index.tsx`

---

### Task 1: `app_bet.not_placed` 컬럼과 DB 헬퍼

**Files:**
- Modify: `scripts/softbook_app_db.py:65-73` (`_ensure_app_bet_columns`), 파일 끝에 헬퍼 추가
- Test: `tests/test_softbook_app_db.py`

**Interfaces:**
- Consumes: 기존 `_norm(pick)`, `APP_BET_COLS`, `init_schema`
- Produces:
  - `get_app_bet(db, bet_id) -> dict | None` — 모든 컬럼을 담은 dict
  - `update_app_bet(db, bet_id, fields: dict) -> int` — `book`/`stake`/`fill_odds`만 반영, 변경 행 수 반환
  - `set_not_placed(db, bet_id, flag: bool, now: str | None) -> bool` — 행 존재 여부 반환
  - `clear_bet_state(db, pick: tuple) -> int` — 삭제된 `pick_state` 행 수

- [ ] **Step 1: Write the failing tests**

`tests/test_softbook_app_db.py` 끝에 추가:

```python
def test_app_bet_has_not_placed_columns_defaulting_off():
    db = mem()
    cols = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    assert {"not_placed", "not_placed_at"} <= cols
    adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert db.execute("SELECT not_placed, not_placed_at FROM app_bet"
                      ).fetchone() == (0, None)


def test_ensure_columns_upgrades_a_pre_existing_table():
    """The column lands on an EC2 db that already has rows — the ALTER path,
    not the CREATE path."""
    db = sqlite3.connect(":memory:")
    db.execute("""CREATE TABLE app_bet(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
        market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
        book TEXT, rec_odds REAL, fill_odds REAL, stake REAL,
        placed_at TEXT, source TEXT,
        live_status TEXT DEFAULT 'pending', live_progress TEXT,
        settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT)""")
    db.execute("INSERT INTO app_bet(event_id, market, side) VALUES ('E1','h2h','NYM')")
    db.commit()
    adb.init_schema(db)
    assert db.execute("SELECT not_placed FROM app_bet").fetchone() == (0,)


def test_get_app_bet_returns_a_dict_or_none():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM",
                                  "stake": 20.0, "book": "FanDuel"})
    row = adb.get_app_bet(db, bid)
    assert row["id"] == bid and row["book"] == "FanDuel" and row["stake"] == 20.0
    assert row["not_placed"] == 0
    assert adb.get_app_bet(db, bid + 999) is None


def test_update_app_bet_writes_only_the_three_editable_columns():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM",
                                  "book": "FanDuel", "stake": 20.0, "fill_odds": 2.1,
                                  "rec_odds": 2.2})
    n = adb.update_app_bet(db, bid, {"book": "DraftKings", "stake": 25.0,
                                     "fill_odds": 2.05, "rec_odds": 9.9,
                                     "market": "spread"})
    assert n == 1
    row = adb.get_app_bet(db, bid)
    assert (row["book"], row["stake"], row["fill_odds"]) == ("DraftKings", 25.0, 2.05)
    assert row["rec_odds"] == 2.2 and row["market"] == "h2h"   # untouched


def test_update_app_bet_with_no_editable_field_is_a_noop():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert adb.update_app_bet(db, bid, {"market": "spread"}) == 0


def test_set_not_placed_flips_the_flag_and_stamps_the_time():
    db = mem()
    bid = adb.insert_app_bet(db, {"event_id": "E1", "market": "h2h", "side": "NYM"})
    assert adb.set_not_placed(db, bid, True, "2026-08-12T10:00:00+00:00") is True
    row = adb.get_app_bet(db, bid)
    assert row["not_placed"] == 1 and row["not_placed_at"] == "2026-08-12T10:00:00+00:00"
    adb.set_not_placed(db, bid, False, None)
    row = adb.get_app_bet(db, bid)
    assert row["not_placed"] == 0 and row["not_placed_at"] is None
    assert adb.set_not_placed(db, bid + 999, True, "x") is False


def test_clear_bet_state_removes_bet_only_and_leaves_dismissed():
    """A dismissed row is a preference; a bet row is what we are undoing here.
    Clearing one must never sweep up the other."""
    db = mem()
    bet = ("E1", "h2h", "NYM", 0, "")
    other = ("E2", "h2h", "MIA", 0, "")
    adb.set_pick_state(db, bet, "bet", "t1")
    adb.set_pick_state(db, other, "dismissed", "t1")
    assert adb.clear_bet_state(db, bet) == 1
    states = adb.pick_states(db)
    assert ("E1", "h2h", "NYM", 0.0, "") not in states
    assert states[("E2", "h2h", "MIA", 0.0, "")] == "dismissed"
    assert adb.clear_bet_state(db, bet) == 0          # idempotent
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/pytest tests/test_softbook_app_db.py -v -k "not_placed or get_app_bet or update_app_bet or clear_bet_state or ensure_columns"
```

Expected: FAIL — `AttributeError: module 'softbook_app_db' has no attribute 'get_app_bet'` 및 `sqlite3.OperationalError: no such column: not_placed`

- [ ] **Step 3: Add the columns**

`scripts/softbook_app_db.py`의 `_ensure_app_bet_columns`를 교체:

```python
def _ensure_app_bet_columns(db):
    """Idempotently add columns to an existing app_bet table."""
    have = {r[1] for r in db.execute("PRAGMA table_info(app_bet)")}
    # Written by the retired price-move pusher (2026-08-05). Kept so existing rows
    # keep their columns; nothing writes or reads them now.
    # not_placed: the user marked a pick bet but the bet never happened (mis-tap,
    # or the book rejected it). NOT called 'voided' — softbook_results already
    # uses 'void' for a settlement outcome (cancelled/walkover game), and one word
    # for two concepts would fuse them permanently. The row is never deleted
    # (ledger rule); every consumer filters not_placed=0 instead.
    for name, decl in (("current_best_odds", "REAL"),
                       ("last_odds_check_at", "TEXT"),
                       ("not_placed", "INTEGER DEFAULT 0"),
                       ("not_placed_at", "TEXT")):
        if name not in have:
            db.execute(f"ALTER TABLE app_bet ADD COLUMN {name} {decl}")
```

같은 파일의 `APP_BET_SCHEMA`에도 두 컬럼을 추가해 신규 DB가 ALTER 없이 바로 갖게 한다:

```python
APP_BET_SCHEMA = """CREATE TABLE IF NOT EXISTS app_bet(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT, sport TEXT, home TEXT, away TEXT, commence_time TEXT,
    market TEXT, side TEXT, point REAL, player TEXT, label TEXT,
    book TEXT, rec_odds REAL, fill_odds REAL, stake REAL,
    placed_at TEXT, source TEXT,
    live_status TEXT DEFAULT 'pending', live_progress TEXT,
    settled INTEGER DEFAULT 0, won INTEGER, pnl REAL, settled_at TEXT,
    not_placed INTEGER DEFAULT 0, not_placed_at TEXT)"""
```

- [ ] **Step 4: Add the helpers**

`scripts/softbook_app_db.py`의 `insert_app_bet` 바로 아래에 추가:

```python
_EDITABLE_BET_COLS = ("book", "stake", "fill_odds")


def get_app_bet(db, bet_id):
    """One app_bet row as a dict (all columns), or None."""
    cur = db.execute("SELECT * FROM app_bet WHERE id=?", (bet_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def update_app_bet(db, bet_id, fields):
    """Write the three user-entered columns. Anything else in `fields` is
    ignored on purpose — event/market/side describe a DIFFERENT bet, rec_odds is
    a system observation feeding CLV, and settled/won/pnl belong to the settle
    worker. Returns rows changed."""
    cols = [c for c in _EDITABLE_BET_COLS if c in fields]
    if not cols:
        return 0
    sets = ", ".join(f"{c}=?" for c in cols)
    cur = db.execute(f"UPDATE app_bet SET {sets} WHERE id=?",
                     (*(fields[c] for c in cols), bet_id))
    db.commit()
    return cur.rowcount


def set_not_placed(db, bet_id, flag, now):
    """Mark (or unmark) a bet as never actually placed. The row stays — see the
    LEDGER RULE at the top of this module. Returns True if the row exists."""
    cur = db.execute("UPDATE app_bet SET not_placed=?, not_placed_at=? WHERE id=?",
                     (1 if flag else 0, now if flag else None, bet_id))
    db.commit()
    return cur.rowcount > 0


def clear_bet_state(db, pick):
    """Delete this pick's state='bet' override so it returns to the live feed.
    Single row only, and only state='bet' — unlike undismiss_group there is no
    reverse-middle group to sweep, and a 'dismissed' row is a separate
    preference that this must not touch. Returns rows deleted."""
    cur = db.execute(
        "DELETE FROM pick_state WHERE event_id=? AND market=? AND side=?"
        " AND point=? AND player=? AND state='bet'", _norm(pick))
    db.commit()
    return cur.rowcount
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_softbook_app_db.py -v
```

Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 6: Commit**

```bash
git add scripts/softbook_app_db.py tests/test_softbook_app_db.py
git commit -m "feat(app-db): not_placed flag + app_bet accessors

행 삭제 없이 오표시를 되돌리기 위한 플래그. 'voided'를 피한 건
softbook_results의 'void'(경기 취소 정산)와 이름이 충돌하기 때문."
```

---

### Task 2: `not_placed` 행을 4개 워커 쿼리에서 제외

**Files:**
- Modify: `scripts/softbook_app_settle.py:70`, `scripts/softbook_app_settle.py:148`
- Modify: `scripts/softbook_push_worker.py:131`
- Modify: `scripts/softbook_live_grade.py:80`
- Test: `tests/test_softbook_app_settle.py`, `tests/test_app_settle_stuck.py`, `tests/test_softbook_push.py`, `tests/test_softbook_live_grade.py`

**Interfaces:**
- Consumes: Task 1의 `not_placed` 컬럼
- Produces: 없음 (동작 변경만)

이 태스크가 빠지면 "베팅 안 했다고 처리한 픽이 이겼다"는 푸시가 날아간다. 푸시 워커가 가장 중요하다.

- [ ] **Step 1: Write the failing tests**

`tests/test_softbook_app_settle.py` 끝에 추가:

```python
def test_not_placed_bets_are_never_settled():
    """A bet the user says never happened must not enter PnL."""
    db = _db_with_bet()
    db.execute("UPDATE app_bet SET not_placed=1")
    db.commit()
    n = settle.settle_app_bets(
        db, key="k",
        fetch_scores=lambda *a, **k: pytest.fail("must not fetch for a not-placed bet"),
        player_stat=lambda *a, **k: pytest.fail("must not grade a not-placed bet"))
    assert n == 0
    assert db.execute("SELECT settled FROM app_bet").fetchone() == (0,)
```

`tests/test_softbook_app_settle.py` 상단 import에 `import pytest`가 없으면 추가한다.

`tests/test_app_settle_stuck.py`의 `db` 픽스처 CREATE TABLE에 컬럼을 추가하고(아래 Step 3), 테스트를 추가:

```python
def test_not_placed_bets_never_go_stuck(db):
    """An unsettleable bet the user already disowned is not an alarm."""
    db.execute("INSERT INTO app_bet(sport, label, book, stake, commence_time,"
               " settled, not_placed) VALUES ('baseball_kbo','x','FanDuel',20,"
               " datetime('now','-15 days'), 0, 1)")
    db.commit()
    assert settle.stuck_bets(db) == []
```

`tests/test_softbook_push.py` 끝에 추가:

```python
def test_worker_does_not_push_settlement_for_a_not_placed_bet():
    """Regression: without the not_placed filter the user gets a "정산 결과"
    push for a bet they told the app never happened."""
    ev, pdb = _worker_dbs()
    pdb.execute("INSERT INTO app_bet(event_id,market,side,point,player,label,book,"
                "fill_odds,stake,settled,won,pnl,settled_at,not_placed) VALUES "
                "('E1','batter_total_bases','Over',1.5,'Brandon Nimmo','x','FanDuel',"
                "2.2,2.0,1,1,2.4,'2026-06-12T22:00:00+00:00',1)")
    pdb.commit()
    sent = []
    worker.run_once(ev, pdb, send=lambda msgs: sent.extend(msgs) or [])
    assert [m for m in sent if m.get("categoryId") == "settle"] == []
```

`tests/test_softbook_live_grade.py` 끝에 추가:

```python
def test_grade_skips_not_placed_bets():
    db = sqlite3.connect(":memory:")
    import softbook_app_db as adb
    adb.init_schema(db)
    db.execute("INSERT INTO app_bet(event_id,sport,market,side,point,player,settled,"
               "not_placed) VALUES ('E1','baseball_mlb','batter_total_bases',"
               "'Over',1.5,'Brandon Nimmo',0,1)")
    db.commit()
    assert lg.grade_open_bets(db, fetch_stat=lambda *a: (5, "Final")) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/pytest tests/test_softbook_app_settle.py tests/test_app_settle_stuck.py tests/test_softbook_push.py tests/test_softbook_live_grade.py -v -k "not_placed"
```

Expected: FAIL — 4건 모두. settle/live_grade/push는 필터가 없어 행을 집어가고, stuck 테스트는 `no such column: not_placed`.

- [ ] **Step 3: Add the filters**

`scripts/softbook_app_settle.py:70` — `settle_app_bets`의 쿼리:

```python
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player, home, away,"
        " commence_time, fill_odds, stake"
        " FROM app_bet WHERE settled=0 AND not_placed=0"
    ).fetchall()
```

`scripts/softbook_app_settle.py:148` — `stuck_bets`의 쿼리:

```python
                "SELECT id, sport, label, book, stake, commence_time FROM app_bet"
                " WHERE settled=0 AND not_placed=0"
                "   AND sport IS NOT NULL AND commence_time IS NOT NULL"
                "   AND datetime(replace(commence_time,'Z','')) < datetime('now', ?)"
                " ORDER BY commence_time", (f"-{int(hours)} hours",))]
```

`scripts/softbook_push_worker.py:131` — `_settle_messages`의 쿼리:

```python
    rows = pdb.execute(
        "SELECT a.id, a.label, a.won, a.pnl FROM app_bet a"
        " LEFT JOIN notified_settle n ON n.app_bet_id=a.id"
        " WHERE a.settled=1 AND a.not_placed=0 AND n.app_bet_id IS NULL").fetchall()
```

`scripts/softbook_live_grade.py:80` — `grade_open_bets`의 쿼리:

```python
    rows = db.execute(
        "SELECT id, sport, event_id, market, side, point, player FROM app_bet"
        " WHERE settled=0 AND not_placed=0").fetchall()
```

`tests/test_app_settle_stuck.py`의 `db` 픽스처 — 이 테이블은 손으로 만든 축소판이라 새 컬럼을 직접 넣어야 한다:

```python
@pytest.fixture
def db():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE app_bet(id INTEGER PRIMARY KEY, sport TEXT, label TEXT,"
              " book TEXT, stake REAL, commence_time TEXT, settled INT,"
              " not_placed INT DEFAULT 0)")
    return c
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_softbook_app_settle.py tests/test_app_settle_stuck.py tests/test_softbook_push.py tests/test_softbook_live_grade.py -v
```

Expected: PASS (기존 테스트 포함 전부)

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_settle.py scripts/softbook_push_worker.py scripts/softbook_live_grade.py tests/test_softbook_app_settle.py tests/test_app_settle_stuck.py tests/test_softbook_push.py tests/test_softbook_live_grade.py
git commit -m "fix(app): not_placed 베팅을 정산·푸시·라이브그레이딩에서 제외

푸시 워커 필터가 특히 중요 — 없으면 '베팅 안 함'으로 처리한 픽의
정산 결과 알림이 날아간다."
```

---

### Task 3: `GET /mybets` 확장 — settled / not_placed 구획

**Files:**
- Modify: `scripts/softbook_app_api.py:573-597` (`get_mybets`)
- Test: `tests/test_softbook_app_api.py`

**Interfaces:**
- Consumes: Task 1의 `not_placed` 컬럼
- Produces: `GET /mybets` → `{calendar, open, settled, not_placed}`. 각 행은 `id`, `settled`, `won`, `pnl`, `settled_at`, `not_placed`, `not_placed_at`을 포함한다. Task 6의 `MyBetsResponse` 타입이 이 모양에 맞춰진다.

- [ ] **Step 1: Write the failing tests**

`tests/test_softbook_app_api.py`의 `test_mybets_groups_open_and_calendar` 바로 아래에 추가:

```python
def _place_bet(client, **over):
    body = {"event_id": "E1", "sport": "baseball_mlb", "market": "batter_total_bases",
            "side": "Over", "point": 1.5, "player": "Brandon Nimmo",
            "label": "Brandon Nimmo TB Over 1.5", "book": "theScore",
            "fill_odds": 2.2, "stake": 2.0}
    body.update(over)
    r = client.post("/bets", json=body, headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    return r.json()["id"]


def test_mybets_splits_open_settled_and_not_placed(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at=strftime('%Y-%m-%dT%H:%M:%S+00:00','now') WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert body["open"] == []
    assert [b["id"] for b in body["settled"]] == [bid]
    assert body["settled"][0]["pnl"] == 2.4 and body["settled"][0]["won"] == 1
    assert body["not_placed"] == []


def test_mybets_excludes_not_placed_from_calendar_and_lists(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-06-12T22:00:00+00:00', not_placed=1,"
                " not_placed_at='2026-08-12T10:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    assert body["calendar"] == {}                       # PnL no longer counted
    assert body["settled"] == [] and body["open"] == []
    assert [b["id"] for b in body["not_placed"]] == [bid]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py -v -k "mybets"
```

Expected: FAIL — `KeyError: 'settled'` (응답에 구획이 없음)

- [ ] **Step 3: Rewrite `get_mybets`**

`scripts/softbook_app_api.py`의 `get_mybets`를 통째로 교체:

```python
_MYBET_COLS = ("id, event_id, sport, home, away, commence_time, market, side,"
               " point, player, label, book, fill_odds, stake, live_status,"
               " live_progress, settled, won, pnl, settled_at, not_placed,"
               " not_placed_at")

# 90 days: the app screen is a working record, not an archive — the dashboard
# History owns the long tail. Keeps the payload bounded as app_bet grows.
_SETTLED_WINDOW_D = 90


def _mybet_rows(pdb, tail, params=()):
    keys = [c.strip() for c in _MYBET_COLS.split(",")]
    rows = [dict(zip(keys, r)) for r in
            pdb.execute(f"SELECT {_MYBET_COLS} FROM app_bet WHERE {tail}", params)]
    for o in rows:
        if o.get("live_progress"):
            try:
                o["live_progress"] = json.loads(o["live_progress"])
            except Exception:
                pass
    return rows


@app.get("/mybets")
def get_mybets(_=Depends(require_auth)):
    """The bet ledger, split by what the user can do with each row: open (waiting),
    settled (done), not_placed (disowned — kept visible because the row is never
    deleted). not_placed is excluded from the calendar: its PnL is not real."""
    pdb = adb.connect()
    try:
        cal = {}
        for day, pnl in pdb.execute(
                "SELECT substr(settled_at,1,10) d, COALESCE(sum(pnl),0)"
                " FROM app_bet WHERE settled=1 AND settled_at IS NOT NULL"
                " AND not_placed=0 GROUP BY d"):
            cal[day] = round(pnl, 2)
        open_rows = _mybet_rows(
            pdb, "settled=0 AND not_placed=0 ORDER BY commence_time")
        settled = _mybet_rows(
            pdb, "settled=1 AND not_placed=0 AND settled_at IS NOT NULL"
                 " AND datetime(settled_at) >= datetime('now', ?)"
                 " ORDER BY settled_at DESC", (f"-{_SETTLED_WINDOW_D} days",))
        not_placed = _mybet_rows(pdb, "not_placed=1 ORDER BY not_placed_at DESC")
    finally:
        pdb.close()
    return {"calendar": cal, "open": open_rows,
            "settled": settled, "not_placed": not_placed}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py -v -k "mybets"
```

Expected: PASS (신규 2건 + 기존 `test_mybets_groups_open_and_calendar`)

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(api): /mybets를 open/settled/not_placed 3구획으로 확장"
```

---

### Task 4: `PATCH /bets/{id}` — 3필드 수정

**Files:**
- Modify: `scripts/softbook_app_api.py` (`post_bet` 아래에 추가), import 한 줄
- Test: `tests/test_softbook_app_api.py`

**Interfaces:**
- Consumes: Task 1의 `adb.get_app_bet`, `adb.update_app_bet`
- Produces: `PATCH /bets/{bet_id}` — 본문 `{book?, stake?, fill_odds?}`, 응답 `{"ok": true}`. 그 외 필드는 422, 정산·해제된 행은 409.

- [ ] **Step 1: Write the failing tests**

`tests/test_softbook_app_api.py`에 추가 (Task 3의 `_place_bet` 헬퍼 재사용):

```python
def test_patch_bet_updates_the_three_editable_fields(client):
    bid = _place_bet(client)
    r = client.patch(f"/bets/{bid}", json={"book": "DraftKings", "stake": 25.0,
                                           "fill_odds": 2.05},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    body = client.get("/mybets", headers={"Authorization": "Bearer secret"}).json()
    row = body["open"][0]
    assert (row["book"], row["stake"], row["fill_odds"]) == ("DraftKings", 25.0, 2.05)


def test_patch_bet_rejects_fields_that_would_make_it_a_different_bet(client):
    bid = _place_bet(client)
    for payload in ({"market": "h2h"}, {"rec_odds": 9.9}, {"pnl": 100.0}):
        r = client.patch(f"/bets/{bid}", json=payload,
                         headers={"Authorization": "Bearer secret"})
        assert r.status_code == 422, payload


def test_patch_bet_on_a_settled_row_is_conflict(client):
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, pnl=2.4 WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    r = client.patch(f"/bets/{bid}", json={"stake": 25.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 409


def test_patch_bet_on_a_not_placed_row_is_conflict(client):
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers={"Authorization": "Bearer secret"})
    r = client.patch(f"/bets/{bid}", json={"stake": 25.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 409


def test_patch_unknown_bet_is_404(client):
    r = client.patch("/bets/9999", json={"stake": 1.0},
                     headers={"Authorization": "Bearer secret"})
    assert r.status_code == 404
```

`test_patch_bet_on_a_not_placed_row_is_conflict`는 Task 5의 엔드포인트에 의존한다 — Task 4 단계에서는 404로 실패하고, Task 5 완료 후 통과한다. Task 4의 Step 4에서는 나머지 4건만 확인한다.

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py -v -k "patch_bet"
```

Expected: FAIL — 전부 405 Method Not Allowed (`PATCH` 라우트 없음)

- [ ] **Step 3: Add the endpoint**

`scripts/softbook_app_api.py`의 import를 수정:

```python
from pydantic import BaseModel, ConfigDict
```

`post_bet` 함수 바로 아래에 추가:

```python
class BetPatch(BaseModel):
    """The only three fields the user typed, so the only three they may retype.

    extra="forbid" makes anything else a 422 rather than a silent no-op:
    event/market/side/point/player describe a DIFFERENT bet (void this one and
    record a new one instead), rec_odds is the system's observation at
    recommendation time and feeds CLV, and settled/won/pnl belong to the
    settle worker."""
    model_config = ConfigDict(extra="forbid")
    book: str | None = None
    stake: float | None = None
    fill_odds: float | None = None


@app.patch("/bets/{bet_id}")
def patch_bet(bet_id: int, body: BetPatch, _=Depends(require_auth)):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(422, "no editable fields supplied")
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if row["settled"]:
            # Editing the stake now would contradict a pnl already computed from
            # it. The honest move in that situation is /unmark.
            raise HTTPException(409, "settled bet — 수정 불가, 표시 해제를 쓰세요")
        if row["not_placed"]:
            raise HTTPException(409, "not-placed bet — 먼저 표시를 복원하세요")
        adb.update_app_bet(pdb, bet_id, fields)
    finally:
        pdb.close()
    return {"ok": True}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py -v -k "patch_bet and not not_placed"
```

Expected: PASS 4건 (`test_patch_bet_on_a_not_placed_row_is_conflict`는 Task 5 이후)

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(api): PATCH /bets/{id} — book/stake/fill_odds만 수정

extra=forbid 로 나머지 필드는 422. 정산된 행은 409(표시 해제가 올바른 출구)."
```

---

### Task 5: `POST` / `DELETE /bets/{id}/unmark` — 표시 해제와 복원

**Files:**
- Modify: `scripts/softbook_app_api.py` (Task 4의 `patch_bet` 아래)
- Test: `tests/test_softbook_app_api.py`

**Interfaces:**
- Consumes: Task 1의 `adb.get_app_bet`, `adb.set_not_placed`, `adb.clear_bet_state`, 기존 `adb.set_pick_state`
- Produces:
  - `POST /bets/{bet_id}/unmark` → `{"ok": true, "pnl_delta": float|null, "settled_day": str|null}`
  - `DELETE /bets/{bet_id}/unmark` → `{"ok": true}`

  Task 6의 `UnmarkResponse` 타입, Task 8의 확인 다이얼로그 문구가 `pnl_delta`/`settled_day`에 의존한다.

- [ ] **Step 1: Write the failing tests**

`tests/test_softbook_app_api.py`에 추가:

```python
def test_unmark_returns_the_pick_to_the_active_feed(client):
    """The whole point: a mis-marked pick becomes actionable again."""
    ev = sqlite3.connect(os.environ["SOFTBOOK_EV_DB"])
    ev.execute("DELETE FROM softbook_ev")
    ev.execute("INSERT INTO softbook_ev VALUES (datetime('now'),"
               "'baseball_mlb','E1','2099-01-01T00:00:00Z','MIA','NYM',"
               "'spread','NYM',-1.5,'',0.45,2.0,'theScore',2.2,0.074,9,5,60,'High')")
    ev.commit(); ev.close()
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client, market="spread", side="NYM", point=-1.5, player="",
                     label="NYM -1.5")
    assert client.get("/picks", headers=h).json()["picks"] == []      # hidden
    r = client.post(f"/bets/{bid}/unmark", headers=h)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "pnl_delta": None, "settled_day": None}
    picks = client.get("/picks", headers=h).json()["picks"]
    assert [p["id"] for p in picks] == ["E1|spread|NYM|-1.5|"]         # back


def test_unmark_of_a_settled_bet_reports_the_calendar_change(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    pdb.execute("UPDATE app_bet SET settled=1, won=1, pnl=2.4,"
                " settled_at='2026-08-09T22:00:00+00:00' WHERE id=?", (bid,))
    pdb.commit(); pdb.close()
    r = client.post(f"/bets/{bid}/unmark", headers=h).json()
    assert r["pnl_delta"] == -2.4 and r["settled_day"] == "2026-08-09"


def test_unmark_is_idempotent(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    for _ in range(2):
        assert client.post(f"/bets/{bid}/unmark", headers=h).status_code == 200
    import softbook_app_db as adb2
    pdb = adb2.connect()
    n = pdb.execute("SELECT count(*) FROM pick_state").fetchone()[0]
    rows = pdb.execute("SELECT count(*) FROM app_bet").fetchone()[0]
    pdb.close()
    assert n == 0 and rows == 1          # state gone, ledger row still there


def test_unmark_never_deletes_the_ledger_row(client):
    """LEDGER RULE. If this ever fails, the feature has become a delete button."""
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)
    import softbook_app_db as adb2
    pdb = adb2.connect()
    row = adb2.get_app_bet(pdb, bid)
    pdb.close()
    assert row is not None and row["not_placed"] == 1
    assert row["not_placed_at"] is not None
    assert row["stake"] == 2.0 and row["fill_odds"] == 2.2   # not repriced


def test_delete_unmark_restores_the_bet_state(client):
    h = {"Authorization": "Bearer secret"}
    bid = _place_bet(client)
    client.post(f"/bets/{bid}/unmark", headers=h)
    r = client.delete(f"/bets/{bid}/unmark", headers=h)
    assert r.status_code == 200
    assert client.get("/picks", headers=h).json()["counts"].get("bet") == 1
    body = client.get("/mybets", headers=h).json()
    assert body["not_placed"] == [] and [b["id"] for b in body["open"]] == [bid]


def test_unmark_unknown_bet_is_404(client):
    h = {"Authorization": "Bearer secret"}
    assert client.post("/bets/9999/unmark", headers=h).status_code == 404
    assert client.delete("/bets/9999/unmark", headers=h).status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py -v -k "unmark"
```

Expected: FAIL — 404/405 (라우트 없음)

- [ ] **Step 3: Add the endpoints**

`scripts/softbook_app_api.py`의 `patch_bet` 아래에 추가:

```python
def _pick_of(row):
    """The pick_state key for an app_bet row."""
    return (row["event_id"], row["market"], row["side"],
            row["point"] or 0, row["player"] or "")


@app.post("/bets/{bet_id}/unmark")
def unmark_bet(bet_id: int, _=Depends(require_auth)):
    """"이 베팅은 성립하지 않았다" — a mis-tap, or the book rejected the slip.

    The row is NOT deleted: app_bet rows are bet records (LEDGER RULE in
    softbook_app_db). It gets not_placed=1, which the settle worker, the push
    worker, the live grader and /mybets all filter out, and its pick_state 'bet'
    override is dropped so the pick returns to the live feed.

    Allowed on a settled bet too: if a mis-marked bet already auto-settled, that
    day's PnL is ALREADY wrong and this is the only way to fix it. The response
    reports the change so the app can say which day moves by how much rather
    than letting a past number shift in silence.

    Reversible: DELETE the same path."""
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if not row["not_placed"]:
            adb.set_not_placed(pdb, bet_id, True, _now())
            adb.clear_bet_state(pdb, _pick_of(row))
    finally:
        pdb.close()
    # `row` is the state BEFORE this call, so a repeat unmark correctly reports
    # no change. Only a settled bet moves the calendar.
    if row["not_placed"] or not row["settled"]:
        return {"ok": True, "pnl_delta": None, "settled_day": None}
    return {"ok": True,
            "pnl_delta": -(row["pnl"] or 0),
            "settled_day": (row["settled_at"] or "")[:10] or None}


@app.delete("/bets/{bet_id}/unmark")
def remark_bet(bet_id: int, _=Depends(require_auth)):
    """Undo an unmark: the bet counts again and the pick re-hides from the feed."""
    pdb = adb.connect()
    try:
        row = adb.get_app_bet(pdb, bet_id)
        if row is None:
            raise HTTPException(404, "no such bet")
        if row["not_placed"]:
            adb.set_not_placed(pdb, bet_id, False, None)
            adb.set_pick_state(pdb, _pick_of(row), "bet", _now())
    finally:
        pdb.close()
    return {"ok": True}
```

- [ ] **Step 4: Run the full API + db test suite**

```bash
./venv/bin/pytest tests/test_softbook_app_api.py tests/test_softbook_app_db.py -v
```

Expected: PASS 전부 — Task 4에서 미뤄둔 `test_patch_bet_on_a_not_placed_row_is_conflict` 포함

- [ ] **Step 5: Commit**

```bash
git add scripts/softbook_app_api.py tests/test_softbook_app_api.py
git commit -m "feat(api): POST/DELETE /bets/{id}/unmark — 오표시 되돌리기

행 삭제가 아니라 not_placed 플래그 + pick_state 'bet' 해제.
정산분도 허용하되 pnl_delta/settled_day를 돌려줘 캘린더 변화를 명시."
```

---

### Task 6: 앱 API 타입과 클라이언트 메서드

**Files:**
- Modify: `app/src/api/types.ts` (파일 끝)
- Modify: `app/src/api/client.ts:38-68` (`api` 객체)
- Test: `app/src/api/client.test.ts` (없으면 생성)

**Interfaces:**
- Consumes: Task 3–5의 엔드포인트
- Produces: `AppBet`, `MyBetsResponse`, `UnmarkResponse`, `BetPatch` 타입과 `api.getMyBets`, `api.patchBet`, `api.unmarkBet`, `api.remarkBet`. Task 7·8이 이 이름들을 그대로 쓴다.

- [ ] **Step 1: Write the failing test**

`app/src/api/client.test.ts`를 생성 (이미 있으면 아래 테스트만 추가):

```tsx
jest.mock("../storage", () => ({
  getCredentials: jest.fn(async () => ({
    baseUrl: "https://x.test", token: "t", cfId: "i", cfSecret: "s",
  })),
}));

import { api } from "./client";

const okJson = (body: unknown) =>
  Promise.resolve({ ok: true, status: 200, json: async () => body } as Response);

beforeEach(() => {
  global.fetch = jest.fn(() => okJson({ ok: true })) as unknown as typeof fetch;
});

test("getMyBets reads /mybets", async () => {
  (global.fetch as jest.Mock).mockReturnValueOnce(
    okJson({ calendar: {}, open: [], settled: [], not_placed: [] }));
  const r = await api.getMyBets();
  expect((global.fetch as jest.Mock).mock.calls[0][0]).toBe("https://x.test/mybets");
  expect(r.not_placed).toEqual([]);
});

test("patchBet sends PATCH with only the supplied fields", async () => {
  await api.patchBet(7, { stake: 25 });
  const [url, init] = (global.fetch as jest.Mock).mock.calls[0];
  expect(url).toBe("https://x.test/bets/7");
  expect(init.method).toBe("PATCH");
  expect(JSON.parse(init.body)).toEqual({ stake: 25 });
});

test("unmarkBet POSTs and remarkBet DELETEs the same path", async () => {
  await api.unmarkBet(7);
  await api.remarkBet(7);
  const calls = (global.fetch as jest.Mock).mock.calls;
  expect(calls[0][0]).toBe("https://x.test/bets/7/unmark");
  expect(calls[0][1].method).toBe("POST");
  expect(calls[1][0]).toBe("https://x.test/bets/7/unmark");
  expect(calls[1][1].method).toBe("DELETE");
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm --prefix app test -- src/api/client.test.ts
```

Expected: FAIL — `api.getMyBets is not a function`

- [ ] **Step 3: Add the types**

`app/src/api/types.ts` 끝에 추가:

```ts
// ===== 내 베팅 (app_bet 원장) =====
// A row of the bet ledger. `not_placed` = the user said this bet never happened
// (mis-tap, or the book rejected it). The row is NEVER deleted — it is a record —
// so the app renders it struck through instead of hiding it.
export interface AppBet {
  id: number;
  event_id: string | null;
  sport: string | null;
  home: string | null;
  away: string | null;
  commence_time: string | null;
  market: string | null;
  side: string | null;
  point: number | null;
  player: string | null;
  label: string | null;
  book: string | null;
  fill_odds: number | null;
  stake: number | null;
  live_status: string | null;
  live_progress: unknown;
  settled: number;
  won: number | null;
  pnl: number | null;
  settled_at: string | null;
  not_placed: number;
  not_placed_at: string | null;
}

export interface MyBetsResponse {
  /** day (YYYY-MM-DD) -> settled PnL, not_placed excluded */
  calendar: Record<string, number>;
  open: AppBet[];
  settled: AppBet[];
  not_placed: AppBet[];
}

/** POST /bets/{id}/unmark. pnl_delta/settled_day are non-null only when the
 *  unmarked bet had already settled — the app names the calendar change. */
export interface UnmarkResponse {
  ok: boolean;
  pnl_delta: number | null;
  settled_day: string | null;
}

/** The only three fields the server accepts — anything else is a 422. */
export interface BetPatch {
  book?: string | null;
  stake?: number | null;
  fill_odds?: number | null;
}
```

- [ ] **Step 4: Add the client methods**

`app/src/api/client.ts`의 import에 타입을 추가 (`AppBet`은 이 파일에서 직접 참조하지 않으므로
넣지 않는다 — 미사용 import는 lint 오류다):

```ts
import type {
  BetIn,
  BetPatch,
  MuteRule,
  MyBetsResponse,
  Pick,
  PicksResponse,
  RefreshResponse,
  ScanDraft,
  Settings,
  UnmarkResponse,
} from "./types";
```

`api` 객체의 `postBet` 아래에 추가:

```ts
  getMyBets: () => request<MyBetsResponse>("/mybets"),
  patchBet: (id: number, body: BetPatch) =>
    request<{ ok: boolean }>(`/bets/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  unmarkBet: (id: number) =>
    request<UnmarkResponse>(`/bets/${id}/unmark`, { method: "POST" }),
  remarkBet: (id: number) =>
    request<{ ok: boolean }>(`/bets/${id}/unmark`, { method: "DELETE" }),
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
npm --prefix app test -- src/api/client.test.ts
```

Expected: PASS 3건

- [ ] **Step 6: Commit**

```bash
git add app/src/api/types.ts app/src/api/client.ts app/src/api/client.test.ts
git commit -m "feat(app): /mybets · PATCH · unmark 클라이언트 메서드와 타입"
```

---

### Task 7: `MyBetsScreen` — 3구획 목록과 탭 등록

**Files:**
- Create: `app/src/screens/MyBetsScreen.tsx`
- Create: `app/src/screens/MyBetsScreen.test.tsx`
- Modify: `app/src/navigation/types.ts`
- Modify: `app/src/navigation/index.tsx`

**Interfaces:**
- Consumes: Task 6의 `api.getMyBets`, `AppBet`, `MyBetsResponse`
- Produces:
  - `MyBetsScreen` (named export)
  - `MyBetsStackParamList = { MyBetsList: undefined; BetEdit: { bet: AppBet } }`
  - `TabParamList`에 `MyBets: NavigatorScreenParams<MyBetsStackParamList> | undefined` 추가

- [ ] **Step 1: Write the failing test**

`app/src/screens/MyBetsScreen.test.tsx`:

```tsx
jest.mock("../api/client", () => ({
  api: {
    getMyBets: jest.fn(async () => ({
      calendar: { "2026-08-09": 12.5 },
      open: [{
        id: 1, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
        commence_time: "2099-01-01T00:00:00Z", market: "h2h", side: "NYM",
        point: 0, player: "", label: "NYM ML", book: "FanDuel", fill_odds: 2.1,
        stake: 20, live_status: "pending", live_progress: null, settled: 0,
        won: null, pnl: null, settled_at: null, not_placed: 0, not_placed_at: null,
      }],
      settled: [{
        id: 2, event_id: "E2", sport: "baseball_mlb", home: "LAD", away: "SF",
        commence_time: "2026-08-09T20:00:00Z", market: "h2h", side: "SF",
        point: 0, player: "", label: "SF ML", book: "DraftKings", fill_odds: 2.3,
        stake: 20, live_status: null, live_progress: null, settled: 1,
        won: 1, pnl: 26, settled_at: "2026-08-09T23:00:00+00:00",
        not_placed: 0, not_placed_at: null,
      }],
      not_placed: [{
        id: 3, event_id: "E3", sport: "baseball_mlb", home: "BOS", away: "NYY",
        commence_time: "2026-08-08T20:00:00Z", market: "h2h", side: "NYY",
        point: 0, player: "", label: "NYY ML", book: "BetMGM", fill_odds: 1.9,
        stake: 20, live_status: null, live_progress: null, settled: 0,
        won: null, pnl: null, settled_at: null, not_placed: 1,
        not_placed_at: "2026-08-12T10:00:00+00:00",
      }],
    })),
  },
}));
const mockPush = jest.fn();
jest.mock("@react-navigation/native", () => ({
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  useFocusEffect: (cb: () => void) => { require("react").useEffect(cb, []); },
  useNavigation: () => ({ push: mockPush }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { MyBetsScreen } from "./MyBetsScreen";

test("renders the three sections with their exact headers", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  // Exact strings, not regex: a broken JSX ternary once rendered its own source
  // text and a /.../ assertion happily matched it (PR#75).
  expect(screen.getByText("미정산 1")).toBeOnTheScreen();
  expect(screen.getByText("정산됨 1")).toBeOnTheScreen();
  expect(screen.getByText("베팅 안 함 1")).toBeOnTheScreen();
  expect(screen.getByText("SF ML")).toBeOnTheScreen();
  expect(screen.getByText("NYY ML")).toBeOnTheScreen();
});

test("a not-placed row is struck through", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYY ML")).toBeOnTheScreen());
  expect(screen.getByText("NYY ML")).toHaveStyle({ textDecorationLine: "line-through" });
});

test("settled rows show signed PnL", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("+26.00")).toBeOnTheScreen());
});

test("an empty section is not rendered at all", async () => {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { api } = require("../api/client");
  (api.getMyBets as jest.Mock).mockResolvedValueOnce({
    calendar: {}, open: [], settled: [], not_placed: [],
  });
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("기록된 베팅 없음")).toBeOnTheScreen());
  expect(screen.queryByText("미정산 0")).toBeNull();
});

test("tapping a row opens the edit sheet with that bet", async () => {
  await render(<MyBetsScreen />);
  await waitFor(() => expect(screen.getByText("NYM ML")).toBeOnTheScreen());
  fireEvent.press(screen.getByText("NYM ML"));
  expect(mockPush).toHaveBeenCalledWith("BetEdit", { bet: expect.objectContaining({ id: 1 }) });
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm --prefix app test -- src/screens/MyBetsScreen.test.tsx
```

Expected: FAIL — `Cannot find module './MyBetsScreen'`

- [ ] **Step 3: Write the screen**

`app/src/screens/MyBetsScreen.tsx`:

```tsx
// app/src/screens/MyBetsScreen.tsx
import { useCallback, useState } from "react";
import { ActivityIndicator, Pressable, SectionList, StyleSheet, Text, View } from "react-native";
import { useFocusEffect, useNavigation } from "@react-navigation/native";
import { api } from "../api/client";
import type { AppBet, MyBetsResponse } from "../api/types";
import { americanOdds, matchup } from "../format";
import { color, radius, space } from "../theme";

type Section = { title: string; data: AppBet[] };

/** The month key the calendar uses, in the device's local date. */
function monthPrefix(now = new Date()): string {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

export function monthPnl(calendar: Record<string, number>, prefix = monthPrefix()): number {
  return Object.entries(calendar)
    .filter(([day]) => day.startsWith(prefix))
    .reduce((sum, [, pnl]) => sum + pnl, 0);
}

export function sections(d: MyBetsResponse | null): Section[] {
  if (!d) return [];
  // Empty sections are dropped rather than rendered with a 0 — a header that
  // says "정산됨 0" reads as a state, not as absence.
  return ([
    ["미정산", d.open],
    ["정산됨", d.settled],
    ["베팅 안 함", d.not_placed],
  ] as const)
    .filter(([, rows]) => rows.length > 0)
    .map(([title, rows]) => ({ title: `${title} ${rows.length}`, data: rows }));
}

function signed(n: number): string {
  return `${n >= 0 ? "+" : "-"}${Math.abs(n).toFixed(2)}`;
}

function BetRow({ bet, onPress }: { bet: AppBet; onPress: (b: AppBet) => void }) {
  const off = bet.not_placed === 1;
  const meta = [
    bet.book,
    bet.fill_odds != null ? americanOdds(bet.fill_odds) : null,
    bet.stake != null ? `$${bet.stake}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <Pressable style={s.row} onPress={() => onPress(bet)}>
      <View style={s.rowMain}>
        <Text style={[s.label, off && s.offLabel]}>{bet.label ?? "—"}</Text>
        <Text style={s.matchup}>{matchup(bet.away, bet.home) ?? ""}</Text>
        <Text style={s.meta}>{meta}</Text>
      </View>
      {bet.settled === 1 && bet.pnl != null && (
        <Text style={[s.pnl, bet.pnl >= 0 ? s.pnlUp : s.pnlDown]}>{signed(bet.pnl)}</Text>
      )}
    </Pressable>
  );
}

export function MyBetsScreen() {
  const nav = useNavigation<{ push: (s: string, p?: object) => void }>();
  const [data, setData] = useState<MyBetsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api.getMyBets());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { void load(); }, [load]));

  const openEdit = useCallback((bet: AppBet) => nav.push("BetEdit", { bet }), [nav]);

  if (error) {
    return (
      <View style={s.center}>
        <Text style={s.err}>로드 실패: {error}</Text>
        <Pressable style={s.retry} onPress={() => void load()}>
          <Text style={s.retryText}>다시 시도</Text>
        </Pressable>
      </View>
    );
  }

  const month = data ? monthPnl(data.calendar) : 0;

  return (
    <View style={s.container}>
      <View style={s.summary}>
        <Text style={s.summaryLabel}>이번 달</Text>
        <Text style={[s.summaryValue, month >= 0 ? s.pnlUp : s.pnlDown]}>{signed(month)}</Text>
      </View>
      <SectionList
        sections={sections(data)}
        keyExtractor={(b) => String(b.id)}
        refreshing={loading}
        onRefresh={() => void load()}
        renderSectionHeader={({ section }) => (
          <Text style={s.sectionHeader}>{section.title}</Text>
        )}
        renderItem={({ item }) => <BetRow bet={item} onPress={openEdit} />}
        ListEmptyComponent={
          loading ? <ActivityIndicator style={{ marginTop: 40 }} />
            : <Text style={s.empty}>기록된 베팅 없음</Text>
        }
      />
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg },
  summary: { flexDirection: "row", alignItems: "baseline", justifyContent: "space-between",
    paddingHorizontal: space.lg, paddingVertical: space.md },
  summaryLabel: { color: color.faint, fontSize: 11, letterSpacing: 0.4 },
  summaryValue: { fontSize: 19, fontWeight: "700" },
  sectionHeader: { color: color.muted, fontWeight: "600", fontSize: 12.5,
    paddingHorizontal: space.lg, paddingTop: space.lg, paddingBottom: space.xs,
    backgroundColor: color.bg },
  row: { flexDirection: "row", alignItems: "center", justifyContent: "space-between",
    backgroundColor: color.surface, borderRadius: radius.card, padding: space.md,
    marginHorizontal: space.md, marginTop: space.sm },
  rowMain: { flexShrink: 1, gap: 2 },
  label: { color: color.text, fontSize: 15, fontWeight: "600" },
  offLabel: { color: color.faint, textDecorationLine: "line-through" },
  matchup: { color: color.muted, fontSize: 12.5 },
  meta: { color: color.faint, fontSize: 12 },
  pnl: { fontSize: 15, fontWeight: "700", marginLeft: space.md },
  pnlUp: { color: color.positive },
  pnlDown: { color: color.negative },
  center: { flex: 1, alignItems: "center", justifyContent: "center",
    backgroundColor: color.bg, padding: space.xl },
  err: { color: color.negative, textAlign: "center", marginBottom: space.md },
  retry: { backgroundColor: color.accent, paddingHorizontal: space.lg,
    paddingVertical: 10, borderRadius: radius.sm },
  retryText: { color: color.onAccent, fontWeight: "600" },
  empty: { color: color.faint, textAlign: "center", marginTop: 40 },
});
```

- [ ] **Step 4: Register the tab**

`app/src/navigation/types.ts`를 교체:

```ts
// app/src/navigation/types.ts
import type { NavigatorScreenParams } from "@react-navigation/native";
import type { AppBet, BetInDraft } from "../api/types";

export type PicksStackParamList = {
  PicksList: undefined;
  PickDetail: { pickId: string; matchup?: string | null };
  CaptureSheet: { pickId?: string; scanDraft?: BetInDraft };
};

export type MyBetsStackParamList = {
  MyBetsList: undefined;
  BetEdit: { bet: AppBet };
};

export type TabParamList = {
  Picks: NavigatorScreenParams<PicksStackParamList> | undefined;
  MyBets: NavigatorScreenParams<MyBetsStackParamList> | undefined;
  Settings: undefined;
};

export type RootParamList = {
  Tabs: NavigatorScreenParams<TabParamList>;
};
```

`app/src/navigation/index.tsx` — import에 추가:

```tsx
import { MyBetsScreen } from "../screens/MyBetsScreen";
import { BetEditSheet } from "../screens/BetEditSheet";
import type { MyBetsStackParamList, PicksStackParamList, RootParamList, TabParamList } from "./types";
```

`PicksNavigator` 아래에 추가:

```tsx
const MyBetsStack = createNativeStackNavigator<MyBetsStackParamList>();
function MyBetsNavigator() {
  return (
    <MyBetsStack.Navigator
      screenOptions={{
        headerStyle: { backgroundColor: color.bg },
        headerTintColor: color.accent,
        headerTitleStyle: { color: color.text },
        headerShadowVisible: false,
        contentStyle: { backgroundColor: color.bg },
      }}
    >
      <MyBetsStack.Screen name="MyBetsList" component={MyBetsScreen} options={{ title: "내 베팅" }} />
      <MyBetsStack.Screen name="BetEdit" component={BetEditSheet}
        options={{ presentation: "modal", title: "베팅 수정" }} />
    </MyBetsStack.Navigator>
  );
}
```

`Tabs.Navigator`의 Picks와 Settings 사이에 추가:

```tsx
      <Tabs.Screen name="MyBets" component={MyBetsNavigator} options={{ title: "내 베팅", tabBarIcon: ({ color: c, size }) => <Feather name="file-text" size={size} color={c} /> }} />
```

`BetEditSheet`는 Task 8에서 만든다. 이 단계에서 앱이 컴파일되도록 최소 스텁을 먼저 생성한다 —
`app/src/screens/BetEditSheet.tsx`:

```tsx
// app/src/screens/BetEditSheet.tsx
import { Text, View } from "react-native";

export function BetEditSheet() {
  return <View><Text>베팅 수정</Text></View>;
}
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
npm --prefix app test -- src/screens/MyBetsScreen.test.tsx src/navigation
```

Expected: PASS — `MyBetsScreen.test.tsx` 5건 + 기존 `navigation/types.test.ts` 3건

- [ ] **Step 6: Commit**

```bash
git add app/src/screens/MyBetsScreen.tsx app/src/screens/MyBetsScreen.test.tsx app/src/screens/BetEditSheet.tsx app/src/navigation/types.ts app/src/navigation/index.tsx
git commit -m "feat(app): 내 베팅 탭 — 미정산/정산됨/베팅 안 함 3구획"
```

---

### Task 8: `BetEditSheet` — 3필드 수정과 표시 해제

**Files:**
- Modify: `app/src/screens/BetEditSheet.tsx` (Task 7의 스텁을 실제 구현으로 교체)
- Create: `app/src/screens/BetEditSheet.test.tsx`

**Interfaces:**
- Consumes: Task 6의 `api.patchBet`, `api.unmarkBet`, `api.remarkBet`, `UnmarkResponse`; Task 7의 `MyBetsStackParamList`
- Produces: `BetEditSheet` (named export)

- [ ] **Step 1: Write the failing test**

`app/src/screens/BetEditSheet.test.tsx`:

```tsx
jest.mock("../api/client", () => ({
  api: {
    patchBet: jest.fn(async () => ({ ok: true })),
    unmarkBet: jest.fn(async () => ({ ok: true, pnl_delta: null, settled_day: null })),
    remarkBet: jest.fn(async () => ({ ok: true })),
  },
}));
const mockPopToTop = jest.fn();
jest.mock("@react-navigation/native", () => ({
  useNavigation: () => ({ popToTop: mockPopToTop, goBack: jest.fn() }),
}));

import { fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import { Alert } from "react-native";
import { api } from "../api/client";
import type { AppBet } from "../api/types";
import { BetEditSheet } from "./BetEditSheet";

// spyOn the public Alert, not react-native's internal module path — the internal
// path moves between Expo SDKs and a mock of it fails silently as a no-op.
const alertSpy = jest.spyOn(Alert, "alert").mockImplementation(() => {});

/** Press the destructive button in the last Alert the component raised. */
function confirmLastAlert() {
  const buttons = alertSpy.mock.calls.at(-1)?.[2] as
    { text?: string; onPress?: () => void }[] | undefined;
  buttons?.find((b) => b.text === "처리")?.onPress?.();
}

const bet: AppBet = {
  id: 7, event_id: "E1", sport: "baseball_mlb", home: "MIA", away: "NYM",
  commence_time: "2099-01-01T00:00:00Z", market: "h2h", side: "NYM", point: 0,
  player: "", label: "NYM ML", book: "FanDuel", fill_odds: 2.1, stake: 20,
  live_status: "pending", live_progress: null, settled: 0, won: null, pnl: null,
  settled_at: null, not_placed: 0, not_placed_at: null,
};

const routeFor = (b: AppBet) => ({ params: { bet: b } }) as never;

beforeEach(() => jest.clearAllMocks());

test("shows the immutable context and the three editable fields", async () => {
  await render(<BetEditSheet route={routeFor(bet)} />);
  expect(screen.getByText("NYM ML")).toBeOnTheScreen();
  expect(screen.getByText("NYM @ MIA")).toBeOnTheScreen();
  expect(screen.getByText("경기·마켓은 수정할 수 없습니다")).toBeOnTheScreen();
  expect(screen.getByTestId("edit-book").props.value).toBe("FanDuel");
  expect(screen.getByTestId("edit-stake").props.value).toBe("20");
  expect(screen.getByTestId("edit-odds").props.value).toBe("+110");
});

test("saving sends only the three fields", async () => {
  await render(<BetEditSheet route={routeFor(bet)} />);
  fireEvent.changeText(screen.getByTestId("edit-stake"), "25");
  fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel", stake: 25, fill_odds: 2.1,
  }));
  expect(mockPopToTop).toHaveBeenCalled();
});

test("an emptied stake is omitted, never sent as null", async () => {
  // The server 422s an explicit null stake — a placed bet without one is not a
  // record of anything, and the settle worker multiplies by it.
  await render(<BetEditSheet route={routeFor(bet)} />);
  fireEvent.changeText(screen.getByTestId("edit-stake"), "");
  fireEvent.press(screen.getByText("저장"));
  await waitFor(() => expect(api.patchBet).toHaveBeenCalledWith(7, {
    book: "FanDuel", fill_odds: 2.1,
  }));
});

test("표시 해제 goes through a confirm dialog before calling the API", async () => {
  await render(<BetEditSheet route={routeFor(bet)} />);
  fireEvent.press(screen.getByText("베팅 안 했음"));
  expect(api.unmarkBet).not.toHaveBeenCalled();      // dialog first, no API yet
  const [title, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(title).toBe("베팅 안 했음으로 처리");
  expect(message).toBe("기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
  confirmLastAlert();
  await waitFor(() => expect(api.unmarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});

test("a settled bet's dialog names the PnL change", async () => {
  const settled: AppBet = { ...bet, settled: 1, won: 1, pnl: -45,
    settled_at: "2026-08-09T22:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  fireEvent.press(screen.getByText("베팅 안 했음"));
  const [, message] = alertSpy.mock.calls[0] as unknown as [string, string];
  expect(message).toBe(
    "8월 9일 PnL이 +45.00 변경됩니다. 기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.");
});

test("a settled bet cannot be edited, only unmarked", async () => {
  const settled: AppBet = { ...bet, settled: 1, won: 1, pnl: 26,
    settled_at: "2026-08-09T22:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(settled)} />);
  expect(screen.getByText("정산된 베팅은 수정할 수 없습니다")).toBeOnTheScreen();
  expect(screen.queryByText("저장")).toBeNull();
  expect(screen.getByText("베팅 안 했음")).toBeOnTheScreen();
});

test("a not-placed bet offers 되돌리기 instead", async () => {
  const off: AppBet = { ...bet, not_placed: 1, not_placed_at: "2026-08-12T10:00:00+00:00" };
  await render(<BetEditSheet route={routeFor(off)} />);
  fireEvent.press(screen.getByText("되돌리기"));
  await waitFor(() => expect(api.remarkBet).toHaveBeenCalledWith(7));
  expect(mockPopToTop).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
npm --prefix app test -- src/screens/BetEditSheet.test.tsx
```

Expected: FAIL — 스텁이 `NYM ML`을 렌더하지 않음

- [ ] **Step 3: Write the screen**

`app/src/screens/BetEditSheet.tsx`를 교체:

```tsx
// app/src/screens/BetEditSheet.tsx
import { useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { useNavigation } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { api } from "../api/client";
import type { AppBet, BetPatch } from "../api/types";
import type { MyBetsStackParamList } from "../navigation/types";
import { americanOdds, matchup, parseOddsToDecimal } from "../format";
import { color, radius, space } from "../theme";

type Props = { route: NativeStackScreenProps<MyBetsStackParamList, "BetEdit">["route"] };

const MONTH_DAY = (iso: string): string => {
  const [, m, d] = iso.slice(0, 10).split("-");
  return `${Number(m)}월 ${Number(d)}일`;
};

/** The confirm text. A settled bet's PnL moves, so the dialog names the day and
 *  the amount — a past number must never shift in silence. */
export function unmarkMessage(bet: AppBet): string {
  const tail = "기록은 남고 집계에서만 빠집니다. 이 픽은 다시 피드에 나타납니다.";
  if (!bet.settled || bet.pnl == null || !bet.settled_at) return tail;
  const delta = -bet.pnl;
  const signed = `${delta >= 0 ? "+" : "-"}${Math.abs(delta).toFixed(2)}`;
  return `${MONTH_DAY(bet.settled_at)} PnL이 ${signed} 변경됩니다. ${tail}`;
}

export function BetEditSheet({ route }: Props) {
  const nav = useNavigation<{ popToTop: () => void; goBack: () => void }>();
  const bet = route.params.bet;
  const [book, setBook] = useState(bet.book ?? "");
  const [stake, setStake] = useState(bet.stake != null ? String(bet.stake) : "");
  const [odds, setOdds] = useState(bet.fill_odds != null ? americanOdds(bet.fill_odds) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const editable = bet.settled === 0 && bet.not_placed === 0;

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      // stake / fill_odds cannot be nulled server-side (422): a placed bet
      // without them is not a record of anything, and the settle worker
      // multiplies by both. So an emptied field is OMITTED, not sent as null.
      // book may be cleared — it is descriptive metadata.
      const patch: BetPatch = { book: book || null };
      if (stake) patch.stake = Number(stake);
      const parsedOdds = parseOddsToDecimal(odds);
      if (parsedOdds != null) patch.fill_odds = parsedOdds;
      await api.patchBet(bet.id, patch);
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  // No optimistic update and no 5-second undo bar here: this action is already
  // gated by a confirm dialog, so waiting for the server is the honest thing.
  const unmark = () => {
    Alert.alert("베팅 안 했음으로 처리", unmarkMessage(bet), [
      { text: "취소", style: "cancel" },
      {
        text: "처리", style: "destructive",
        onPress: () => void (async () => {
          setBusy(true);
          try {
            await api.unmarkBet(bet.id);
            nav.popToTop();
          } catch (e) {
            setError((e as Error).message);
            setBusy(false);
          }
        })(),
      },
    ]);
  };

  const remark = async () => {
    setBusy(true);
    try {
      await api.remarkBet(bet.id);
      nav.popToTop();
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <View style={s.container}>
      <Text style={s.label}>{bet.label ?? "—"}</Text>
      <Text style={s.matchup}>{matchup(bet.away, bet.home) ?? ""}</Text>
      <Text style={s.immutable}>경기·마켓은 수정할 수 없습니다</Text>

      {editable ? (
        <>
          <Text style={s.field}>북</Text>
          <TextInput testID="edit-book" style={s.input} value={book} onChangeText={setBook}
            placeholder="북" placeholderTextColor={color.faint} autoCapitalize="none" />
          <Text style={s.field}>스테이크</Text>
          <TextInput testID="edit-stake" style={s.input} value={stake} onChangeText={setStake}
            placeholder="스테이크 ($)" placeholderTextColor={color.faint} keyboardType="decimal-pad" />
          <Text style={s.field}>실제 체결 배당</Text>
          <TextInput testID="edit-odds" style={s.input} value={odds} onChangeText={setOdds}
            placeholder="실배당 (예: -110)" placeholderTextColor={color.faint}
            keyboardType="numbers-and-punctuation" />
        </>
      ) : (
        <Text style={s.locked}>
          {bet.not_placed === 1 ? "베팅 안 함으로 처리된 기록입니다"
            : "정산된 베팅은 수정할 수 없습니다"}
        </Text>
      )}

      {error && <Text style={s.err}>{error}</Text>}

      <View style={s.actions}>
        <Pressable style={[s.btn, s.cancel]} onPress={() => nav.goBack()}>
          <Text style={s.btnText}>닫기</Text>
        </Pressable>
        {editable && (
          <Pressable style={[s.btn, s.save]} disabled={busy} onPress={() => void save()}>
            <Text style={s.btnText}>저장</Text>
          </Pressable>
        )}
      </View>

      {bet.not_placed === 1 ? (
        <Pressable style={s.destructive} disabled={busy} onPress={() => void remark()}>
          <Text style={s.destructiveText}>되돌리기</Text>
        </Pressable>
      ) : (
        <Pressable style={s.destructive} disabled={busy} onPress={unmark}>
          <Text style={s.destructiveText}>베팅 안 했음</Text>
        </Pressable>
      )}
    </View>
  );
}

const s = StyleSheet.create({
  container: { flex: 1, backgroundColor: color.bg, padding: space.xl },
  label: { color: color.text, fontSize: 18, fontWeight: "600" },
  matchup: { color: color.muted, fontSize: 13, marginTop: 2 },
  immutable: { color: color.faint, fontSize: 11.5, marginTop: space.xs },
  locked: { color: color.urgent, fontSize: 13, marginTop: space.lg },
  field: { color: color.faint, fontSize: 11, letterSpacing: 0.4, marginTop: space.lg },
  input: { backgroundColor: color.surfaceRaised, color: color.text, borderRadius: radius.sm,
    paddingHorizontal: space.md, paddingVertical: 10, marginTop: space.xs, fontSize: 15 },
  err: { color: color.negative, marginTop: space.md },
  actions: { flexDirection: "row", gap: space.md, marginTop: space.xl },
  btn: { flex: 1, paddingVertical: 12, borderRadius: radius.button, alignItems: "center" },
  cancel: { backgroundColor: color.surfaceRaised },
  save: { backgroundColor: color.accent },
  btnText: { color: color.text, fontWeight: "600" },
  destructive: { marginTop: space.xl, paddingVertical: 12, alignItems: "center",
    borderRadius: radius.button, borderWidth: 1, borderColor: color.negativeStrong },
  destructiveText: { color: color.negative, fontWeight: "600" },
});
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
npm --prefix app test -- src/screens/BetEditSheet.test.tsx
```

Expected: PASS 6건

- [ ] **Step 5: Run the whole app + python suites**

```bash
npm --prefix app test
```

Expected: 전 스위트 PASS

```bash
./venv/bin/pytest tests/ -q
```

Expected: 전 스위트 PASS

- [ ] **Step 6: Commit**

```bash
git add app/src/screens/BetEditSheet.tsx app/src/screens/BetEditSheet.test.tsx
git commit -m "feat(app): 베팅 수정 시트 — 3필드 수정 + 베팅 안 했음 처리

정산분 확인 다이얼로그가 바뀌는 날짜와 금액을 명시 — 과거 PnL이
조용히 움직이지 않게."
```

---

## 배포 (모든 태스크 완료 후)

- [ ] **API 배포**

```bash
git push ec2 master
```

`app_bet` 컬럼은 `_ensure_app_bet_columns`가 기동 시 추가하므로 별도 마이그레이션이 없다.
배포 후 확인:

```bash
ssh -i ~/.ssh/kalshi-bot.pem ubuntu@3.146.180.145 "sqlite3 /home/ubuntu/kalshi-arb/data/softbook_paper.db 'PRAGMA table_info(app_bet)' | grep not_placed"
```

Expected: `not_placed`, `not_placed_at` 두 줄

- [ ] **앱 OTA**

네이티브 변경이 없으므로(새 화면 + 탭 하나 = JS 전용) fingerprint가 바뀌지 않는다:

```bash
cd app && eas update --channel production --message "내 베팅 탭 — 조회/수정/표시 해제"
```

⚠️ Expo OTA는 앱 **재실행 2회**가 필요하다 (1회차 다운로드, 2회차 적용). 백그라운드 복귀는 무효.
