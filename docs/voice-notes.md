# Ideas and voice notes

## Ideas inbox

**Ideas** in the top bar. An idea is a project in status `idea`, so it can have tasks and voice notes from the start and become real work without moving anything.

- Type an idea, or upload / record audio straight into the inbox: a recording with no other target becomes a new idea named after its title (or the time).
- Open an idea to play its notes, read transcripts and add more.
- **Make it a project** turns it into an active project (optionally for a client). Its notes and tasks stay put.

Voice notes also live on projects (the **Notes** tab) and tasks (in the task drawer).

## Uploading (Yog, Shortcuts, curl)

`POST /api/v1/voice-notes` as `multipart/form-data`, with a token that has the `write` scope:

| Field | |
| --- | --- |
| `file` | m4a, mp3, wav, webm or ogg, up to 200 MB. The type is checked from the file's bytes, not its name. |
| `metadata` | Optional JSON, below |

```json
{
  "yog_id": "a1b2c3",               // Yog's id: the same note sent twice is stored once
  "recorded_at": "2026-10-20T07:42:00-05:00",
  "duration_seconds": 95.0,          // optional; read from the file when missing (not for WebM)
  "title": "Drone surveys",
  "transcript": "…",                 // optional, if Yog already transcribed it
  "project_id": "…",                 // one target, or none for a new idea:
  "task_id": "…",
  "project": "network audit",        //   a project by name (ambiguous → 409 needs_confirmation)
  "new_idea": {"title": "Drone surveys", "description": "…"}
}
```

The response (201, or 200 with `"duplicate": true` for a repeated `yog_id`) has the note's `id`, `playback_url`, `download_url` and the project/task it landed on.

```sh
curl -H "Authorization: Bearer $TAHGUL_TOKEN" \
  -F file=@memo.m4a \
  -F 'metadata={"title": "Drone surveys", "recorded_at": "2026-10-20T07:42:00"}' \
  https://tahgul.<tailnet>.ts.net/api/v1/voice-notes
```

Times without an offset are read in the workspace time zone.

## Playback

`GET /api/v1/voice-notes/{id}/audio` serves the file with HTTP range requests, so long notes seek instantly; `?download=true` downloads it with its title as the file name. The web player has 1× / 1.5× / 2×.

## Storage

Files are written under `TAHGUL_DATA_DIR` (the `tahgul-data` volume, `/data` in the container) as `audio/{yyyy}/{mm}/{uuid}.{ext}`; the database keeps the path, size, SHA-256 and metadata. To keep audio on the NAS, mount it at `/data` (or a folder inside it) in `deploy/docker-compose.yml`:

```yaml
    volumes:
      - /mnt/nas/tahgul:/data
```

Deleting a task or project never deletes audio: a task's notes move up to its project, and a project's notes show under **Unfiled notes** in the ideas inbox. Deleting a voice note itself (owner, or a token with `admin`) removes the file.

## API

| Method | Path | |
| --- | --- | --- |
| POST | `/api/v1/voice-notes` | Upload |
| GET | `/api/v1/voice-notes` | List: `project_id`, `task_id`, `unfiled`, `q` (title and transcript search) |
| GET/PATCH/DELETE | `/api/v1/voice-notes/{id}` | Rename, edit transcript, move to another project/task |
| GET | `/api/v1/voice-notes/{id}/audio` | The audio |
| GET/POST | `/api/v1/ideas` | The inbox (with note and to-do counts); create, optionally moving an existing note onto it with `voice_note_id` |
| POST | `/api/v1/ideas/{id}/promote` | Make it a project |

The MCP server's `voice_notes` tool reads notes and transcripts, so a local agent can "pull the to-dos out of this idea" and create tasks from them.
