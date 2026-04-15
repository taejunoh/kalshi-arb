# Bankroll concurrent-deduct race — `asyncio.Lock` 고려 (low priority)

발견일: 2026-04-15
맥락: [commit c677df6](https://github.com/taejunoh/kalshi-arb/) 이후 sports_trader entry 가 `asyncio.create_task` 로 동시 실행되면서 드러남.

## 현상

같은 시각 여러 entry 가 동시에 `_paper_market_order` 에 진입하면 로그에 **stale bankroll 값** 이 찍힌다.

```
19:15:40 [PAPER] Market no  | sports_midshot | $3.00 @ 0.320 | bankroll: $2086.81
19:15:40 [PAPER] Market yes | sports_midshot | $3.00 @ 0.310 | bankroll: $2086.81
                                                                         ↑ 같은 값
```

두 번째 로그는 $2083.18 이어야 하는데 동일 값. 원인: [core/order_manager.py:_paper_market_order](../../core/order_manager.py) 가 `bankroll -=` 직후 `await persist` / `await record_trade` 하면서 yield 하고, 그 사이 다른 task 가 추가 차감 후 로그를 찍어서 둘 다 같은 스냅샷을 봄.

## 실제 상태는 정확

- Python asyncio 는 single-thread event loop, `self.bankroll -= x` 는 한 바이트코드 내에서 atomic
- 두 task 각각 `-= 3.63` → 최종 bankroll 은 정확히 `original - 7.26`
- DB persist 도 마지막 write 값이 정확
- **즉 현재는 로그 표시 artefact 만 있음**

## 이론적 race (아직 관측 안 됨)

### `market_order()` 의 bankroll < size 체크

```python
# core/order_manager.py:151
if self.bankroll < size_usdc:
    return {"status": "skipped", "reason": "insufficient_bankroll"}
```

동시에 시작한 Task A, B 가 각각 체크 통과 후 순차 차감 → 이론적으로 bankroll 음수 가능.

현실적 무해: v31 $3/trade + bankroll $2000+ 이라 overlap 확률 극히 낮음. 큰 포지션 전략 추가 시 문제 될 수 있음.

### `_daily_loss_live` / `_consecutive_losses` 카운터

settlement 시점에 순차 업데이트되므로 entry 동시성과 무관. 영향 없음.

## 트리거 조건 (언제 고치면 좋나)

- 동시 entry 가 **큰 포지션** (>= bankroll의 10%)을 만들 수 있는 전략 추가 시
- Bankroll 이 간당간당해져서 `insufficient_bankroll` skip 이 자주 발생할 때
- 로그 일관성이 디버깅 문제를 만들 때

## 고치는 법

`OrderManager.__init__` 에 `self._bankroll_lock = asyncio.Lock()` 추가 후, bankroll 을 읽고 쓰는 critical section 을 감싼다.

```python
async def market_order(...):
    async with self._bankroll_lock:
        if self.bankroll < size_usdc:
            return {"status": "skipped", ...}
    # (주문 제출은 lock 밖에서 — I/O 동안 다른 task 진입 허용)
    ...
    async with self._bankroll_lock:
        self.bankroll -= (size_usdc + fee)
        await self._persist_bankroll()
    logger.info(f"... bankroll: ${self.bankroll:.2f}")
```

주의: lock 안에서 `await kalshi_client.place_*` 하면 직렬화되어 throughput 손실. **잔고 계산만** lock, API 호출은 밖에서.

## 관련 파일

- [core/order_manager.py](../../core/order_manager.py) — `_paper_market_order`, `_live_market_order`, `_live_limit_ttl_order`, `market_order` 의 bankroll 체크
- [strategies/sports_trader.py](../../strategies/sports_trader.py) — `_dispatch_entry` 가 `asyncio.create_task` 로 fork 하는 지점

## Priority

**Low.** 실제 손실/버그 없음. 큰 전략 추가 시 재평가.
