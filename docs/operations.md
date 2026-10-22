# Running Tahgul

On Hive next to Yog, start with [hive.md](hive.md) (one script: secrets, stack, login, `tailscale serve`). Everything below applies there too; the addresses are `https://hive.<tailnet>.ts.net:8443` (app) and `:8444` (calendars) instead of `tahgul.<tailnet>.ts.net`.

## Demo data

On an empty install, fill a few weeks of believable work (clients, projects, scopes, tasks, time, ideas and one issued invoice) to try things out:

```sh
docker compose exec api tahgul seed-demo
```

It refuses when clients already exist (`--force` adds it anyway). It goes through the real API, so everything shows in the audit log as "demo seed".

## Backups

`deploy/backup.sh` writes a folder per run with:

| File | What |
| --- | --- |
| `db.dump` | `pg_dump` of the database (custom format), taken while the app runs |
| `data.tar.gz` | Invoice PDFs and voice notes (`/data`) |
| `radicale.tar.gz` | Calendars |
| `env` | A copy of `deploy/.env`, including `TAHGUL_SECRET_KEY` (needed to read saved calendar passwords). Keep backups private |
| `SHA256SUMS` | Checked before any restore |

Run it nightly from cron on the homelab, into the NAS, keeping 14 days locally:

```cron
30 2 * * * cd /opt/tahgul && BACKUP_DIR=/mnt/nas/tahgul-backups deploy/backup.sh >> /var/log/tahgul-backup.log 2>&1
```

For the off-site copy, install [restic](https://restic.net), create a repository (another disk, a friend's box, B2/S3) and add `RESTIC_REPOSITORY=…` and `RESTIC_PASSWORD_FILE=…` to that cron line. Each backup is then also sent there encrypted, keeping 7 daily, 4 weekly and 12 monthly snapshots.

## Restore

```sh
deploy/restore.sh /mnt/nas/tahgul-backups/2026-10-21_023000
```

It checks the checksums, asks you to type `restore`, stops the app, replaces the database, files and calendars, and starts it again (migrations run on start, so an older backup is upgraded). On a new machine: clone the repo, copy the backup's `env` to `deploy/.env`, `docker compose up -d --build` once, then restore.

CI runs a backup → change → restore round trip on every push. Do one by hand on the homelab before relying on it.

## On your phone

Tahgul is an installable web app. Open `https://tahgul.<tailnet>.ts.net` in Safari, tap **Share → Add to Home Screen**, and it opens full screen like a native app, with Today, Tasks, Time and Calendar in a bottom tab bar and everything else under **More**. On Android/Chrome use **Install app**. Coming back to it refreshes what's on screen. If Tailscale is off, it shows a "Can't reach Tahgul" page instead of a browser error.

It must be opened over HTTPS (the Tailscale URL) for installing and for in-browser recording to work.

### Linking from a landing page

A public page (say, a Vercel site listing your apps) can link to Tahgul's tailnet address, or to the gate page at `tahgul.cobars.space` ([hive.md](hive.md#tahgulcobarsspace-gate-page)). The gate forwards devices on the tailnet and shows "No entry" to everyone else. Tahgul itself never goes on the public internet. Don't proxy or rewrite Tahgul through the public site, or the login cookie and the no-public-exposure rule both break.

## Updating

```sh
git pull
docker compose up -d --build
```

Migrations run when the API starts. Take a backup first.

## Smoke tests

Browser tests that click through login, the timer, a board, reports, the calendar, an invoice (issue + PDF) and a voice note, against a running stack:

```sh
pip install -e "api[dev]" playwright && python -m playwright install chromium
TAHGUL_SMOKE_URL=http://localhost:8000 TAHGUL_SMOKE_EMAIL=you@example.com \
  TAHGUL_SMOKE_PASSWORD=… pytest tests/smoke
```

They create data (a timer entry, a task, an issued invoice, an idea), so point them at a demo install, not the real one. CI runs them on demo data.
