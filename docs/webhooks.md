# Webhooks

Tahgul can POST to a URL when things happen, so a local agent or a script can react: "the Acme sync just ended, log it?", "that timer ran three hours, add a note?", "Call Laura is due in an hour".

Set them up under **Settings → Webhooks** (owner, or a token with the `admin` scope via the API). Each webhook has a URL, the events it wants (or everything), and a signing secret shown once.

## Events

| Event | When | `data` |
| --- | --- | --- |
| `timer.started` | The timer starts | time entry |
| `timer.stopped` | The timer stops | the finished time entry |
| `time_entry.created` | Time logged by hand, from a meeting or by the agent | time entry |
| `task.created` | A task is added | task |
| `task.completed` | A task moves into a done column | task |
| `task.due_soon` | Within the hour before a task is due (date-only tasks: 9:00 that day), once | task + `due_at` |
| `calendar.event_ended` | A meeting linked to a project ended in the last 30 minutes and has no time logged, once | event + `log_url` |
| `invoice.issued` / `invoice.paid` / `invoice.voided` | Invoice lifecycle | invoice |
| `idea.created` / `idea.promoted` | Ideas inbox | project |
| `voice_note.created` / `voice_note.transcribed` | Audio arrives / Whisper finishes | voice note (with transcript) |
| `ping` | **Send test** | — |

Events are written in the same database transaction as the change, so nothing is announced for work that didn't happen, and nothing is lost if Tahgul restarts before sending.

## The request

```http
POST /tahgul HTTP/1.1
Content-Type: application/json
X-Tahgul-Event: timer.stopped
X-Tahgul-Delivery: 0d0b6d2e-…          # same on every retry: use it to de-duplicate
X-Tahgul-Timestamp: 1761028826
X-Tahgul-Signature: sha256=5f1c…

{"id": "0d0b6d2e-…", "event": "timer.stopped", "created_at": "2026-10-21T06:40:29Z",
 "data": {"id": "…", "project_name": "Network audit", "client_name": "Acme Farms",
          "start_at": "…", "end_at": "…", "duration_seconds": 10800, "hours": "3.00",
          "notes": "Firewall review", "billable": true, "amount": "375.00", "source": "timer"}}
```

Reply with any 2xx within 10 seconds. Anything else (or no answer) is retried after 1 min, 5 min, 30 min, 2 h and 6 h, then marked failed; **Resend** in the log sends it again by hand.

## Checking the signature

The signature is an HMAC-SHA256 of `"<timestamp>.<raw body>"` with the webhook's secret. Check it, and reject old timestamps:

```python
import hashlib, hmac, time

def verify(secret: str, headers, body: bytes) -> bool:
    ts = headers["X-Tahgul-Timestamp"]
    if abs(time.time() - int(ts)) > 300:
        return False
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"sha256={mac}", headers["X-Tahgul-Signature"])
```

```js
import crypto from "node:crypto";
const verify = (secret, headers, rawBody) => {
  const ts = headers["x-tahgul-timestamp"];
  const mac = crypto.createHmac("sha256", secret).update(`${ts}.`).update(rawBody).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(`sha256=${mac}`), Buffer.from(headers["x-tahgul-signature"]));
};
```

## With the local agent

A typical setup: the agent box runs a small HTTP endpoint, Tahgul posts `calendar.event_ended` and `task.due_soon` to it, and the agent asks you (notification, chat) and acts through the MCP tools, e.g. `log_event_as_time` with the event id from the payload.

## API

| Method | Path | |
| --- | --- | --- |
| GET | `/api/v1/webhooks/events` | Event names and descriptions |
| GET/POST | `/api/v1/webhooks` | List / create (returns the secret once) |
| PATCH/DELETE | `/api/v1/webhooks/{id}` | Change URL, events, on/off; delete |
| POST | `/api/v1/webhooks/{id}/rotate-secret` | New secret (returned once) |
| POST | `/api/v1/webhooks/{id}/test` | Send a `ping` now and report the result |
| GET | `/api/v1/webhooks/{id}/deliveries` | Recent deliveries (kept 30 days) |
| POST | `/api/v1/webhook-deliveries/{id}/retry` | Send one again |
