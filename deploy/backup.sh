#!/usr/bin/env bash
# Back up Tahgul: the database, invoice PDFs and audio, calendars, and deploy/.env.
#
#   deploy/backup.sh                     # writes backups/<timestamp>/ next to the repo
#   BACKUP_DIR=/mnt/nas/tahgul-backups deploy/backup.sh
#
# Nightly from cron on the homelab (2:30am):
#   30 2 * * * cd /opt/tahgul && BACKUP_DIR=/mnt/nas/tahgul-backups deploy/backup.sh >> /var/log/tahgul-backup.log 2>&1
#
# Off-site copy: set RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE (and install restic) and
# each backup is also sent there, encrypted, with old snapshots pruned.
#
# Restore with deploy/restore.sh <backup folder>. Try a restore before you rely on this.
set -euo pipefail
umask 077

cd "$(dirname "$0")/.."
BACKUP_DIR=${BACKUP_DIR:-$PWD/backups}
KEEP_DAYS=${KEEP_DAYS:-14}
COMPOSE=(docker compose -f deploy/docker-compose.yml)

stamp=$(date +%Y-%m-%d_%H%M%S)
out="$BACKUP_DIR/$stamp"
mkdir -p "$out"
chmod 700 "$out"
echo "Backing up to $out"

# 1. Database: a consistent snapshot while the app keeps running.
"${COMPOSE[@]}" exec -T db pg_dump -U tahgul -d tahgul -Fc > "$out/db.dump"

# 2. Files: invoice PDFs and voice notes (/data in the api container).
"${COMPOSE[@]}" exec -T api tar -C /data -cf - . | gzip > "$out/data.tar.gz"

# 3. Calendars (Radicale's collections).
"${COMPOSE[@]}" exec -T radicale tar -C /data -cf - . | gzip > "$out/radicale.tar.gz"

# 4. Settings. deploy/.env holds TAHGUL_SECRET_KEY, without which saved calendar
#    passwords can't be decrypted, so it travels with the backup.
if [[ -f deploy/.env ]]; then
  install -m 600 deploy/.env "$out/env"
fi

(cd "$out" && sha256sum -- * > SHA256SUMS)
du -sh "$out" | awk '{print "Backup size: " $1}'

# Keep the last KEEP_DAYS days locally.
find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -type d -name '20*' -mtime +"$KEEP_DAYS" \
  -exec rm -rf {} + -print | sed 's/^/Removed old backup: /'

if [[ -n "${RESTIC_REPOSITORY:-}" ]]; then
  restic backup --tag tahgul "$out"
  restic forget --tag tahgul --keep-daily 7 --keep-weekly 4 --keep-monthly 12 --prune
fi
echo "Done."
