# Tahgul

One self-hosted app for running a solo consultancy: clients, projects, tasks, billable time, invoices and ideas in one place. It runs on the homelab, is reachable only over Tailscale, and exposes an API so Siri and a local agent can drive it.

The full design is in [docs/SPEC.md](docs/SPEC.md). Each milestone in the build plan lands on its own branch with a pull request.

## Run it

```sh
cp deploy/.env.example deploy/.env   # set POSTGRES_PASSWORD, owner email and password
docker compose up --build
```

Open <http://localhost:8000> for the login page and <http://localhost:8000/api/docs> for the API. The owner account is created on first start from `TAHGUL_OWNER_EMAIL` / `TAHGUL_OWNER_PASSWORD`; remove those from `deploy/.env` afterwards. Or create it by hand:

```sh
docker compose exec api tahgul create-owner --email you@example.com
```

On the homelab, add the Tailscale sidecar so the app is served at `https://tahgul.<tailnet>.ts.net` with a valid certificate (set `TS_AUTHKEY` and `TAHGUL_COOKIE_SECURE=true` first):

```sh
docker compose --profile tailscale up -d --build
```

Lost your authenticator? `docker compose exec api tahgul reset-totp --email you@example.com`.

## Develop

API (Python 3.12):

```sh
cd api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export TAHGUL_DATABASE_URL=postgresql+psycopg://tahgul:tahgul@localhost:5432/tahgul TAHGUL_COOKIE_SECURE=false
alembic upgrade head
tahgul create-owner --email you@example.com
uvicorn tahgul.main:app --reload
```

Web (Node 22), with Vite proxying `/api` to the API on :8000:

```sh
cd web && npm install && npm run dev   # http://localhost:5173
```

Tests and lint, from the repo root. Tests use SQLite unless `TAHGUL_TEST_DATABASE_URL` points at Postgres (CI does):

```sh
pytest
ruff check api tests && ruff format --check api tests
```

After changing models: `cd api && alembic revision --autogenerate -m "what changed"`.

## Layout

```
api/      FastAPI app (tahgul/), Alembic migrations
web/      React + TypeScript + Vite, built into the API image
mcp/      tahgul-mcp server (milestone 5)
deploy/   docker-compose.yml, Tailscale serve config, entrypoint
docs/     SPEC.md, decisions.md
tests/    pytest
```

## Auth model

- **Owner** signs in with email + password, plus TOTP once turned on (Settings → Security). The session lives in a secure, HTTP-only, `SameSite=Strict` cookie; cookie-authenticated writes also need an `X-Requested-With: tahgul` header.
- **API tokens** (Settings → API tokens) are one per client, scoped `read` / `write` / `invoice`, stored as SHA-256 hashes, shown once and revocable. Send them as `Authorization: Bearer thg_…`. Tokens can never manage tokens or security settings.
- Every write lands in `audit_log` with who made it.
