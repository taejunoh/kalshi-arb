import sys, os, subprocess, asyncio
sys.path.insert(0,"/home/ubuntu/kalshi-arb")
def le(p="/home/ubuntu/kalshi-arb/.env"):
    for l in open(p):
        l=l.strip()
        if l and not l.startswith("#") and "=" in l:
            k,_,v=l.partition("="); os.environ.setdefault(k.strip(),v.strip().strip('"').strip("'"))
le()
from core.config import Config
DB=sys.argv[1] if len(sys.argv)>1 else "/home/ubuntu/kalshi-arb/data/ws_ticks_20260602.db"
out=subprocess.run(["/home/ubuntu/kalshi-arb/venv/bin/python","/home/ubuntu/kalshi-arb/scripts/ws_speed_analysis.py",DB],
                   capture_output=True,text=True,cwd="/home/ubuntu/kalshi-arb").stdout
msg="📡 *WS speed-edge analysis (Phase 1)*\n```\n"+out[:3500]+"\n```"
cfg=Config()
if cfg.TELEGRAM_BOT_TOKEN and cfg.TELEGRAM_CHAT_ID:
    from monitoring.notifier import TelegramNotifier
    asyncio.run(TelegramNotifier(cfg.TELEGRAM_BOT_TOKEN,cfg.TELEGRAM_CHAT_ID).send(msg,msg_type="ws_speed"))
print(out)
