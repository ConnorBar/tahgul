# Project Handout: Consultancy Ops App

2026-10-20 · Connor

## Overview

Ledger (working name) is one self-hosted app for running a solo consultancy. It holds clients, projects, tasks, billable time, invoices and loose ideas in one place. It runs on the homelab, is reached only over Tailscale, and exposes an API so Siri and a local agent can drive it by voice.

**Goals**

- One place for work: no separate time tracker, to-do app and calendar to keep in sync.
- Two-way sync with Apple Calendar, so the phone's built-in apps and Siri keep working.
- Billing that's right: rates, billable hours and invoices a client can trust.
- Agent-first: anything doable in the UI is doable through the API.
- Nothing leaves the homelab. No third-party servers hold client data.

**Non-goals (v1)**

- Multi-user teams, permissions or SSO. One owner, possibly a guest view for clients later.
- Recording audio. Yog records; Ledger stores, links and plays back.
- Payroll, expenses or full accounting. Export to the accountant's tool instead.
- A native iOS app. A mobile-friendly web app (installable as a PWA) plus Siri is enough.

## What we take from Kimai, Vikunja and Toggl

Each tool does one thing best; Ledger copies that thing and skips the rest.

| From | Borrowed idea | How it shows up in Ledger |
| --- | --- | --- |
| Kimai | Layered rates (client → project → activity), most specific wins | Rate resolved when an entry is saved and stored on the entry, so old work never re-prices |
| Kimai | Billable / exported flags on every entry | `billable` and `invoice_id` on time entries; invoiced hours can't be billed twice |
| Kimai | Invoices built from a filter, grouped by a "calculator" | Pick client + date range → lines grouped by activity, project or day → PDF |
| Kimai | Invoice number patterns | Pattern like `{Y}-{seq:3}`, stored, gap-free |
| Kimai | Budgets per project | Hours or money budget per project and per scope, with burn-down |
| Vikunja | Kanban with custom buckets per project | Board view per project; columns are statuses you define |
| Vikunja | Same tasks in list, board, table and calendar views | One task table, many views |
| Vikunja | Labels, priorities, due dates, subtasks, relations | Issue fields: type, priority, labels, assignee-less, blocks/blocked-by |
| Vikunja | CalDAV for tasks | Tasks with due dates can appear in Apple Reminders (later milestone) |
| Toggl | One-click running timer, always visible | Global timer bar; start from any task; one timer at a time |
| Toggl | Workspaces → clients → projects → tags | Same hierarchy, workspaces separate business from personal |
| Toggl | Idle detection and "forgot to stop" handling | Timer running past a set limit prompts to trim or keep |
| Toggl | Calendar-side view of your day | Day view showing calendar events next to time entries, drag an event into a time entry |
| Toggl | Good reports and a clean API | Reports by client/project/week; API as first-class as the UI |

## Core concepts and data model

Everything hangs off a workspace: Workspace → Client → Project → Scope → Task, with time entries pointing at a task or project. Ideas are projects with no client and status `idea`, so a voice note can grow into real work without moving.

| Entity | What it is | Key fields |
| --- | --- | --- |
| Workspace | A top-level space (e.g. "Consulting", "Personal") | name, default currency, default hourly rate, tax rate, invoice number pattern |
| Client | Who you bill | name, contact email, billing address, rate override, payment terms (days), tax override |
| Project | A body of work, or an idea | client (optional), status (idea / active / paused / done), rate override, budget (hours or money), colour |
| Scope | An agreed chunk of a project: a statement of work, phase or retainer | project, title, description, type (fixed fee / hourly / retainer), budget, start/end dates, status (draft / agreed / done) |
| Activity | The kind of work (audit, setup, support, travel) | name, rate override, billable by default |
| Task (issue) | A unit of work on the board | project, scope (optional), title, description (Markdown), type (task / bug / idea / request), status (board column), priority, labels, due date, estimate, parent task, blocked-by links |
| Board column | A status a project's board uses | project, name, order, counts as done (yes/no) |
| Time entry | A block of work | start, end, duration, project, task (optional), activity, notes, billable, rate (resolved and frozen at save), amount, invoice id, source (timer / manual / calendar / agent) |
| Calendar event | A mirror of an event from a linked calendar | calendar, remote id, title, start/end, etag, linked project/task/time entry |
| Invoice | A bill sent to a client | number, client, date, due date, status (draft / sent / paid / void), lines, subtotal, tax, total, PDF path |
| Voice note | Audio recorded in Yog | file path, duration, mime type, transcript, linked project/task, recorded at, Yog id |
| Comment / activity log | History on a task or project | author (you, agent, system), body, created at |

**Rate resolution** (most specific wins): time-entry override → activity → scope → project → client → workspace default. The result is frozen onto the entry when saved; changing a rate later only affects new work unless you choose "re-price unbilled entries".

Every record carries `id` (UUID), `created_at`, `updated_at` and `deleted_at` (soft delete), so the agent and sync can work safely and nothing is lost by accident.

## Features

Five areas make up v1; each is usable from the web UI and the API.

**Time tracking**

- Running timer in a bar at the top of every page; start it from a task, a project or blank.
- Manual entries, and a week grid (timesheet) for filling in after the fact.
- Turn a calendar event into a time entry in one click ("I was in that meeting, bill it").
- Rounding rules per workspace (e.g. round up to 6 or 15 minutes), applied at invoice time, not on raw entries.
- Long-running-timer warning (default 4 h) and overlap detection.

**Kanban and issue tracking**

- Board per project with columns you define; drag cards between columns.
- List, table and calendar views over the same tasks; filters saved as views (e.g. "due this week, all clients").
- Issue types (task, bug, idea, request), priority, labels, due dates, estimates, subtasks, blocked-by links.
- Markdown descriptions, comments and an activity log that records who changed what (you, the agent, sync).
- Quick-add bar with light parsing: `Fix VPN for Acme tomorrow !high #network`.

**Scopes and budgets**

- A scope is the agreed piece of work: fixed fee, hourly cap or monthly retainer.
- Each scope shows hours used vs budget and money used vs budget, with a warning at 80% and 100%.
- Tasks and time entries can be tagged to a scope, so "what's left on the Acme audit" is one number.
- Work outside every scope is flagged as possible scope creep.

**Invoicing**

- Build an invoice from a client + date range; pulls billable, un-invoiced entries.
- Group lines by activity, project, scope or day; fixed-fee scopes appear as one line.
- Tax, payment terms, invoice number pattern and notes per client or workspace.
- HTML template rendered to PDF; status draft → sent → paid; entries locked once invoiced.
- CSV export of entries and invoices for the accountant.

**Ideas inbox and reports**

- Ideas are projects in status `idea`, shown in an inbox with their voice notes and transcripts.
- "Promote" turns an idea into an active project, keeping its notes and tasks.
- Reports: hours and money by client, project, scope and week; unbilled hours; effective hourly rate.

## Calendar: two-way Apple Calendar sync

Ledger syncs with calendars over CalDAV, the open standard Apple Calendar already speaks, so events made on the phone show in Ledger and events made by Ledger (or the agent) show on the phone.

**Where the calendar lives (pick one per calendar)**

| Option | How | Data location | Notes |
| --- | --- | --- | --- |
| Self-hosted (recommended) | Radicale CalDAV server in the same Docker stack; iPhone/Mac add it as a CalDAV account | Homelab only | iOS needs HTTPS: use `tailscale serve` for a valid `*.ts.net` certificate. Works away from home with Tailscale on. |
| iCloud | Ledger connects to iCloud CalDAV with an app-specific password | Apple's servers | Easiest if you keep using iCloud calendars; less private. |

Ledger is a CalDAV *client* in both cases, so the same sync code covers both.

**Sync rules**

- Pull every 2–5 minutes using CalDAV sync tokens (only changes since last sync), plus an on-demand "sync now".
- Push immediately when an event is created, moved or deleted in Ledger or through the API.
- Each mirrored event stores the remote id and `etag`; a write sends `If-Match` so a change made on the phone is never silently overwritten.
- Conflict: if both sides changed, the newest change wins and the loser is kept in the activity log.
- Ledger writes only to calendars you mark writable; read-only calendars (e.g. a client's shared calendar) are shown, never changed.

**What shows up where**

- Calendar events appear in Ledger's day/week view next to time entries.
- Tasks with a due time can be placed on the calendar as time blocks (optional, per task).
- Time entries can optionally mirror to a "Billable" calendar, so the phone shows what was billed.
- An event can be linked to a project; ending a linked event prompts "log this as time?" (in the UI and through the agent).

**Later:** expose tasks as CalDAV to-dos so they also appear in Apple Reminders.

## Agent API

Ledger exposes one REST API (JSON, OpenAPI-documented at `/api/docs`) that the web UI itself uses, plus an MCP server wrapping the same calls, so Siri Shortcuts, scripts and a local agent all get the same powers as the UI.

**Design rules**

- Versioned under `/api/v1`; API tokens per client (Siri, agent, scripts), each with scopes (read, write, invoice) and revocable.
- Every write accepts an `Idempotency-Key` header, so a retried voice command never creates two tasks.
- Writes from tokens are logged with the token's name ("created by agent").
- Friendly lookups: `project` and `client` accept a name as well as an id; fuzzy matches return a `needs_confirmation` response with candidates instead of guessing.
- Webhooks (later): POST to a URL on events like `timer.stopped`, `task.due_soon`, `calendar.event_ended` so the agent can react.

**Core endpoints (v1)**

| Method | Path | Does |
| --- | --- | --- |
| POST | `/api/v1/tasks` | Create a task (title, project, due, priority, labels, scope) |
| PATCH | `/api/v1/tasks/{id}` | Update fields or move column (`status`) |
| GET | `/api/v1/tasks?project=&status=&due_before=` | Search and list tasks |
| POST | `/api/v1/timer/start` | Start the timer (task or project, notes) |
| POST | `/api/v1/timer/stop` | Stop the running timer, returns the entry |
| GET | `/api/v1/timer` | What's running and for how long |
| POST | `/api/v1/time-entries` | Log time after the fact ("2 hours on Acme yesterday afternoon") |
| GET | `/api/v1/reports/summary?client=&from=&to=` | Hours, money, budget left |
| GET/POST/PATCH | `/api/v1/events` | Read and write calendar events (synced to CalDAV) |
| POST | `/api/v1/ideas` | Create an idea project, optionally with a voice note |
| POST | `/api/v1/voice-notes` | Upload audio + metadata (used by Yog) |
| POST | `/api/v1/invoices/draft` | Draft an invoice for a client and date range (never auto-sent) |
| POST | `/api/v1/quick` | Natural-language-lite quick add: `{"text": "Call Laura Thursday 2pm #acme"}` |

**MCP server**

A small MCP server (`ledger-mcp`) exposes tools like `create_task`, `start_timer`, `stop_timer`, `log_time`, `list_tasks`, `create_event`, `summary` and `capture_idea`. A local model (via Ollama or llama.cpp) with tool calling uses these directly; no cloud model is needed.

**Siri path (no agent needed)**

Apple Shortcuts call the REST API over Tailscale: "Hey Siri, start timer" → Shortcut → `POST /timer/start`; "Hey Siri, new task" → dictate → `POST /quick`. Later, the same Shortcut can send the dictated text to the local agent instead, which picks the right tool.

## Audio and Yog integration

Yog records; Ledger stores, links, transcribes (optionally) and plays back. v1 builds the storage, playback and upload API so Yog can plug in later without changes on Ledger's side.

**Hand-off contract (Yog → Ledger)**

- `POST /api/v1/voice-notes` as `multipart/form-data`: `file` (m4a, mp3, wav, webm or ogg; max 200 MB) and `metadata` JSON.
- Metadata: `yog_id` (Yog's own id, used for de-duplication), `recorded_at`, `duration_seconds`, `title` (optional), `transcript` (optional, if Yog already has one), and a target: `project_id`, `task_id`, or `new_idea: {"title": "..."}`.
- Response: the voice note id, its playback URL and the project/task it was attached to.
- Same `yog_id` sent twice returns the existing note instead of making a copy.

**Storage and playback**

- Files are stored on disk (a mounted volume, e.g. on the NAS) under `audio/{yyyy}/{mm}/{uuid}.{ext}`; the database keeps the path, size, checksum and metadata.
- Playback in the browser with an HTML audio player: play/pause, scrub, 1× / 1.5× / 2× speed. Served with HTTP range requests so long notes seek instantly.
- Voice notes show on the idea, project or task they belong to, newest first, with the transcript beneath if there is one.
- Download the original file at any time; deleting a task never deletes its audio without asking.

**Transcription (optional, later milestone)**

A local Whisper service (e.g. faster-whisper in the same stack) transcribes new notes in the background. The transcript is searchable, and the agent can turn it into tasks ("pull the to-dos out of this idea").

## Architecture and stack

One Docker Compose stack on the homelab: a Python API that also serves the web app, Postgres, Radicale for calendars, and a Tailscale sidecar as the only way in.

```
Phone / browser / agent / Yog --(Tailscale)--> Ledger API (FastAPI + web)
                                                  |-- PostgreSQL
                                                  |-- Radicale (CalDAV) <-- iPhone Calendar
                                                  |-- audio volume (NAS)
                                                  `-- Whisper (later)
```

Phone, browser, agent and Yog all go through Tailscale to the Ledger API; the iPhone's Calendar app also syncs with Radicale over the same tailnet.

| Layer | Choice | Why |
| --- | --- | --- |
| API | Python 3.12, FastAPI, SQLAlchemy 2 + Alembic migrations | Auto OpenAPI docs for the agent; strong CalDAV, PDF and Whisper libraries in Python |
| Database | PostgreSQL 16 | Reliable, easy backups (`pg_dump`), full-text search for tasks and transcripts |
| Background jobs | APScheduler inside the API to start; a worker container later if needed | Calendar sync, reminders, transcription |
| Web app | React + TypeScript + Vite, TanStack Query, dnd-kit for the board, FullCalendar for day/week views | Fast, mature drag-and-drop and calendar pieces |
| Calendar | `caldav` Python library as client; Radicale as the self-hosted server | Standard CalDAV; works with iCloud too |
| Invoices | Jinja2 HTML template → WeasyPrint PDF | Invoices are just styled HTML; easy to edit |
| MCP | Official MCP Python SDK, calling the REST API with its own token | Same rules and logging as every other client |
| Network | Tailscale container with `tailscale serve` for HTTPS on `ledger.<tailnet>.ts.net` | No ports open to the internet |

**Repo:** [ConnorBar/tahgul](https://github.com/ConnorBar/tahgul). This handout is committed as `docs/SPEC.md`, and each milestone lands on its own branch with a pull request.

**Repo layout**

```
ledger/
  api/            FastAPI app: models, routes, services (rates, invoicing, caldav sync)
  web/            React app (built into api/static for production)
  mcp/            ledger-mcp server
  deploy/         docker-compose.yml, Radicale config, Tailscale config, backup script
  docs/           this handout as SPEC.md, API notes, decisions
  tests/          pytest (rates, invoices, sync conflicts) + Playwright smoke tests
```

While it's being built, the stack can also run anywhere Docker runs (laptop, a cloud dev box) with demo data, so development doesn't depend on the homelab.

## Security and privacy

The main protection is that Ledger is never on the public internet; the rules below cover the rest.

- **Network:** reachable only on the tailnet; Tailscale ACLs limit which devices can reach it (phone, laptop, agent box).
- **Login:** single owner account with a strong password plus TOTP; sessions in secure, HTTP-only cookies.
- **API tokens:** one per client, scoped (read / write / invoice), stored hashed, shown once, revocable; last-used time visible.
- **Agent limits:** agent tokens can't delete, send invoices or change rates by default; those need the UI or a separate token.
- **Secrets:** CalDAV and other credentials encrypted at rest with a key from an environment variable, never in the repo.
- **Backups:** nightly `pg_dump` + audio folder copied to the NAS and one offline/off-site copy (encrypted, e.g. restic). A restore is tested before go-live.
- **Audit log:** every write records who made it (owner, token name, sync) and what changed.
- **Client data:** nothing is sent to outside services, including models; transcription and the agent run locally.

## Build plan

Eight milestones, each ending in something usable; build in this order and ship each to the homelab before starting the next.

1. **Skeleton.** Repo, Docker Compose, Postgres, FastAPI, React shell, login, API tokens, CI running tests. *Done when:* `docker compose up` gives a login page and `/api/docs`.
2. **Workspaces, clients, projects, activities.** CRUD in UI and API, rate fields, rate resolution with tests. *Done when:* a project's effective rate is correct in every override case.
3. **Time tracking.** Timer bar, manual entries, week timesheet, reports summary. *Done when:* a week of real work can be logged and summed by client.
4. **Tasks and kanban.** Tasks, custom columns, board drag-and-drop, list view, quick add, comments, activity log. *Done when:* a real project is run from the board for a week.
5. **Agent API and Siri.** Idempotency keys, name lookups, `/quick`, ledger-mcp server, two example Shortcuts (start/stop timer, add task). *Done when:* "Hey Siri, new task" creates a task on the board.
6. **Calendar sync.** Radicale in the stack, CalDAV client, two-way sync with etags, day/week view, event → time entry. *Done when:* an event made on the iPhone appears in Ledger and one made by the agent appears on the iPhone.
7. **Scopes and invoicing.** Scopes with budgets, invoice builder, PDF template, number pattern, lock invoiced entries, CSV export. *Done when:* a real invoice can be sent to a client.
8. **Ideas and voice notes.** Ideas inbox, voice-note upload API, storage, playback, promote idea → project. *Done when:* Yog (or `curl`) uploads a note and it plays back on the idea. Whisper transcription follows as 8b.

Later: webhooks, tasks in Apple Reminders, a client-facing read-only report link, mobile PWA polish.

## Open questions

- [ ] App name: keep "Ledger" or pick another?
- [ ] Calendar home: move to self-hosted Radicale, or keep iCloud and sync with it?
- [ ] GitHub repo: [ConnorBar/tahgul](https://github.com/ConnorBar/tahgul) chosen; give the Claude GitHub app access to it so code can be pushed. Should the app be called Tahgul too?
- [ ] Homelab host: what machine and OS will run the Docker stack, and where should audio and backups live?
- [ ] Currency, tax/VAT and invoice number format to start with.
- [ ] Yog: what stack is it, what audio format does it record, and does it already transcribe?
- [ ] Stack preference: happy with Python (FastAPI) + React, or rather TypeScript end to end?
