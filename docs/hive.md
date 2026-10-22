# Running Tahgul on Hive, next to Yog

Hive (the Ser 5 Pro mini PC) already runs Yog. Tahgul uses the same deployment pattern, on its own ports:

```
            Docker Compose (127.0.0.1 only)        host `tailscale serve`         who can reach it
Yog         api        127.0.0.1:8780        →  https://hive.<tailnet>.ts.net         you + family (tag:yog:443)
Tahgul      api        127.0.0.1:8790        →  https://hive.<tailnet>.ts.net:8443    you only
            radicale   127.0.0.1:8791        →  https://hive.<tailnet>.ts.net:8444    you only (iPhone calendars)
            reports    127.0.0.1:8792        →  (optional) Funnel :10000              anyone, report pages only
```

The two apps share the machine and nothing else. They have separate Compose projects, databases, volumes and logins, and neither needs the other running.

## Install

On Hive:

```sh
git clone git@github.com:ConnorBar/tahgul.git ~/w/dev/tahgul
cd ~/w/dev/tahgul
deploy/hive/setup.sh
```

The script:

1. **Writes `deploy/.env`** with fresh secrets (`openssl rand -hex …`, like Yog's) and the Hive ports. It never overwrites an existing file. It prints the calendar login for the iPhone once; the login is also saved in `deploy/.env`.
2. **Builds and starts the stack**: `docker compose up -d --build --wait`. Migrations run on start.
3. **Creates your login** on first install. It asks for your email, then for a password (12+ characters). The password is entered in the container and never written to a file.
4. **Publishes it on the tailnet**:
   ```sh
   sudo tailscale serve --bg --https=8443 http://127.0.0.1:8790
   sudo tailscale serve --bg --https=8444 http://127.0.0.1:8791
   ```
   Yog's `tailscale serve --bg 8780` on 443 is left as it is. `tailscale serve status` shows all three.

Then open `https://hive.<tailnet>.ts.net:8443`, sign in, and turn on two-factor under Settings. To install it on the iPhone, use Share → Add to Home Screen.

Other options:

- Run `deploy/hive/setup.sh --no-serve` to leave Tailscale alone.
- Run `deploy/hive/setup.sh --off` to stop publishing. The stack keeps running.
- If a port is taken, override it with `TAHGUL_PORT`, `RADICALE_PORT`, `TAHGUL_HTTPS_PORT` or `RADICALE_HTTPS_PORT`.

### iPhone calendars

Settings → Calendar → Accounts → Add Account → Other → Add CalDAV Account. Use server `hive.<tailnet>.ts.net:8444` and the `RADICALE_USER` / `RADICALE_PASSWORD` from `deploy/.env`. Tailscale must be on. See [calendar.md](calendar.md).

### Reminders links

`TAHGUL_APP_URL` is set to `https://hive.<tailnet>.ts.net:8443`, so each reminder links back to its task.

## Updating

```sh
cd ~/w/dev/tahgul && git pull && deploy/hive/setup.sh
```

This rebuilds, migrates and re-applies `tailscale serve`, all of which is safe to repeat. Take a backup first ([operations.md](operations.md)). The nightly cron backup works the same on Hive:

```cron
30 2 * * * cd ~/w/dev/tahgul && BACKUP_DIR=/mnt/nas/tahgul-backups deploy/backup.sh >> ~/tahgul-backup.log 2>&1
```

Schedule it at a different minute from Yog's backup if Yog has one.

## Access control (keep family out)

Hive is tagged `tag:yog`. The family rule in Yog's policy allows only `tag:yog:443`, which already keeps them away from Tahgul's 8443 and 8444. **Don't widen that rule to `tag:yog:*`.** Your own rule needs to cover 8443 and 8444 too (or `*`).

Add tests to the tailnet policy so Tailscale refuses any later edit that would expose Tahgul. Use real family logins, since tests take users, not groups:

```hujson
"tests": [
  {
    "src":    "mom@example.com",
    "accept": ["tag:yog:443"],
    "deny":   ["tag:yog:8443", "tag:yog:8444", "tag:yog:8790", "tag:yog:8791"],
  },
  {
    "src":    "you@example.com",
    "accept": ["tag:yog:443", "tag:yog:8443", "tag:yog:8444"],
  },
],
```

The 8790/8791 lines also check that nobody reaches the raw Docker ports. Those ports are bound to 127.0.0.1 anyway.

### Same machine, different ports: what that means

The browser treats `hive…:443` (Yog) and `hive…:8443` (Tahgul) as separate apps. Each has its own home-screen install, offline page and storage.

Cookies are the exception, because they ignore ports. Tahgul's `tahgul_session` cookie is also sent to Yog, and Yog's cookie is also sent to Tahgul. Both are HttpOnly and only you use both apps, so neither app can read the other's cookie in page scripts and each ignores the other's. If you ever want full separation, run Tahgul as its own tailnet machine instead: use the sidecar (`docker compose --profile tailscale`, see the README). It gets `https://tahgul.<tailnet>.ts.net` and needs no `tailscale serve` on the host.

## tahgul.cobars.space (gate page)

Like `yog.cobars.space`, a static page on Vercel checks whether the device can reach Tahgul over Tailscale. If it can, it forwards there, keeping the path, so `tahgul.cobars.space/invoices` opens Invoices. If it can't, it shows "No entry" with a link to get Tailscale. Tahgul itself is never proxied through Vercel or Cloudflare.

The page is in `deploy/gate/`: one HTML file, the icon, and `vercel.json` with `noindex` and a strict CSP. To set it up:

1. In Vercel, create a new project from this repo with **Root Directory** `deploy/gate`, no framework, and no build command.
2. Add the domain `tahgul.cobars.space`. In Cloudflare, add a `CNAME tahgul → cname.vercel-dns.com`, DNS only (grey cloud).
3. If the tailnet name or port changes, edit `TARGET` at the bottom of `deploy/gate/index.html`.

The landing page that lists all your apps can link to `tahgul.cobars.space` the same way it links to Yog.

## Client report links on the internet (optional)

Only the separate `reports` container, which serves `/r/<token>` pages and nothing else, goes on the internet through Funnel. The app stays private.

```sh
# deploy/.env
TAHGUL_PUBLIC_REPORT_URL=https://hive.<tailnet>.ts.net:10000

docker compose -f deploy/docker-compose.yml --profile public-reports up -d
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:8792
```

Funnel must be allowed for Hive in the tailnet policy (`nodeAttrs` with `funnel`). Only ports 443, 8443 and 10000 can be funneled, and 443 is Yog's, so reports take 10000. See [client-reports.md](client-reports.md).
