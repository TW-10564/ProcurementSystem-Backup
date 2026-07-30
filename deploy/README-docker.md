# DGX deployment (Docker + PostgreSQL)

The container path. `README.md` in this folder documents the original
**systemd** deployment — keep it: it is the standing rollback if the
container stack has to be abandoned mid-cutover.

```
Browser ──HTTPS──> nginx (DGX host)  ──> 127.0.0.1:8600 ──> [app] FastAPI + SPA
                                                              │
                                                        [db] postgres:17
   /mnt/nas/one  (cifs, ro, systemd .mount) ──bind ro──────────┘
```

---

## 0. Prerequisites

```bash
sudo apt-get install -y docker.io docker-compose-v2 cifs-utils nginx
nc -vz 172.25.20.11 445          # must succeed, or the NAS is not reachable
hostname -f                      # must return an FQDN (needed for TLS + Entra)
```

`hostname -f` returning a name with no dots means there is no DNS entry yet —
request one before continuing. TLS certificates and Entra redirect URIs
cannot use bare IP addresses.

## 1. NAS mount

```bash
sudo install -d -m 700 /etc/onefcst
sudo tee /etc/onefcst/nas.cred >/dev/null <<'EOF'
username=<AD service account>
password=<password>
domain=<AD domain>
EOF
sudo chmod 600 /etc/onefcst/nas.cred

sudo mkdir -p /mnt/nas/one
sudo cp deploy/mnt-nas-one.mount /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mnt-nas-one.mount
```

**Verify — Japanese filenames must be readable, not mangled:**

```bash
ls /mnt/nas/one | head
```

If you see `????` or `\357\274` escapes instead of `ONE計画.xlsm`, the
`iocharset=utf8` option is missing or not taking effect. Fix it before
going further: every glob in `config.py` will silently match nothing and the
build will fail claiming files are absent while they are plainly there.

## 2. Configuration

Put the env file and the writable directories **outside** the checkout, so the
CD workflow (`deploy/README-cd.md`) can drive compose from a disposable runner
workspace without deleting them:

```bash
sudo mkdir -p /srv/onefcst/data/backups /srv/onefcst/data/exports /srv/onefcst/sap
sudo chown -R 10001:10001 /srv/onefcst/data /srv/onefcst/sap   # container's onefcst user
sudo cp deploy/compose.env.example /srv/onefcst/.env
sudo chmod 600 /srv/onefcst/.env
openssl rand -base64 24                  # -> POSTGRES_PASSWORD
openssl rand -base64 32                  # -> ONEFCST_API_KEY
sudo $EDITOR /srv/onefcst/.env           # also set ONEFCST_DATA_HOST_DIR / SAP_CACHE_HOST_DIR
```

`ONEFCST_API_KEY` is **required** — auth is fully open when it is empty. For
purely local work you can still `cp deploy/compose.env.example .env` in the
repo instead and omit the two `*_HOST_DIR` lines; compose then keeps the
in-repo defaults.

## 3. Build and start

```bash
export COMPOSE="docker compose --env-file /srv/onefcst/.env -p onefcst"
$COMPOSE build
$COMPOSE up -d
$COMPOSE ps                              # both services must be "healthy"
curl -s http://127.0.0.1:8600/health
```

`-p onefcst` is not optional on the DGX: the project name determines the
container and volume names, so driving compose from a different directory
without it creates a **second** stack with an empty `pgdata`.

> Changing `ONEFCST_API_KEY` later needs `docker compose build` again, not
> just a restart: `VITE_API_KEY` is compiled into the JS bundle.

## 4. Migrate the data

With the SQLite database present at `app/data/onefcst.db`:

```bash
docker compose exec app python -m onefcst.migrate_to_postgres            # dry run
docker compose exec app python -m onefcst.migrate_to_postgres --commit
```

The SQLite file is opened **read-only** and never written — it stays a valid
rollback. Verify the reported counts against the pre-migration numbers,
especially `adjustments`, `compat_rules`, `audit_log`, `order_actions`.

## 5. TLS and nginx

```bash
sudo deploy/generate-self-signed-cert.sh one-forecast.twave.co.jp   # or install an internal-CA cert
sudo cp deploy/nginx-onefcst-docker.conf /etc/nginx/sites-available/onefcst
sudo ln -sf /etc/nginx/sites-available/onefcst /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

`ONEFCST_CORS_ORIGINS` is already set to `https://one-forecast.twave.co.jp` in the env
example; `docker compose up -d` to apply.

The hostname is **`one-forecast.twave.co.jp`** (A record -> 172.16.5.106).
It must match the DNS record, the TLS certificate and the Entra redirect URI
exactly — a mismatch in any one of the three breaks sign-in.

## 6. Entra ID SSO — after the platform team responds

Currently **not enabled**; the shared API key is the only authentication.
When the tenant/client/secret arrive:

1. Fill `ENTRA_*` in `.env` (including `ENTRA_COOKIE_SECRET`).
2. `docker compose -f docker-compose.yml -f deploy/docker-compose.sso.yml up -d`
3. Confirm `curl -s http://127.0.0.1:4180/ping` answers **before** touching nginx.
4. Uncomment the `auth_request` block and `/oauth2/` locations in
   `/etc/nginx/sites-available/onefcst`, then `nginx -t && systemctl reload nginx`.

Keep `ONEFCST_API_KEY` set afterwards — it still guards the API against
anything on the internal network that reaches port 8600 directly.

**SSO authenticates; it does not yet authorize.** The app has no user or
role model — the Entra role claim arrives as `X-Forwarded-Roles` and nothing
reads it. Per-department permissions are follow-on work.

---

## Operating

| Task | Command |
|---|---|
| Logs | `docker compose logs -f app` |
| Manual rebuild | via the UI, or `docker compose exec app python -m onefcst.rebuild_preserving` |
| Deploy new code | push to `main` — CD does it (`deploy/README-cd.md`); manual equivalent is `docker compose --env-file /srv/onefcst/.env -p onefcst up -d --build` |
| Maintenance on/off | `docker compose exec app python -m onefcst.maintenance on\|off\|status` |
| Backups | `app/data/backups/*.dump` — `pg_dump` custom format, keep = `ONEFCST_DB_BACKUP_KEEP` |
| Restore a backup | `docker compose exec -T db pg_restore -U onefcst -d onefcst --clean < app/data/backups/<file>.dump` |

### Things that will bite

- **Mount the ONE folder, not the share root.** `//172.25.20.11/サードウェーブグループ`
  is the share; ONE計画.xlsm is five levels deeper. `deploy/mnt-nas-one.mount`
  already has the full path — don't "simplify" it.
- **`/srv/sap` gets the weekly SAP drops via two read-only bind mounts** of
  `★週頭在庫(ZM13)` / `★組込実績(ZP21)` from inside the NAS mount. Stock is read
  from those files, and `nas_sync` is a deliberate no-op in mount mode, so
  without them the app runs with **no stock data and no order suggestions** —
  and nothing errors, because a missing weekly file is treated as non-blocking.

- **A failed backup aborts the rebuild.** Deliberate: the human data
  (adjustments, confirmed compat rules, audit log) exists nowhere else. If
  rebuilds start failing, check `pg_dump` and disk space first.
- **Maintenance mode is for DEPLOYMENTS only** (policy 2026-07-30). Turn it on
  by hand when you push new code or features; nothing turns it on
  automatically, and nothing turns it off but you. A NAS rebuild deliberately
  does **not** use it — users get a non-blocking 「再構築中」 strip driven by
  `/build/rebuild/status` and keep browsing. So "the site is in maintenance"
  now always means a person put it there.
- **`drop_all` aborts after `ONEFCST_DROP_LOCK_TIMEOUT`** (default 15s) if
  another session holds a lock. This is safe — *nothing* is dropped. Retry
  once the blocking query ends. Since rebuilds no longer close the site
  first, a long report query CAN now block one on Postgres; if a rebuild
  keeps losing this race, turn maintenance on for it deliberately.
- **One uvicorn worker, deliberately.** The scheduler runs per process; a
  second worker would run a second scheduler and two rebuilds could drop the
  database concurrently. Do not add `--workers`.
- **Code changes need a container restart**, not just a file update — the
  scheduler holds imported modules in memory and will keep running the old
  logic otherwise.

## Rollback

```bash
docker compose exec app python -m onefcst.maintenance on
docker compose down
# start the previous systemd unit with DATABASE_URL unset -> back on SQLite
sudo systemctl start onefcst-backend
docker compose exec app python -m onefcst.maintenance off   # or edit app/data/maintenance.json
```

Keep `app/data/onefcst.db` untouched for at least 4 weeks after cutover.
Only once the PostgreSQL dumps have been verified restorable should it stop
being the fallback.
