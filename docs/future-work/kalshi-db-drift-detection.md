# Kalshi ↔ DB 포지션 드리프트 감지 (low priority)

발견일: 2026-04-16
트리거 이벤트: 09:02 에 v31 이 placed 한 limit 주문이 그 후 bot restart 로 TTL poller 가 죽으면서 Kalshi 에 resting 으로 남음. 14:21 UTC 에 체결됐을 때 Kalshi 는 유저에게 알림을 보냈지만 DB 에는 trade row 가 없어서 대시보드·PnL·CLV 모두 누락. [Commit 51cc34f](https://github.com/taejunoh/kalshi-arb/) 이후 startup 에서 resting orders 자동 취소하게 됐지만, 아래 잔여 케이스는 여전히 감지 불가.

## 잔여 drift 원인 (모두 저빈도)

1. **Runtime TTL cancel 실패** — `_live_limit_ttl_order` 가 `cancel_order` 를 불렀는데 Kalshi 가 거절. 현재 우리는 re-read 로 late fill 을 한 번 잡지만, 그 사이에 또 timing 나면 orphan.
2. **Bot down 동안 체결** — 장시간 다운(예: EC2 이슈) 동안 resting order 가 체결되면 복구 시 DB 누락.
3. **외부 액션** — 유저가 Kalshi 앱/웹에서 수동 매수·매도. 봇은 모름. (정상이지만 drift 관점에선 mismatch.)
4. **Settlement timing window** — Kalshi 가 settle 한 시각과 봇 settlement cycle 사이 몇 분 mismatch. (자동으로 따라잡지만 순간적으로 drift.)

## 기본 알고리즘 스케치

```python
def detect_drift():
    kalshi_qty = {}  # ticker -> net contracts from Kalshi
    for p in kalshi_client.get_positions():
        if float(p["position_fp"]) != 0:
            kalshi_qty[p["ticker"]] = float(p["position_fp"])

    db_qty = {}  # ticker -> net contracts summed from filled/settled rows
    for row in db.get_trades(status_in=["filled", "settled"]):
        count = int(row["size"] / row["price"])
        signed = count if row["direction"] == "yes" else -count
        db_qty[row["market_id"]] = db_qty.get(row["market_id"], 0) + signed

    for ticker, kqty in kalshi_qty.items():
        dqty = db_qty.get(ticker, 0)
        if abs(kqty - dqty) >= 1:
            logger.warning(f"[DRIFT] {ticker}: Kalshi={kqty} DB={dqty}")
```

## 설계 challenges (구현 전 해결 필요)

1. **False-positive 필터링**
   - 유저 수동 trade 는 정상. strategy 컬럼 없는 (= 봇이 기록 안 한) Kalshi 포지션은 알림에서 제외해야 함.
   - 한 방법: DB 에 "known manual positions" 테이블 추가해서 유저가 마킹하게 함. 복잡.
   - 더 간단한 방법: v31/mentions_trader 가 실제로 트레이드했던 ticker 들의 화이트리스트 만들어 거기에만 drift 체크.

2. **Settlement race window**
   - Kalshi 가 먼저 settle (position disappear) → 봇 settlement cycle 전: **DB 에만 있음**
   - 이걸 drift 로 기록하면 매 settle 마다 false positive
   - 해결: 체크를 settle 직후에만 돌리거나 일정 grace period 후 평가

3. **Partial fill accounting**
   - DB 의 `size` 는 체결된 실량 ($2.79 for 9×0.31). `count = int(size/price)` 는 보통 맞지만 소수점 오차 가능
   - 비교 임계값 `>= 1 contract` 로 잡으면 반올림 이슈 회피

4. **Alert fatigue**
   - 너무 자주 경고하면 무시됨
   - 권장: 드리프트 발생 시 **한 번만** telegram, 이후 같은 ticker 는 해결 or 24h 후까지 음소거

## Priority

**Low.** Startup reconciliation 이 90% 문제 해결함. 구현 시간 대비 얻는 이익 작음.

## 재평가 트리거

- Startup reconcile 이후에도 orphan 관찰됨 (로그 + Kalshi 알림 vs DB 불일치)
- 자본 $10K+ 에 도달해서 $1 drift 도 추적 필요
- Live 전략 수가 3개 넘어가면서 수동 점검 버거움

## 관련 파일

- [main.py](../../main.py) — `_reconcile_resting_orders()` 에 함께 호출 가능 (startup 시 drift 경고)
- [core/kalshi_client.py](../../core/kalshi_client.py) — `get_positions()`
- [core/database.py](../../core/database.py) — `get_trades()` 필터 옵션 필요할 수 있음
