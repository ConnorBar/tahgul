#!/usr/bin/env bash
# Tahgul on Hive, next to Yog. Same shape as Yog's deploy:
#
#   Docker Compose on localhost ports → the host's `tailscale serve` → tailnet HTTPS
#
#   Yog     127.0.0.1:8780 → https://hive.<tailnet>.ts.net        (443)
#   Tahgul  127.0.0.1:8790 → https://hive.<tailnet>.ts.net:8443   (the app)
#   Radicale 127.0.0.1:8791 → https://hive.<tailnet>.ts.net:8444  (iPhone calendars)
#
# Safe to run again: it never overwrites deploy/.env, and re-applying `tailscale serve`
# for the same port just replaces that port's handler. See docs/hive.md.
#
#   deploy/hive/setup.sh                 # first install, or after `git pull`
#   deploy/hive/setup.sh --no-serve      # leave tailscale serve alone
#   deploy/hive/setup.sh --off           # stop publishing to the tailnet (stack keeps running)

set -euo pipefail
cd "$(dirname "$0")/../.."

APP_PORT=${TAHGUL_PORT:-8790}
CAL_PORT=${RADICALE_PORT:-8791}
APP_HTTPS=${TAHGUL_HTTPS_PORT:-8443}
CAL_HTTPS=${RADICALE_HTTPS_PORT:-8444}
ENV_FILE=deploy/.env
COMPOSE=(docker compose -f deploy/docker-compose.yml)

say() { printf '\033[1m%s\033[0m\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

for cmd in docker openssl tailscale python3; do
  command -v "$cmd" >/dev/null || die "$cmd is not installed"
done
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is missing"

SERVE=1
case "${1:-}" in
  --no-serve) SERVE=0 ;;
  --off)
    sudo tailscale serve --https="$APP_HTTPS" off || true
    sudo tailscale serve --https="$CAL_HTTPS" off || true
    tailscale serve status || true
    exit 0 ;;
  "") ;;
  *) die "unknown option $1" ;;
esac

HOST=$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')
[ -n "$HOST" ] || die "this machine isn't on a tailnet (tailscale up first)"

# 1. Secrets ---------------------------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  say "Keeping the existing $ENV_FILE"
else
  say "Writing $ENV_FILE with fresh secrets"
  CAL_USER=${RADICALE_USER:-${USER:-connor}}
  CAL_PASS=$(openssl rand -hex 16)
  umask 077
  cat > "$ENV_FILE" <<EOF
# Written by deploy/hive/setup.sh on $(date +%F). Back this file up with the database:
# TAHGUL_SECRET_KEY is needed to read saved calendar passwords.
POSTGRES_PASSWORD=$(openssl rand -hex 24)
TAHGUL_SECRET_KEY=$(openssl rand -hex 32)

# Calendar server. The iPhone logs in with these (server $HOST:$CAL_HTTPS).
RADICALE_USER=$CAL_USER
RADICALE_PASSWORD=$CAL_PASS

# Localhost ports, published to the tailnet by \`tailscale serve\` (Yog has 8780 / 443).
TAHGUL_PORT=$APP_PORT
RADICALE_PORT=$CAL_PORT
TAHGUL_REPORTS_PORT=8792

# Served over HTTPS by Tailscale, so the login cookie is Secure.
TAHGUL_COOKIE_SECURE=true
TAHGUL_APP_URL=https://$HOST:$APP_HTTPS

# Transcription: start with --profile whisper and set http://whisper:8000
TAHGUL_WHISPER_URL=
EOF
  say "Calendar login for the iPhone: $CAL_USER / $CAL_PASS  (also in $ENV_FILE)"
fi

# 2. Stack -----------------------------------------------------------------------------
say "Building and starting the stack"
BUILD=--build
[ "${TAHGUL_BUILD:-1}" = 0 ] && BUILD=--no-build   # use images already built
"${COMPOSE[@]}" up -d "$BUILD" --wait

# 3. Owner account (first install only) ------------------------------------------------
if "${COMPOSE[@]}" exec -T api python -c '
import sys
from sqlalchemy import func, select
from tahgul.db import get_sessionmaker
from tahgul.models import User
with get_sessionmaker()() as db:
    sys.exit(0 if db.scalar(select(func.count()).select_from(User)) else 1)
' </dev/null 2>/dev/null; then
  say "Owner account exists"
else
  say "Create your login"
  read -rp "Email: " OWNER_EMAIL
  TTY=(); [ -t 0 ] || TTY=(-T)
  "${COMPOSE[@]}" exec "${TTY[@]}" api tahgul create-owner --email "$OWNER_EMAIL"
fi

# 4. Tailnet ---------------------------------------------------------------------------
if [ "$SERVE" = 1 ]; then
  say "Publishing on the tailnet (sudo tailscale serve)"
  sudo tailscale serve --bg --https="$APP_HTTPS" "http://127.0.0.1:$APP_PORT"
  sudo tailscale serve --bg --https="$CAL_HTTPS" "http://127.0.0.1:$CAL_PORT"
  tailscale serve status
fi

cat <<EOF

Tahgul      https://$HOST:$APP_HTTPS
Calendars   $HOST:$CAL_HTTPS   (iPhone: Settings → Calendar → Accounts → Other → CalDAV)
Yog         https://$HOST       (untouched)

Only tailnet devices allowed to reach port $APP_HTTPS can open it. The family ACL that
grants tag:yog:443 doesn't, which is what keeps Tahgul private. See docs/hive.md.
EOF
