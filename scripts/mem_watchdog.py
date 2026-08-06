#!/usr/bin/env python3
"""Host watchdog for EC2 — Telegram alert when RAM or DISK runs low.

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

DISK was added 2026-08-06 after / hit 100% with 4 MB free: SQLite returned
"disk I/O error" on every write and macro_event / xvenue_polymarket stopped. The
box had been filling for weeks (30 days of uncompressed logs against a 12 GB
bot.db on a 29 GB disk) and nothing said so — it surfaced as two strategy errors.
Same state machine as memory, so it warns while there is still room to act
instead of after writes start failing.

Disk uses os.statvfs, not `du`: a full disk is exactly when you cannot afford to
walk 17 GB of files, and the reading has to work when fork() is struggling. The
message names the two known consumers by stat() alone, which is cheap.

Runs from kalshi-mem-watchdog.timer (every 60s). Tunables via .env (all optional):
  MEM_ALERT_MB (300)   MEM_RECOVER_MARGIN_MB (150)   MEM_REALERT_MIN (30)
  DISK_ALERT_PCT (85)  DISK_RECOVER_MARGIN_PCT (5)
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
DISK_ALERT_PCT = float(os.getenv("DISK_ALERT_PCT", "85"))
DISK_RECOVER_MARGIN_PCT = float(os.getenv("DISK_RECOVER_MARGIN_PCT", "5"))
DISK_PATH = os.getenv("DISK_WATCH_PATH", "/")
REPO = os.getenv("KALSHI_REPO", "/home/ubuntu/kalshi-arb")


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


def _disk(path=None):
    """(used_pct, free_gb, total_gb) via statvfs — no subprocess, no directory walk."""
    v = os.statvfs(path or DISK_PATH)
    total = v.f_blocks * v.f_frsize
    free = v.f_bavail * v.f_frsize                 # available to non-root
    used = total - v.f_bfree * v.f_frsize          # what df calls Used
    pct = 100.0 * used / (used + free) if (used + free) else 0.0
    return pct, free / 2**30, total / 2**30


def _big_consumers():
    """The two things that actually fill this box, by stat() only. `du` is off the
    table here: a full disk is when you can least afford to walk 17 GB."""
    out = []
    try:
        db = os.path.join(REPO, "data", "bot.db")
        out.append(f"  bot.db {os.path.getsize(db) / 2**30:.1f}GB")
    except OSError:
        pass
    try:
        d = os.path.join(REPO, "logs")
        n = sum(os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)
                if os.path.isfile(os.path.join(d, f)))
        out.append(f"  logs {n / 2**30:.1f}GB")
    except OSError:
        pass
    return "\n".join(out) or "  (unavailable)"


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
    """{"mem": {...}, "disk": {...}}. Migrates the pre-2026-08-06 flat shape,
    which held memory only, so an in-flight low-memory alert is not forgotten."""
    try:
        st = json.load(open(STATE_FILE))
    except Exception:
        st = {}
    if "state" in st:                              # legacy flat form
        st = {"mem": {"state": st.get("state", "ok"),
                      "last_alert": st.get("last_alert", 0)}}
    for k in ("mem", "disk"):
        st.setdefault(k, {"state": "ok", "last_alert": 0})
    return st


def _save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        json.dump(st, open(STATE_FILE, "w"))
    except Exception:
        pass


def _check(st, key, now, low, recovered, body, first_tag, again_tag, ok_msg):
    """One subsystem through the shared state machine: alert on entering `low`,
    again every REALERT_SEC while it stays there, once on recovery. `recovered`
    is deliberately a separate predicate, not `not low` — the gap between them is
    the hysteresis band, where nothing is sent and no state changes."""
    cur = st[key]
    if low:
        first = cur.get("state") != "low"
        due = (now - cur.get("last_alert", 0)) >= REALERT_SEC
        if first or due:
            send_telegram((first_tag if first else again_tag) + body)
            st[key] = {"state": "low", "last_alert": now}
            return True
    elif recovered and cur.get("state") == "low":
        send_telegram(ok_msg)
        st[key] = {"state": "ok", "last_alert": cur.get("last_alert", 0)}
    return False


def main():
    st = _load_state()
    now = time.time()
    host = socket.gethostname()
    la = _loadavg()

    avail, total, swap_used = _meminfo()
    _check(st, "mem", now,
           low=avail < ALERT_MB,
           recovered=avail > ALERT_MB + RECOVER_MARGIN_MB,
           body=(f" ({host})\n"
                 f"여유 {avail}MB / 총 {total}MB (임계 {ALERT_MB}MB)\n"
                 f"swap 사용 {swap_used}MB · load {la[0]} {la[1]} {la[2]}\n"
                 f"top mem:\n{_top_mem()}\n"
                 f"→ OOM 행 위험. reference_ec2_aws_recovery 참고."),
           first_tag="⚠️ 메모리 부족", again_tag="⚠️ 메모리 여전히 낮음",
           ok_msg=f"✅ 메모리 회복 ({host}) — 여유 {avail}MB / 총 {total}MB")

    pct, free_gb, total_gb = _disk()
    _check(st, "disk", now,
           low=pct >= DISK_ALERT_PCT,
           recovered=pct < DISK_ALERT_PCT - DISK_RECOVER_MARGIN_PCT,
           body=(f" ({host})\n"
                 f"{DISK_PATH} 사용 {pct:.0f}% · 여유 {free_gb:.1f}GB / 총 {total_gb:.0f}GB"
                 f" (임계 {DISK_ALERT_PCT:.0f}%)\n"
                 f"주요 사용처:\n{_big_consumers()}\n"
                 f"→ 가득 차면 SQLite 쓰기 실패(disk I/O error)로 전략이 멈춥니다."),
           first_tag="⚠️ 디스크 부족", again_tag="⚠️ 디스크 여전히 부족",
           ok_msg=f"✅ 디스크 회복 ({host}) — 사용 {pct:.0f}% · 여유 {free_gb:.1f}GB")

    _save_state(st)
    print(f"host_watchdog: avail={avail}MB total={total}MB swap_used={swap_used}MB "
          f"mem={st['mem']['state']} | disk={pct:.0f}% free={free_gb:.1f}GB "
          f"state={st['disk']['state']} thr={DISK_ALERT_PCT:.0f}%")


if __name__ == "__main__":
    main()
