# Scopes and invoicing

## Scopes

A scope is an agreed piece of a project. Open a project and pick **Scopes**.

| Type | Billed as | Budget |
| --- | --- | --- |
| Hourly | Its time, at the scope's rate (or the project's) | Optional hours and/or money cap |
| Fixed fee | In parts any time (a deposit, a milestone), and whatever is left once the scope is marked **done**. Time logged in it is covered by the fee | Optional; useful to see whether the fee was worth it |
| Monthly retainer | One line per month between its start and end dates, plus hours over the monthly allowance at the scope's rate | Included hours per month |

Each scope shows hours and money used against its budget, amber at 80% and red at 100%. Time on the project outside every scope is flagged as possible scope creep.

Tag time with a scope from the time-entry form, or tag a task (in the task drawer): time logged against that task, by timer or by hand, picks the scope up.

A scope's rate sits between activity and project in the rate order: entry override → activity → **scope** → project → client → workspace.

## Fixed-price work

A project done for one agreed price: pick **Fixed price** when creating the project and enter the price. That makes a fixed-fee scope covering the whole project (add more fixed-fee scopes for phases if you like). The project header shows where the money stands ("Fixed price $6,000 · $6,000 awaiting payment"), and its **Scopes** tab has a card per fixed fee:

- a bar of **paid**, **awaiting payment** (red when overdue), **on a draft** and **not invoiced**, with each invoice listed;
- **New invoice…**: a draft for part or all of what's left (half, all, or any amount; never more than the fee). Once the scope is marked done, anything still left goes on the client's next draft by itself, as "(fixed fee, balance)".
- **Record a sent invoice**: for an invoice you already sent from somewhere else (Word, another app). Give its number, amount, the date you sent it and, if you like, the PDF. It keeps its own number (Tahgul's numbering is untouched), counts towards *Outstanding* and *Overdue*, and you mark it paid like any other. A mistaken record can be deleted; edit its number, dates or amount from its page.

**Invoices** lists fixed-price work that isn't fully paid under *Fixed-price work*, and *Ready to bill* includes the unbilled rest of done fixed fees. Time logged on a fixed-fee scope never shows up there by the hour.

A fee that's (partly) on invoices can be raised (a change order: the difference is left to bill) but not lowered below what's billed, and the scope can't be deleted. Voiding or deleting an invoice frees its part of the fee.

## Invoices

**Invoices → New invoice** (or **Draft invoice** next to a client under *Ready to bill*):

1. Pick the client, the work period and how lines are grouped (by project, activity, scope, day, or one line per entry).
2. Tahgul builds a **draft** from billable, not-yet-invoiced time in the period, plus finished fixed-fee scopes and retainer months. Rounding (Settings → Workspace) is applied per entry here; logged time stays exact.
3. On the draft you can change the period, dates, tax and notes, reword lines, add lines by hand (expenses, a discount as a negative price), remove lines, or **Refresh from time** to pull in time logged since. The preview on the right is the PDF.
4. **Issue invoice** gives it the next number, freezes the from/to addresses, stores the PDF and marks it sent. Download the PDF and email it to the client.
5. **Mark paid** when the money arrives (with the date). **Mark unpaid** undoes it.

Time on a draft or issued invoice is locked: it can't be edited or billed again. Deleting a draft frees its time. An issued invoice is never deleted: **Void** it instead. The number stays used (no gaps), its PDF gets a VOID stamp, and the time goes back to unbilled for a fresh invoice.

### Numbers

Numbers are assigned only when issuing, in issue order, from a counter per workspace (and per year, month or client, depending on the pattern). Pick the pattern in Settings → Workspace. `{CLIENT}` patterns need a client code (Clients → edit).

### What's on the PDF

Settings → Workspace → *On your invoices*: business name, address, email, how to pay (ACH/Zelle/check details) and a footer line. A client's billing address and email come from the client. Tax is off unless the client or workspace has a rate; it can be changed per invoice.

PDFs live in the API container's `/data` volume (`tahgul-data`), under `invoices/<year>/<number>.pdf`. Back it up with the database. If a stored PDF goes missing, Tahgul renders it again from the invoice.

## Exports

- **Invoices → Export (CSV)**: issued and recorded invoices for the year (number, dates, client, subtotal, tax, total, status, paid date, and whether it was recorded from elsewhere).
- `GET /api/v1/exports/time-entries.csv?from=2026-01-01&to=2026-12-31` (optionally `client_id`, `project_id`): every finished entry with hours, rate, amount and invoice number.

## API

All invoice endpoints need a token with the `invoice` scope (the owner's session always has it). Voiding also needs `admin`. Agents get three MCP tools with such a token: `unbilled_work`, `fixed_fees` ("has Eckerts paid?") and `draft_invoice`; drafts are never issued automatically.

| Method | Path | |
| --- | --- | --- |
| GET | `/api/v1/invoices` | List (`client_id`, `status`, `overdue`, `year`) |
| GET | `/api/v1/invoices/unbilled` | Clients with unbilled time |
| GET | `/api/v1/invoices/totals` | Outstanding, overdue, paid this year |
| POST | `/api/v1/invoices` | Draft from `client_id` or `client` (name/code), `period_start`, `period_end`, `group_by` |
| PATCH | `/api/v1/invoices/{id}` | Edit a draft |
| POST | `/api/v1/invoices/{id}/rebuild` | Refresh a draft from time |
| POST/PATCH/DELETE | `/api/v1/invoices/{id}/lines[/{line_id}]` | Manual lines; reword or remove any line |
| POST | `/api/v1/invoices/{id}/issue` · `/paid` · `/unpaid` · `/void` | Lifecycle |
| DELETE | `/api/v1/invoices/{id}` | Delete a draft, or a recorded invoice |
| GET | `/api/v1/fixed-fees` | Fixed-fee work with invoiced, paid, outstanding, overdue, to invoice (`open=true`: not settled) |
| POST | `/api/v1/scopes/{id}/invoice` | Draft an invoice for part (`amount`) or all of a fixed fee |
| POST | `/api/v1/invoices/external` | Record an invoice sent elsewhere: `scope_id` or `client_id`, `number`, `issue_date`, `amount`, `status` (`sent`/`paid`) |
| PATCH | `/api/v1/invoices/{id}/external` | Fix a recorded invoice's number, dates or amount |
| POST/DELETE | `/api/v1/invoices/{id}/file` | Attach (multipart `file`, PDF ≤ 20 MB) or remove the PDF you sent |
| GET | `/api/v1/invoices/{id}/pdf` · `/html` | The document |
| GET/POST | `/api/v1/projects/{id}/scopes` | Scopes with burn, and `billing` for fixed fees |
| GET/PATCH/DELETE | `/api/v1/scopes/{id}` | One scope |
