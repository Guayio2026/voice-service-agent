#!/usr/bin/env bash
# Consistent SQLite backup + housekeeping. Run from cron, e.g. daily 03:20:
#   20 3 * * * /srv/voice-service-agent/deploy/backup.sh >> /var/log/zh-backup.log 2>&1
# Uses ".backup" (online consistent snapshot); plain cp on a live db can
# produce a malformed copy.
set -euo pipefail

ROOT=/srv/voice-service-agent
BACKUP_DIR=/srv/backups
KEEP_DAYS=14
HOUSEKEEP_DAYS=30

mkdir -p "$BACKUP_DIR"
stamp=$(date +%F-%H%M)

if [ -f "$ROOT/runtime/mock.sqlite3" ]; then
  sqlite3 "$ROOT/runtime/mock.sqlite3" ".backup '$BACKUP_DIR/mock-$stamp.sqlite3'"
fi

# Rotate backups.
find "$BACKUP_DIR" -name 'mock-*.sqlite3' -mtime +"$KEEP_DAYS" -delete

# Housekeeping: publish backups and closed-loop reports grow without bound.
find "$ROOT/runtime" -maxdepth 1 -name 'auto-rsi-before-*.sqlite3' -mtime +"$HOUSEKEEP_DAYS" -delete
find "$ROOT/runtime/rsi-loops" -mindepth 1 -maxdepth 1 -type d -mtime +"$HOUSEKEEP_DAYS" -exec rm -rf {} + 2>/dev/null || true

echo "$(date '+%F %T') backup ok: mock-$stamp.sqlite3"
