# Local agent (tahgul-mcp)

`tahgul-mcp` exposes Tahgul as MCP tools so a local model with tool calling can manage tasks, timers and time by conversation. Nothing leaves the homelab: the model runs locally, and the server talks to Tahgul's REST API over the tailnet with its own token.

## Tools

| Tool | Does |
| --- | --- |
| `quick` | One sentence → task, timer start/stop, logged time, idea or a quick answer, with a line to relay |
| `create_task` | Add a task to a project (by project or client name) |
| `list_tasks` | Find tasks (open by default, soonest due first) |
| `update_task` | Move, tick off, rename, re-date or comment on a task (`#12`) |
| `start_timer` / `stop_timer` / `timer_status` | The running timer |
| `log_time` | Log time after the fact |
| `summary` | Hours and money by client and project, with budget left |
| `create_event` / `list_events` | The calendar (events land on the phone right away) |
| `events_to_log` / `log_event_as_time` | "Log this meeting as time?" |
| `capture_idea` | Save to the ideas inbox |
| `voice_notes` | Read voice notes and transcripts on an idea or project, or search them ("pull the to-dos out of this idea") |
| `unbilled_work` / `draft_invoice` | "Who should I bill?" and "draft an invoice for Acme". Only with a token that also has the **invoice** scope; drafts are never issued by the agent |

There are no delete or rate tools, and the agent's token should have **read** and **write** only (add **invoice** if you want it drafting invoices). Agent writes show in each task's history as made by the token's name.

When a name is ambiguous, tools return `needs_confirmation` with candidates. The server's instructions tell the model to ask you rather than pick.

## Setup

1. In Tahgul, Settings → API tokens: create `local agent` with **read** and **write**.
2. On the machine running the model:

    ```sh
    pip install ./mcp            # from this repo
    export TAHGUL_URL=https://tahgul.<tailnet>.ts.net
    export TAHGUL_TOKEN=thg_…
    tahgul-mcp                   # stdio, for clients that launch servers
    tahgul-mcp --http --port 8765  # or streamable HTTP at http://127.0.0.1:8765/mcp
    ```

3. Point your MCP client at it. Most clients that launch stdio servers take a config like:

    ```json
    {
      "mcpServers": {
        "tahgul": {
          "command": "tahgul-mcp",
          "env": { "TAHGUL_URL": "https://tahgul.<tailnet>.ts.net", "TAHGUL_TOKEN": "thg_…" }
        }
      }
    }
    ```

Pair it with a local model that handles tool calls well (via Ollama or llama.cpp). Smaller models do better with `quick`, which needs only one string argument.

## Siri through the agent (later)

Today the Siri Shortcuts call `/quick` directly (see [siri-shortcuts.md](siri-shortcuts.md)). Later the same Shortcut can send the dictated text to the agent instead, which picks the right tool, without changing anything on Tahgul's side.
