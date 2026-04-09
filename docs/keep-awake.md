# Keep Mac Awake for Trading Bot

## Current Status

`caffeinate -is` is running (PID: 63119). Your Mac will stay awake and the bot will keep trading even with the display closed.

## Stop caffeinate

```bash
kill 63119
```

Or kill all caffeinate processes:

```bash
killall caffeinate
```

## Restart caffeinate

```bash
caffeinate -is &
```

- `-i` — prevents idle sleep (no user activity)
- `-s` — prevents system sleep (lid closed)

## Restart the trading bot

```bash
cd /Users/taejunoh/Desktop/LFG/kalshi-arb
source venv/bin/activate
python main.py &
```

Dashboard: http://localhost:8080
