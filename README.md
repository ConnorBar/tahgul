# Tahgul

One self-hosted app for running a solo consultancy: clients, projects, tasks, billable time, invoices and ideas in one place. It runs on the homelab, is reachable only over Tailscale, and exposes an API so Siri and a local agent can drive it.

The full design is in [docs/SPEC.md](docs/SPEC.md). Each milestone in the build plan lands on its own branch with a pull request.

## Run it

```sh
cp deploy/.env.example deploy/.env   # passwords, TAHGUL_SECRET_KEY, Radicale login, owner
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
mcp/      tahgul-mcp server (MCP tools over the REST API)
deploy/   docker-compose.yml, Radicale image, Tailscale serve config, entrypoint
docs/     SPEC.md, decisions.md, siri-shortcuts.md, agent.md, calendar.md, invoicing.md
tests/    pytest
```

## Auth model

- **Owner** signs in with email + password, plus TOTP once turned on (Settings → Security). The session lives in a secure, HTTP-only, `SameSite=Strict` cookie; cookie-authenticated writes also need an `X-Requested-With: tahgul` header.
- **API tokens** (Settings → API tokens) are one per client, scoped `read` / `write` / `invoice` / `admin`, stored as SHA-256 hashes, shown once and revocable. Send them as `Authorization: Bearer thg_…`. Deleting records and changing rates need `admin`; tokens can never manage tokens or security settings.
- Every write lands in `audit_log` with who made it.

## Time

- **Timer:** one at a time across all workspaces; starting a new one stops the old. It lives in the top bar and can start with or without a project. Past the workspace's warning limit (default 4 h) it offers to trim, keep or discard.
- **Entries:** times without an offset are read in the workspace time zone (default America/Chicago). Each entry freezes its rate and amount when saved; editing the project, activity, times or override re-prices it, and Settings → Workspace can re-price everything not yet invoiced.
- **Timesheet:** typing hours into the week grid (`1.5`, `1:30`, `90m`) adds or trims manual entries; timer entries are never shortened from the grid.
- **Reports:** `GET /api/v1/reports/summary?from=&to=&client=` gives hours, money, unbilled amount and effective rate by client, project and week, plus budget used across all time.
- **Rounding** (e.g. up to 6 minutes) is a workspace setting applied when invoicing; logged time stays exact.

## Tasks

- Every project has a board; new projects start with To do / In progress / Done. Columns can be added, renamed, reordered, marked as "done" or deleted (their tasks move first).
- Tasks have a short per-workspace number (`#12`, also `GET /api/v1/tasks/by-number/12`), type, priority, labels, due date and time, estimate, subtasks and blocked-by links (cycles are refused). `PATCH /tasks/{id}` takes a column name or id as `status`, or `done: true/false`.
- Quick add understands `Fix VPN for Acme tomorrow !high #network`: `!priority`, `#label` (or `#project`/`#client` when the word matches one), `for Acme`/`@Acme`, dates like `fri`, `next monday`, `in 3 days`, `oct 30`, and times like `2pm`. A client with several open projects comes back as candidates instead of a guess. `POST /api/v1/tasks/quick` with `dry_run: true` previews.
- Comments are Markdown; `GET /tasks/{id}/history` merges them with every change and who made it (you, an agent token, sync).
- Saved views store task filters (`open`, `due_within_days`, `priority`, `client`, …) under `/api/v1/views`.
- Timers and entries can point at a task (`task_id`), which sets the project.

## Agents and Siri

- `POST /api/v1/quick {"text": "..."}` turns one sentence into a task, a timer start or stop, logged time, an idea, or an answer ("what's running", "how many hours this week"). It replies with `speech` for Siri to say. Recipes are in [docs/siri-shortcuts.md](docs/siri-shortcuts.md).
- Every write accepts an `Idempotency-Key` header. A retry with the same key gets the first response back (`Idempotent-Replayed: true`), so a repeated voice command never makes two tasks.
- Projects, clients and activities can be named instead of passed by id (`"project": "acme"`). Anything fuzzy or ambiguous returns `409 needs_confirmation` with candidates instead of a guess.
- `tahgul-mcp` (in `mcp/`) gives a local model the same powers as MCP tools; see [docs/agent.md](docs/agent.md).

## Calendar

Two-way CalDAV sync with the Radicale in the stack (or iCloud): events from the phone show in Tahgul's day/week view next to logged time, and events made in Tahgul or by the agent show on the phone. Optional Billable calendar mirroring every time entry, task time blocks, and "log this meeting as time?". Setup, including adding Radicale to the iPhone, is in [docs/calendar.md](docs/calendar.md).

## Scopes and invoices

Scopes (hourly, fixed fee, monthly retainer) track burn against a budget and flag work outside them. Invoices are drafted from a client and a period, edited, then issued with a gap-free number and a PDF; time on an invoice is locked. CSV exports for the accountant. See [docs/invoicing.md](docs/invoicing.md).

## Rates

The rate for a piece of work is the most specific one set: time-entry override → activity → scope → project → client → workspace default. `GET /api/v1/projects/{id}/rate?activity_id=` shows the answer and every level behind it. A rate of 0 counts as set (pro bono).
