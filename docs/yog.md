# Tahgul and Yog

Yog records meetings and transcribes them on your own machines. Tahgul tracks the business side: clients, projects, time, tasks and invoices. Both run on Hive ([hive.md](hive.md)) and are kept apart on purpose:

- **Each works alone.** Neither app needs the other to start, to sign in, or to do its main job. If Yog is down, Tahgul loses nothing. If Tahgul is down, Yog just keeps (or retries) what it would have sent.
- **They talk only over public APIs:** HTTP with a token, webhooks and MCP. Neither reads the other's database, files or code, and neither has to ship in step with the other.
- **Every link is optional and idempotent.** Each side stores the other's id (for example `yog_id`), so a retry or a re-send never makes a duplicate.

**Status: notes only.** Nothing below is built yet beyond what Tahgul already had, which is marked *exists*. How the two apps should connect will be decided later.

## What exists today (Tahgul side)

| Piece | What it gives Yog |
| --- | --- |
| `POST /api/v1/voice-notes` *(exists)* | Upload audio plus metadata. It takes `yog_id` (sending the same one twice returns the same note), `recorded_at`, `duration_seconds`, `title`, `transcript`, and a target: `project_id`, `project` (by name), `task_id`, or `new_idea`. See [voice-notes.md](voice-notes.md). |
| Tokens with scopes *(exists)* | A `write` token for Yog (Settings → Tokens). It can't delete, change rates or see invoices. |
| Webhooks *(exists)* | `voice_note.created` and `voice_note.transcribed`, plus task, time and invoice events, all HMAC-signed. Yog could listen for any of them. See [webhooks.md](webhooks.md). |
| MCP *(exists)* | `voice_notes` (find a note's transcript), `create_task`, `log_time` and `quick`. An agent holding a meeting summary can already turn its action items into tasks by hand. |
| Projects and tasks lookup *(exists)* | `GET /projects?q=` and `GET /tasks/by-number/{n}` let Yog offer "file this meeting under…". |

## Candidate integration points (not built)

Grouped by direction. Each one should degrade to "nothing happens" when the other app isn't there.

### Yog → Tahgul

1. **A meeting becomes a voice note on a project.** Yog already has everything the upload API takes. The only open questions are when it sends (after transcription? after the summary?) and which project it picks (a picker in Yog fed by `GET /projects`, or a per-calendar-series default).
2. **Meeting summary and action items.** Tahgul's notes have a transcript but no summary field. Options:
   - add `summary` (markdown) and `source_url` (a link back to the meeting in Yog) to voice notes;
   - or let Yog post the summary as a comment on a task.
   Action items could become tasks (`POST /tasks`, with `source: yog` and the meeting link) or stay suggestions until accepted in Tahgul.
3. **Meeting time as billable time.** A client call recorded in Yog could become a time entry with the meeting's start, end and project. Tahgul's calendar already turns events into time ("events to log"). A Yog meeting could feed the same suggestion list rather than log time directly, so nothing is billed without a click.
4. **Diarized speakers → client contacts.** A later idea, once Yog's speaker memory exists.

### Tahgul → Yog

1. **Context for a meeting.** Yog could ask Tahgul which client or project a calendar event belongs to, using the read-only `GET /events` with `project_name`, and tag the meeting with it.
2. **Webhooks.** Yog could subscribe to `task.*` to show open tasks for the client during a call. This is low priority.

### Both

- **Identity:** keep separate logins. If Yog adopts sign-in via Tailscale identity headers, Tahgul could too, but each app would still keep its own sessions.
- **Links:** each side stores the other's URL (`source_url` / `tahgul_url`) and opens it in a new tab. They never embed each other.
- **The gate / landing page:** `yog.cobars.space` and `tahgul.cobars.space` stay separate entries on the landing page.

## Things to settle before building

- Which direction starts the link: does Yog push to Tahgul, or does Tahgul pull from Yog?
- Is one meeting one voice note, or one note per speaker track?
- Should the summary live in Tahgul, or only a link back to Yog?
- Should action items create tasks automatically, or only after you accept them?
- Should meeting time be billable automatically, or only suggested?
- Mom's Yog account should never send anything to Tahgul. Integration would be per Yog user, and only for the admin.
