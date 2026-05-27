#!/bin/bash
# VACUUM + composite index maint window for 2026-05-28 12:30 UTC
# Idempotent: safe to re-run. Creates pre-maint snapshot.
set -e

DB=/home/ubuntu/kalshi-arb/data/bot.db
SNAP=/home/ubuntu/kalshi-arb/data/bot.db.pre-maint-2026-05-28
LOGDIR=/home/ubuntu/kalshi-arb/logs
mkdir -p $LOGDIR
LOG=$LOGDIR/maint_2026_05_28_$(date -u +%H%M).log

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a $LOG; }

log "=== Maint started ==="
log "Disk before: $(df -h /home/ubuntu | tail -1)"
log "DB size before: $(ls -lh $DB | awk '{print $5}')"

log "1/6 Creating snapshot $SNAP"
cp $DB $SNAP
log "Snapshot size: $(ls -lh $SNAP | awk '{print $5}')"

log "2/6 Stopping kalshi-bot"
sudo systemctl stop kalshi-bot.service
sleep 5
log "Bot stopped: $(systemctl is-active kalshi-bot.service)"

log "3/6 VACUUM (5GB DB, expect 10-20 min)"
START=$(date +%s)
sqlite3 $DB "VACUUM;" 2>&1 | tee -a $LOG
log "VACUUM done in $(($(date +%s) - START))s. DB size after: $(ls -lh $DB | awk '{print $5}')"

log "4/6 CREATE INDEX idx_oo_ts_sport_phase"
START=$(date +%s)
sqlite3 $DB "CREATE INDEX IF NOT EXISTS idx_oo_ts_sport_phase ON odds_observations(timestamp, sport, phase);" 2>&1 | tee -a $LOG
log "INDEX done in $(($(date +%s) - START))s"

log "5/6 ANALYZE odds_observations"
START=$(date +%s)
sqlite3 $DB "ANALYZE odds_observations;" 2>&1 | tee -a $LOG
log "ANALYZE done in $(($(date +%s) - START))s"

log "6/6 Restarting kalshi-bot"
sudo systemctl start kalshi-bot.service
sleep 10
log "Bot status: $(systemctl is-active kalshi-bot.service)"
systemctl status kalshi-bot.service --no-pager | head -8 | tee -a $LOG

log "=== Maint done ==="
log "Final disk: $(df -h /home/ubuntu | tail -1)"
log "Final DB size: $(ls -lh $DB | awk '{print $5}')"
log "Snapshot retained at $SNAP — delete after 24h with: rm $SNAP"
