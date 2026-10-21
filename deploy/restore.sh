#!/usr/bin/env bash
# Restore a backup made by deploy/backup.sh. This REPLACES the current data.
#
#   deploy/restore.sh backups/2026-10-21_023000
#
# On a new machine: clone the repo, copy the backup's `env` file to deploy/.env, run
# `docker compose up -d --build` once so the volumes exist, then run this.
set -euo pipefail

src=${1:?usage: deploy/restore.sh <backup folder>}
src=$(cd "$src" && pwd)
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f deploy/docker-compose.yml)

(cd "$src" && sha256sum --quiet -c SHA256SUMS)
if [[ "${YES:-}" != "1" ]]; then
  read -r -p "Replace the database, files and calendars with $src? Type 'restore': " answer
  [[ "$answer" == "restore" ]] || { echo "Cancelled."; exit 1; }
fi

if [[ -f "$src/env" && ! -f deploy/.env ]]; then
  install -m 600 "$src/env" deploy/.env
  echo "Copied settings to deploy/.env"
fi

echo "Stopping the app..."
"${COMPOSE[@]}" stop api radicale
"${COMPOSE[@]}" up -d --wait db

echo "Restoring the database..."
"${COMPOSE[@]}" exec -T db dropdb -U tahgul --if-exists --force tahgul
"${COMPOSE[@]}" exec -T db createdb -U tahgul tahgul
"${COMPOSE[@]}" exec -T db pg_restore -U tahgul -d tahgul --no-owner < "$src/db.dump"

echo "Restoring files..."
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api \
  -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < "$src/data.tar.gz"
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh radicale \
  -c 'find /data -mindepth 1 -delete && tar -C /data -xzf -' < "$src/radicale.tar.gz"

echo "Starting the app (migrations run on start)..."
"${COMPOSE[@]}" up -d --wait api radicale
echo "Restored from $src."
