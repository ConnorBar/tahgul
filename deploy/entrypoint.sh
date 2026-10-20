#!/bin/sh
# Apply database migrations, then start the API.
set -e
cd /app
alembic upgrade head
exec "$@"
