#!/usr/bin/env python3
"""Memory-pressure watchdog for the EC2 host — Telegram alert when available RAM runs low.

Added 2026-07-01 after the t3.small OOM hang (SSH + Cloudflare tunnel died together → app
HTTP 530; recovered by reboot) and the upgrade to t3.medium (4GB). This is the early-warning
so a memory leak / load spike is caught BEFORE the box hangs again. See memory
reference_ec2_aws_recovery for the recovery playbook.

Design constraints:
  * STDLIB ONLY (urllib/os/time/json/socket, ps via subprocess best-effort) so the watchdog
    still runs when memory is tight — no pandas/heavy imports, minimal fork.
  * Reads MemAvailable straight from /proc/meminfo (no subprocess for the core reading, so it
    works even if fork() is failing under pressure). `ps` for the top-processes list is
    best-effort and degrades to "(unavailable)".
  * State machine with HYSTERESIS + RATE-LIMIT (state file) so it alerts:
      - once when available RAM first drops below MEM_ALERT_MB,
      - again every MEM_REALERT_MIN while it stays low (so it isn't forgotten),
      - once when it recovers above MEM_ALERT_MB + MEM_RECOVER_MARGIN_MB.
    Never every minute.

Runs from kalshi-mem-watchdog.timer (every 60s). Tunables via .env (all optional):
  MEM_ALERT_MB (300)   MEM_RECOVER_MARGIN_MB (150)   MEM_REALERT_MIN (30)
"""
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request

ENV_PATH = os.getenv("ENV_PATH", "/home/ubuntu/kalshi-arb/.env")
STATE_FILE = os.getenv("MEM_WATCHDOG_STATE",
                       "/home/ubuntu/kalshi-arb/logs/mem_watchdog_state.json")
ALERT_MB = int(os.getenv("MEM_ALERT_MB", "300"))
RECOVER_MARGIN_MB = int(os.getenv("MEM_RECOVER_MARGIN_MB", "150"))
REALERT_SEC = int(os.getenv("MEM_REALERT_MIN", "30")) * 60


def _env(key):
    """Read one KEY=value from .env (same self-contained pattern as softbook_tripwire)."""
    try:
        for line in open(ENV_PATH):
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"')
    except Exception:
        return None
    return None


def send_telegram(text):
    tok, cho = _env("TELEGRAM_BOT_TOKEN"), _env("TELEGRAM_CHAT_ID")
    if not (tok and cho):
        return False
    try:
        data = urllib.parse.urlencode({"chat_id": cho, "text": text}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=10).read()
        return True
    except Exception:
        return False


def _meminfo():
    """(mem_avail_mb, mem_total_mb, swap_used_mb) from /proc/meminfo (values are kB)."""
    m = {}
    for line in open("/proc/meminfo"):
        p = line.split()
        if len(p) >= 2:
            m[p[0].rstrip(":")] = int(p[1])
    avail = m.get("MemAvailable", 0) // 1024
    total = m.get("MemTotal", 0) // 1024
    swap_used = (m.get("SwapTotal", 0) - m.get("SwapFree", 0)) // 1024
    return avail, total, swap_used


def _loadavg():
    try:
        return open("/proc/loadavg").read().split()[:3]
    except Exception:
        return ["?", "?", "?"]


def _top_mem(n=5):
    """Best-effort top-N processes by RSS (MB). Guarded — fork can fail under real pressure."""
    try:
        rows = subprocess.run(["ps", "-eo", "rss,comm", "--sort=-rss"],
                              capture_output=True, text=True, timeout=8).stdout.splitlines()
        out = []
        for r in rows[1:n + 1]:
            parts = r.split(None, 1)
            if len(parts) == 2 and parts[0].isdigit():
                out.append(f"  {int(parts[0]) // 1024}MB {parts[1]}")
        return "\n".join(out) or "  (none)"
    except Exception:
        return "  (unavailable)"


def _load_state():
    try:
        return json.load(open(STATE_FILE))
    except Exception:
        return {"state": "ok", "last_alert": 0}


def _save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        json.dump(st, open(STATE_FILE, "w"))
    except Exception:
        pass


def main():
    avail, total, swap_used = _meminfo()
    st = _load_state()
    now = time.time()
    host = socket.gethostname()
    la = _loadavg()

    if avail < ALERT_MB:
        first = st.get("state") != "low"
        due = (now - st.get("last_alert", 0)) >= REALERT_SEC
        if first or due:
            tag = "⚠️ 메모리 부족" if first else "⚠️ 메모리 여전히 낮음"
            send_telegram(
                f"{tag} ({host})\n"
                f"여유 {avail}MB / 총 {total}MB (임계 {ALERT_MB}MB)\n"
                f"swap 사용 {swap_used}MB · load {la[0]} {la[1]} {la[2]}\n"
                f"top mem:\n{_top_mem()}\n"
                f"→ OOM 행 위험. reference_ec2_aws_recovery 참고.")
            _save_state({"state": "low", "last_alert": now})
    elif avail > ALERT_MB + RECOVER_MARGIN_MB:
        if st.get("state") == "low":
            send_telegram(f"✅ 메모리 회복 ({host}) — 여유 {avail}MB / 총 {total}MB")
            _save_state({"state": "ok", "last_alert": st.get("last_alert", 0)})
    # else: inside the hysteresis band (ALERT_MB .. ALERT_MB+MARGIN) — no state change, no alert.

    print(f"mem_watchdog: avail={avail}MB total={total}MB swap_used={swap_used}MB "
          f"state={_load_state().get('state')} thr={ALERT_MB}MB")


if __name__ == "__main__":
    main()
