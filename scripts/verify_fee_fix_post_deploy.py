"""One-shot post-deploy verification of the 2026-06-02 fee fix (real_fee + loss
branch + underdog_v37 demote). Confirms the live code path now records Kalshi's
REAL fee (0.07*p*(1-p)), cross-checks against the /portfolio/fills ledger, checks
bot health, and Telegrams a GREEN/anomaly verdict on whether to start ④ (the
gate->real-fee migration). Context: memory newmodel_edge_hunt_2026-06-02.md.

Armed via a systemd one-shot timer for ~24h post-deploy. Read-only on the DB.
"""
import asyncio, os, sys, sqlite3, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.config import Config

DEPLOY_TS = "2026-06-02 15:45"
DB = "/home/ubuntu/kalshi-arb/data/bot.db"


def check_recorded_fees():
    c = sqlite3.connect(DB); c.execute("PRAGMA query_only=1")
    rows = c.execute(
        """SELECT price,size,fee,status FROM trades
           WHERE datetime(timestamp) >= datetime(?)
             AND price>0.01 AND price<0.99 AND size>0 AND fee>0""", (DEPLOY_TS,)).fetchall()
    n = len(rows); real_like = flat_like = 0
    for p, sz, fee, st in rows:
        ct = sz / p
        real = 0.07 * p * (1 - p) * ct
        flat = min(0.07 * ct, p * ct, (1 - p) * ct)
        if abs(fee - real) <= abs(fee - flat):
            real_like += 1
        else:
            flat_like += 1
    return n, real_like, flat_like


def check_kalshi_fills():
    from core.kalshi_client import KalshiClient
    cfg = Config(); kc = KalshiClient(cfg); kc._init_http()
    d = kc._get("/trade-api/v2/portfolio/fills?limit=100")
    fills = d.get("fills", []) if d else []
    act = formula = 0.0
    for f in fills:
        cnt = float(f["count_fp"])
        p = float(f["yes_price_dollars"]) if f["side"] == "yes" else float(f["no_price_dollars"])
        act += float(f["fee_cost"]); formula += 0.07 * p * (1 - p) * cnt
    return len(fills), act, formula


def check_health():
    active = subprocess.run(["systemctl", "is-active", "kalshi-bot"],
                            capture_output=True, text=True).stdout.strip()
    ntb = -1
    try:
        out = subprocess.run(["journalctl", "-u", "kalshi-bot", "--since", DEPLOY_TS, "--no-pager"],
                             capture_output=True, text=True).stdout
        if out:
            ntb = sum(1 for l in out.splitlines() if "Traceback" in l or "FATAL" in l)
    except Exception:
        pass
    return active, ntb


async def main():
    lines = ["🔍 *Fee-fix post-deploy verify (②③ bake)*"]
    anomalies = []
    try:
        n, real_like, flat_like = check_recorded_fees()
        if n == 0:
            lines.append("• Recorded fees: no new trades since deploy (no fills in window yet).")
        else:
            lines.append(f"• New trades since deploy: {n} | real-curve fee: {real_like} | flat-like: {flat_like}")
            if flat_like > 0:
                anomalies.append(f"{flat_like}/{n} new trades still recorded a flat-$0.07-like fee")
    except Exception as e:
        anomalies.append(f"recorded-fee check error: {e}")

    try:
        nf, act, formula = check_kalshi_fills()
        ratio = (formula / act) if act else 0.0
        lines.append(f"• Kalshi /fills (last {nf}): actual ${act:.2f} vs taker-formula ${formula:.2f} "
                     f"(formula/actual={ratio:.2f}; >1 ⇒ maker fills, fee even lower)")
        if act > formula * 1.2:
            anomalies.append(f"actual fee ${act:.2f} EXCEEDS taker formula ${formula:.2f} — unexpected")
    except Exception as e:
        anomalies.append(f"kalshi-fills check error: {e}")

    active, ntb = check_health()
    tb_str = "n/a" if ntb < 0 else str(ntb)
    lines.append(f"• Bot: {active} | tracebacks since deploy: {tb_str}")
    if active != "active":
        anomalies.append(f"bot not active ({active})")
    if ntb > 0:
        anomalies.append(f"{ntb} tracebacks since deploy")

    if not anomalies:
        lines.append("\n✅ *GREEN — fee fix clean. Ready to start ④ (gate→real-fee migration).*")
    else:
        lines.append("\n⚠️ *ANOMALIES — hold ④ until reviewed:*")
        lines += [f"  - {a}" for a in anomalies]

    text = "\n".join(lines)
    cfg = Config()
    if "--print-only" not in sys.argv and cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
        from monitoring.notifier import TelegramNotifier
        await TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN, cfg.TELEGRAM_CHAT_ID).send(text, msg_type="fee_fix_verify")
    print(text)


if __name__ == "__main__":
    asyncio.run(main())
