#!/bin/sh
# Write the login from RADICALE_USER / RADICALE_PASSWORD (bcrypt), then start Radicale.
set -e
if [ -z "$RADICALE_USER" ] || [ -z "$RADICALE_PASSWORD" ]; then
  echo "Set RADICALE_USER and RADICALE_PASSWORD in deploy/.env" >&2
  exit 1
fi
python - <<'PY'
import os, bcrypt
user, pw = os.environ["RADICALE_USER"], os.environ["RADICALE_PASSWORD"]
hashed = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
with open("/data/users", "w") as f:
    f.write(f"{user}:{hashed}\n")
os.chmod("/data/users", 0o600)
PY
exec python -m radicale --config /config/config
