# Decisions

Answers to the open questions in [SPEC.md](SPEC.md), newest first.

| Date | Question | Decision |
| --- | --- | --- |
| 2026-10-21 | Whisper service | **speaches** (`ghcr.io/speaches-ai/speaches:latest-cpu`, faster-whisper) as an optional compose profile, called through the OpenAI transcription API so any compatible server can replace it. Default model `faster-whisper-small`. Tahgul asks speaches to download the model on first use. |
| 2026-10-21 | Voice note with no target | Becomes a new idea in the inbox (titled from the note, or the time), so `curl -F file=@memo.m4a` just works. |
| 2026-10-21 | Deleting work with audio | Deleting a task moves its notes to the project; deleting a project leaves its notes "unfiled" in the ideas inbox. Only deleting a note deletes audio, and that needs the owner or an `admin` token. |
| 2026-10-21 | Audio type check | By the file's first bytes (m4a/mp4, mp3, wav, webm, ogg), not the name or declared type. Durations are read with `mutagen` when Yog doesn't send one. |
| 2026-10-21 | Invoice lifecycle | Numbers are assigned when a draft is **issued** (never at draft time), so deleted drafts leave no gaps. Issued invoices are voided, not deleted; a void keeps its number. "Sent" means issued: Tahgul makes the PDF and you email it, so nothing goes to a client automatically. |
| 2026-10-21 | Rounding on invoices | Applied per entry when lines are built, then summed. Logged time is never changed. |
| 2026-10-21 | Retainers | One line per calendar month in the invoice period (between the scope's start and end), billed once per month even with no time. Hours over the monthly allowance are billed at the scope rate, else the entry's rate. |
| 2026-10-21 | Voiding | Needs the `admin` token scope (or the owner), like deletes. Drafting and issuing need `invoice`. |
| 2026-10-21 | Calendar home | **Self-hosted Radicale** in the stack, as the spec recommends, reached by the phone over Tailscale on port 8443. iCloud stays available as an account type; the sync code is the same. |
| 2026-10-21 | CalDAV client | A small client of our own (`api/tahgul/caldav.py`, ~250 lines over httpx) instead of the `caldav` package the spec names. Two-way sync depends on exact control of `If-Match`/`If-None-Match`, sync-collection tokens and 412 handling, and the `caldav` 3.x API was in flux. Tested against a real Radicale server in CI. |
| 2026-10-21 | Event times | Tahgul writes event times in UTC (no VTIMEZONE needed). Floating times from other apps are read in the workspace time zone. Repeating events are read-only in Tahgul. |
| 2026-10-21 | Agent limits | Tokens get a fourth scope, `admin`, needed to delete records or change rates. Read / write / invoice tokens can't do either (SPEC: security, agent limits). |
| 2026-10-21 | Starting workspace | New installs get a "Consulting" workspace with the defaults below; more can be added under Settings. |
| 2026-10-20 | App name | **Tahgul.** The spec still says "Ledger (working name)"; the code, env vars (`TAHGUL_*`), token prefix (`thg_`) and UI use Tahgul. |
| 2026-10-20 | Currency | **USD** as the default for new workspaces; each workspace can change it. |
| 2026-10-20 | Tax / VAT | **Off by default.** A workspace or client can set a tax rate later; blank means no tax line. Revisit before invoicing (milestone 7). Whether consulting needs a tax line (Illinois, and any later move) is a question for the CPA. |
| 2026-10-20 | Time zone | **America/Chicago** as the default for new workspaces (owner is in Illinois). |
| 2026-10-20 | Invoice number format | **Pick per workspace from presets**, default `{Y}-{seq:3}` (2026-001, resets yearly). Others: `INV-{seq:4}` (continuous), `{Y}{M}-{seq:3}` (resets monthly), `{CLIENT}-{Y}-{seq:3}` (per client, per year). Custom patterns allowed. |
