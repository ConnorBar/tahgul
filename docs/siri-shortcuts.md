# Siri Shortcuts

Three Shortcuts drive Tahgul by voice: **Start timer**, **Stop timer** and **New task**. They call the REST API over Tailscale, so they work anywhere your phone is on the tailnet. No agent or model is involved; Tahgul's `/quick` endpoint does the understanding and hands back a sentence for Siri to say.

## Before you start

1. **Make a token.** In Tahgul, go to Settings → API tokens. Name it `Siri`, tick **read** and **write** only, and copy the token (it's shown once).
2. **Know your address.** It's `https://tahgul.<your-tailnet>.ts.net` once the Tailscale sidecar is running (see the README). Below, `TAHGUL` stands for that address.
3. **Keep Tailscale on** on the iPhone, or the Shortcuts will time out.

Every request below sends two headers:

| Header | Value |
| --- | --- |
| `Authorization` | `Bearer thg_…` (your token) |
| `Idempotency-Key` | something unique per run, so a retry never creates two tasks |

For the key, add a **Current Date** action, then **Format Date** (ISO 8601, include time), then a **Random Number** (1 to 1,000,000), and combine them in a **Text** action like `siri-[Formatted Date]-[Random Number]`. Use that Text as the header value.

## New task ("Hey Siri, new task")

This is the milestone's done-when: saying it creates a task on the board.

1. **Dictate Text** (stop listening: *After Pause*).
2. **Text** action (the idempotency key, as above).
3. **Get Contents of URL**
    - URL: `TAHGUL/api/v1/quick`
    - Method: **POST**
    - Headers: `Authorization`, `Idempotency-Key` (from step 2)
    - Request Body: **JSON**, one field `text` = *Dictated Text*
4. **Get Dictionary Value**: key `speech` from *Contents of URL*.
5. **Speak Text**: *Dictionary Value*.

Name the Shortcut **New task**. Things you can say:

- "Call Laura for Acme Thursday at 2pm" → a task on Acme's project due Thursday at 2 PM
- "Fix VPN for Acme tomorrow, high priority" → due tomorrow, high priority
- Naming the project with "for *project*" is the most reliable way by voice; dictation turns "hashtag Acme" into `#Acme` only some of the time
- "Log two hours on Acme yesterday afternoon" → time logged instead of a task
- "Idea: drone surveys for the co-op" → saved to the ideas inbox

**When Tahgul isn't sure** (say, a client with two projects), it answers with a question like "Which project did you mean: Yield dashboard, Grain bin sensors?" and changes nothing. To handle that in the Shortcut, add after step 3:

- **If** *Dictionary Value* for key `needs_confirmation` **is** *true*:
    - **Get Dictionary Value** `candidates` → **Repeat with Each** → **Get Dictionary Value** `name` (collect into a list), then **Choose from List**.
    - **Get Contents of URL** again with body `text` = *Dictated Text* and `project` = *Chosen Item* (and a new idempotency key).

## Start timer ("Hey Siri, start timer")

1. **Ask for Input** (Text): "For which project?" (leave the default empty).
2. **Text** action (idempotency key).
3. **Get Contents of URL**: POST `TAHGUL/api/v1/quick`, same headers, JSON body `text` = `start timer for [Provided Input]`.
4. **Get Dictionary Value** `speech`, then **Speak Text**.

Answering "Acme" starts a timer on Acme's project; an empty answer starts a timer with no project, which you can assign later in the app.

## Stop timer ("Hey Siri, stop timer")

1. **Text** action (idempotency key).
2. **Get Contents of URL**: POST `TAHGUL/api/v1/quick`, JSON body `text` = `stop`.
3. **Get Dictionary Value** `speech`, then **Speak Text** ("Stopped. 1 hour 25 minutes on Network audit.").

## Other things `/quick` answers

These read only, so no idempotency key is needed:

- "What's running" → "25 minutes on Network audit."
- "How many hours this week" → "This week: 12.5 hours, $1,450 billable."

## Troubleshooting

| Siri says | Meaning |
| --- | --- |
| *(nothing; the Shortcut times out)* | The phone can't reach Tahgul. Check Tailscale is connected. |
| A 401 error | The token is wrong or revoked. Make a new one. |
| A 403 mentioning `admin` | The Shortcut tried something only an admin token can do (deleting, changing rates). That's deliberate. |
| "Which project should … go in?" | No project was named. Say "for *project*" or answer the follow-up. |

You can try any sentence without changing anything by adding `"dry_run": true` to the JSON body; Tahgul answers with "I'd …" instead of doing it.
