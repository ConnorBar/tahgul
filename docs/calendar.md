# Calendar sync

Tahgul syncs both ways with CalDAV, the standard the iPhone's Calendar app already speaks. Events made on the phone show in Tahgul, and events made in Tahgul (or by the agent) show on the phone.

## Self-hosted with Radicale (the default)

The Docker stack runs [Radicale](https://radicale.org), a small CalDAV server. Nothing leaves the homelab.

1. **Set the login** in `deploy/.env`: `RADICALE_USER`, `RADICALE_PASSWORD`, plus a `TAHGUL_SECRET_KEY` (Tahgul stores the Radicale password encrypted with it).
2. **Start the stack** with the Tailscale profile (see the README). On first start Tahgul connects to Radicale by itself, creates a **Work** calendar, and makes it the default for new events.
3. **Add it to the iPhone:** in Settings, open Calendar's settings (under **Apps** on recent iOS), then Calendar Accounts → Add Account → Other → **Add CalDAV Account**.
    - Server: `tahgul.<your-tailnet>.ts.net:8443`
    - User name and password: the `RADICALE_USER` and `RADICALE_PASSWORD` from step 1
    - Tailscale must be on for the phone to reach it. iOS needs HTTPS, which `tailscale serve` provides with a real certificate.
4. On a Mac: Calendar → Settings → Accounts → + → Other CalDAV Account → Manual, with the same details.

To show what you billed on the phone, open **Settings → Calendars** in Tahgul and choose **Create a Billable calendar**. Every time entry is then mirrored there as an event, with hours and amount in the notes. It appears on the phone like any other calendar.

## iCloud instead

If you'd rather keep your iCloud calendars, choose **Settings → Calendars → Connect a calendar → iCloud**. Use your Apple ID email and an **app-specific password** (account.apple.com → Sign-In and Security); your normal password won't work. Events then live on Apple's servers, so this is less private than Radicale. The same sync code handles both.

## How sync works

| | |
| --- | --- |
| **Pull** | Every 3 minutes (`TAHGUL_SYNC_MINUTES`) and on **Sync now**, asking only for changes since last time (CalDAV sync tokens). |
| **Push** | Immediately when you or the agent create, move or delete an event. If the server can't be reached, the change waits and goes out on the next sync (the event shows a dashed outline meanwhile). |
| **No silent overwrites** | Every write carries the event's last-known version (`If-Match` with the etag). If the phone changed it in between, the server refuses and Tahgul compares. |
| **Conflicts** | When both sides changed, the newest change wins. The losing version is kept in the audit log as a `conflict` entry by `sync`. |
| **Read-only calendars** | Newly found calendars start read-only in Tahgul. Mark the ones Tahgul may edit under Settings → Calendars; the rest (say, a client's shared calendar) are shown and never changed. |
| **Repeating events** | Shown with every occurrence. Edit or delete them on the phone; Tahgul doesn't rewrite repeat rules. |

## Linking events to work

- Link an event to a project from the calendar page. Linking is Tahgul-only and allowed even on read-only calendars.
- **Log as time:** one click turns an event into a time entry ("I was in that meeting, bill it"). The agent tool is `log_event_as_time`.
- **"Log this as time?"** When a linked event ends without time logged, Tahgul asks on the Today and Calendar pages. The agent can ask through `events_to_log`.
- **Task time blocks:** a task with a due date and time can be put on the calendar from its drawer, as long as its estimate (default an hour). Changing the task's due time moves the block.

## Tasks in Reminders

**Settings → Calendars → Reminders → Add to Reminders** makes a to-do list called "Tahgul" on the calendar server (or links an existing one, e.g. an iCloud Reminders list). On the iPhone it shows in the **Reminders** app under the same CalDAV account as your calendars (Settings → Apps → Reminders → Accounts, if it's switched off).

| In Tahgul | On the phone |
| --- | --- |
| An open task with a due date (or every open task, if you choose) | A reminder with its title, due date/time, priority (urgent/high → !!!), and `#12 · Project` in the notes |
| Finishing a task | It's ticked off |
| Changing title, due date or priority | Updated within seconds |
| Deleting a task, or clearing its due date on a "due dates" list | Removed |

| On the phone | In Tahgul |
| --- | --- |
| Tick a reminder off | The task moves to its first done column (and back if you untick it) |
| Edit the title or due date | The task changes |
| Add a reminder to the Tahgul list | A new task in the project you chose under "New reminders from the phone go to" (or left alone if none) |
| Delete a reminder | The task stays; the reminder comes back only if the task changes in Tahgul |

Changes in Tahgul are pushed a few seconds after you make them; the phone's changes are pulled with the calendar sync (every few minutes) or **Sync now**. If both sides changed the same task, the newer change wins and the other is kept in the task's history. Set `TAHGUL_APP_URL=https://tahgul.<tailnet>.ts.net` to put a link to the task in each reminder's notes.

