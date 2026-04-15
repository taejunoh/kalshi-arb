# Telegram Error Handler — Silent Startup Hang

**상태:** 2026-04-15 통합 직후 롤백. 재통합 전 원인 파악 필요.

## 증상

`main.py` `Bot.__init__()` 에서 root logger 에 `TelegramErrorHandler` 추가 후:

- systemd 상으로 봇 프로세스 active (95M memory, ~5% CPU)
- uvicorn dashboard 만 정상 서빙
- **`Bot.start()` 의 로그 (`"Bot starting in PAPER mode ..."`) 가 안 뜸**
- 프라이스 피드 connect 로그 없음, sports scan 로그 없음
- `logs/bot.log` 가 shutdown 시점에서 멈추고 그 이후 안 써짐

4분 이상 silent 정지 상태 유지. `/tmp/kalshi_stop` 없음, 에러 로그도 없음 — 그냥 아무 일도 안 함.

## 추정 원인

### 가설 A: `setup_logger()` 가 기존 handler 를 완전 교체
`monitoring/logger.py:setup_logger("bot")` 호출 시 root 또는 "bot" 로거의
handler list 를 `[]` 로 리셋 후 파일/콘솔 핸들러 추가할 수 있음. 우리 핸들러가
증발하고 혹은 간섭으로 전체 로깅 파이프라인이 깨짐.

### 가설 B: emit() 동기 호출 중 asyncio 이벤트 루프 설정 전 `_dispatch_async` 재귀
`Bot.__init__()` 은 sync 컨텍스트. 이 시점에 일부 컴포넌트가 `logger.error()` 호출
(예: kalshi_client 의 RSA key 로드 실패). 우리 핸들러가 emit 받고 try/except 로
`RuntimeError: no running loop` 잡아서 return 하지만, 그 과정에서 어떤 부작용
(예: blocking I/O, 다른 핸들러 race) 이 생길 가능성.

### 가설 C: Handler emit 중 notifier.send 의 httpx import/init 이 동기 lock
`_dispatch_async` 가 `asyncio.run_coroutine_threadsafe` 가 아닌 `create_task`
사용. `create_task` 는 RunningLoop 필요 — 에러 시 silent drop 해야 하는데 이벤트
루프 붙어 있는 task 가 notifier.send 를 호출하면서 httpx 내부 gRPC/TLS handshake
가 gather 와 충돌? 낮은 가능성이지만 배제 못함.

## 가장 의심스러운 것 (가설 A)

`setup_logger` 를 본 적 없음. 이게 root handler list 를 clear 하는 경우 흔함
(중복 로그 방지용으로 이전 handler 제거). 그럼 우리 핸들러가 제거되고 에러는
없지만 handler 내 `logging.getLogger().addHandler(_err_handler)` 이후에 뭔가
다른 문제가 생겼다면... 모순이긴 함.

## 재통합 전 체크리스트

1. **`monitoring/logger.py:setup_logger` 읽기** — 이 함수가 root handler 건드리는
   지 확인. 건드리면 우리 핸들러 추가 지점을 `setup_logger` **이후** 로 이동.
2. **Bot.__init__ 에서 추가 X, Bot.start() 에서 추가 O** 로 이동 — 이벤트 루프
   살아있는 상태에서 추가하면 경쟁 없어짐.
3. **로컬 dry-run 테스트** — 실서버 배포 전 로컬에서 `python3 main.py` 실행 → 1분
   관찰 → scan 로그 정상 찍히는지 확인.
4. **핸들러 내부 `_dispatch_async` 안전성 강화**: `run_coroutine_threadsafe` 로
   바꾸거나 완전히 loop-agnostic 으로 queue 쓰기 (handler → queue → async task
   drain).

## 현재 상태

- `monitoring/telegram_error_handler.py` — 모듈 자체는 유지, 9개 단위 테스트 전부
  pass
- `main.py` — 통합 부분 주석 처리 (line 46-48 주변)
- 모든 error 알림 여전히 수동 (user 가 물어봐야 알 수 있음)

## 대안 (재통합 실패 시)

- **Cron sidecar**: `journalctl --since -5min | grep ERROR` 결과를 5분마다 Telegram
  으로 포워딩. 봇 프로세스와 격리 → startup 간섭 없음. 간단하고 견고
- **Dashboard 알림 섹션**: `/api/recent-errors` 엔드포인트 + dashboard 에 표시.
  리얼타임은 아니지만 관측 가능. user 가 주기적으로 봐야 함
- **Log shipping to Papertrail/Datadog**: proper infra, 유료

## 우선순위

높지 않음 — V31 Live 운영/검증이 먼저. Error 알림은 "있으면 좋은" 수준이지
blocker 아님. 2-3일 여유 생길 때 가설 A 검증 + setup_logger 분석부터 재시작 권장.
