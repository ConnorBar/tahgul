# Client report links

A read-only progress page per client (or per project) that you can send: hours by week, budget used on the project and each agreed scope, and tasks finished and coming up. Open it from **Clients → Share report**.

## What's on it

| Always | If you tick it | Never |
| --- | --- | --- |
| Projects, hours by week, budget used (hours), agreed scopes, period switcher (this month, last month, 90 days, all time) | **Tasks** finished and coming up (titles, status, due date), on by default · **Money**: billable amounts, fixed fees and retainers, issued invoices · **Work log**: dates, hours and time-entry notes | Rates, other clients, ideas, voice notes, comments, task descriptions, anything in drafts |

Each link has an expiry (30 / 90 days, a year, or never) and can be turned off at any time; it stops working immediately. The dialog shows how often and when it was last opened. Pages are `noindex` and send no referrer.

## Sending it

**On your tailnet** (default): the link is `https://tahgul.<tailnet>.ts.net/r/<token>`. Useful for checking it yourself, but a client can't open it. Use **PDF** on the link (or "Download PDF" on the page) and email the file.

**On the internet** (optional): Tahgul stays private, and a second, tiny app that serves only `/r/…` pages is published with [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) on port 10000:

1. In the Tailscale admin console, allow Funnel for the `tahgul` node (Access controls → `nodeAttrs` with `funnel`).
2. In `deploy/.env`:
   ```
   TS_SERVE_FILE=serve-public-reports.json
   TAHGUL_PUBLIC_REPORT_URL=https://tahgul.<tailnet>.ts.net:10000
   ```
3. `docker compose --profile tailscale --profile public-reports up -d --build`

Now **Copy link** gives `https://tahgul.<tailnet>.ts.net:10000/r/<token>`, which opens anywhere. That address answers report pages and nothing else: the `reports` container has no login, no API, no web app and no files (there's a test for that). The app itself is still only on 443 inside the tailnet.

## API

Owner, or a token with `admin` (links share client data):

| Method | Path | |
| --- | --- | --- |
| GET/POST | `/api/v1/clients/{id}/report-links` | List / create (`project_id`, `show_amounts`, `show_tasks`, `show_notes`, `expires_in_days`) |
| PATCH | `/api/v1/report-links/{id}` | Change what's shown or the expiry |
| DELETE | `/api/v1/report-links/{id}` | Turn it off |
| GET | `/r/{token}?period=this-month\|last-month\|90d\|all` | The page (no login) |
| GET | `/r/{token}/pdf?period=…` | The same as a PDF |
