# From "SSO credentials received" to live — task list

Written 2026-07-30. Everything here is blocked on the platform team returning
the Entra values, **except phase 0**, which you can do today.

What you are waiting for:

| Value | Goes into |
|---|---|
| `ENTRA_TENANT_ID` | `/srv/onefcst/.env` |
| `ENTRA_CLIENT_ID` | `/srv/onefcst/.env` |
| `ENTRA_CLIENT_SECRET` | `/srv/onefcst/.env` |
| Redirect URI registered as `https://one-forecast.twave.co.jp/oauth2/callback` | Entra app registration (their side) |
| Group/role claim names for `Purchasing` / `Planning` / `Admin` | Phase 4 below |
| DNS rename `one-forcast` → `one-forecast` | TLS cert + the redirect URI |

Estimates assume things work. The two that historically do not: the SMB mount
(phase 1) and the redirect URI round-trip (phase 3).

---

## Phase 0 — do now, before the credentials arrive (~2 h)

Nothing here needs Entra, and each one removes a variable from the day itself.

- [ ] **0.1** Push the five local commits (`git push origin pre-deploy`), open a
      PR to `main`, confirm CI goes green on GitHub for the first time.
- [ ] **0.2** Merge to `main`. Note `deploy.yml` only becomes active once it is
      on the default branch — `workflow_run` triggers are read from there.
- [ ] **0.3** Decide the git-history question (audit F3): the SAP Access DB is
      still in history. Rewrite now while there is one contributor, or accept.
- [ ] **0.4** Confirm the repository is **private** (audit F3, F1).
- [ ] **0.5** Fix the dev `SAP_ACCDB_PATH` so this box stops forecasting off the
      Jul 22 stock copy, and re-check numbers against the Jul 27 extract.
- [ ] **0.6** Cheap audit items while you are in the code: bound the `limit`
      params (F4), `chmod 0600` on `pg_dump` output (F5), validate the
      maintenance body (F8).

## Phase 1 — DGX access and the NAS mount (~2–4 h, highest risk)

- [ ] **1.1** Shell access on the DGX confirmed; `sudo` available.
- [ ] **1.2** `nc -vz 172.25.20.11 445` — is the NAS reachable at all? If not,
      the relay plan from the DGX/NAS notes applies and everything below waits.
- [ ] **1.3** `hostname -f` → expect `one-forecast.twave.co.jp`.
- [ ] **1.4** Install prerequisites: `docker`, `docker compose`, `cifs-utils`,
      `git`, `nginx`.
- [ ] **1.5** Store the AD credentials for the share in a root-only file
      (`/etc/onefcst/nas.cred`, mode 0600).
- [ ] **1.6** Install `deploy/mnt-nas-one.mount`, `systemctl enable --now` it.
- [ ] **1.7** **`ls /mnt/nas/one` and read the Japanese filenames.** This is the
      single most likely failure of the whole deployment: if `iocharset=utf8`
      is not taking effect, names come back mangled, every glob in `config.py`
      matches nothing, and the build fails claiming files are missing while
      they are plainly on the share. Do not proceed past a mangled listing.
- [ ] **1.8** Confirm the mount is **read-only** (`touch /mnt/nas/one/x` must
      fail) — CLAUDE.md constraint #1 depends on it.
- [ ] **1.9** Same for the planning share, if it is separate.

## Phase 2 — the stack, without SSO (~2 h)

Get the app healthy behind plain TLS first. Adding SSO to a working site is far
easier to debug than doing both at once.

- [ ] **2.1** `/srv/onefcst/{data,sap}` created, owned by `10001:10001`;
      `/srv/onefcst/.env` from `deploy/compose.env.example`, mode 0600.
- [ ] **2.2** Fill it: `POSTGRES_PASSWORD`, `ONEFCST_API_KEY`,
      `ONEFCST_CORS_ORIGINS=https://one-forecast.twave.co.jp`,
      `ONEFCST_DATA_HOST_DIR=/srv/onefcst/data`, `SAP_CACHE_HOST_DIR=/srv/onefcst/sap`.
- [ ] **2.3** Clone the repo, `docker compose --env-file /srv/onefcst/.env -p onefcst build`.
- [ ] **2.4** `up -d`; both services `healthy`; `curl 127.0.0.1:8600/health`.
- [ ] **2.5** Migrate the SQLite data: `python -m onefcst.migrate_to_postgres`
      (dry run) → read the report → `--commit`. Verify adjustments,
      compat_rules, audit_log, order_actions counts match.
- [ ] **2.6** One full rebuild on the DGX: `python -m onefcst.rebuild_preserving`.
      Expect Gate A ≈ 99.8 % and ~4–5 min. This is the first time the container
      reads the real NAS rather than a cache.
- [ ] **2.7** TLS cert for `one-forecast.twave.co.jp` installed at
      `/etc/onefcst/tls/`, mode 0600 on the key.
- [ ] **2.8** `deploy/nginx-onefcst-docker.conf` into `/etc/nginx/conf.d/`,
      `nginx -t`, reload. It now ships HSTS + CSP + `nosniff` +
      `X-Frame-Options` and overwrites `X-Forwarded-For` (audit fixes).
- [ ] **2.9** Browse `https://one-forecast.twave.co.jp` end to end: dashboard,
      forecast list, a detail page, order suggestions, an Excel export
      (Japanese filename must survive), the mismatch report.
- [ ] **2.10** Restart the host (`reboot`) and confirm everything comes back:
      the mount unit, the containers (`restart: unless-stopped`), nginx.

## Phase 3 — SSO (~3–4 h once the values are in hand)

- [ ] **3.1** Confirm the DNS rename landed and the cert matches the corrected
      spelling. The Entra redirect URI must match **exactly**, including scheme.
- [ ] **3.2** Add `ENTRA_TENANT_ID/CLIENT_ID/CLIENT_SECRET/REDIRECT_URI` to
      `/srv/onefcst/.env`, plus `ENTRA_COOKIE_SECRET`
      (`openssl rand -base64 32 | head -c 32` — must be exactly 16/24/32 bytes).
- [ ] **3.3** Start the overlay:
      `docker compose --env-file /srv/onefcst/.env -p onefcst -f docker-compose.yml -f deploy/docker-compose.sso.yml up -d`.
      Check oauth2-proxy's own logs before touching nginx — a bad client secret
      is obvious there and invisible from nginx.
- [ ] **3.4** Uncomment the `auth_request` lines and the `/oauth2/` blocks in
      the nginx config, `nginx -t`, reload. Note the warning already in that
      file: uncommenting before oauth2-proxy is up makes **every** request 500.
- [ ] **3.5** Sign in as yourself from a clean browser profile. Verify the
      redirect, the callback, and that a signed-out session cannot reach `/`.
- [ ] **3.6** Verify `X-Forwarded-Email` / `X-Forwarded-Groups` actually arrive
      at the app (log them once, temporarily). Everything in phase 4 depends on
      the claim names being what you expect.
- [ ] **3.7** Test with a second real user from procurement, not just yourself.
- [ ] **3.8** Confirm `/health` still answers unauthenticated (container
      healthcheck and any monitoring depend on it).

## Phase 4 — authorization, the part SSO does not give you (~1–2 days)

SSO answers *who*, not *what they may do*. Right now (audit F1/F2) the shared
API key is compiled into the JS bundle, so anyone who can load the page can
call every endpoint — including `POST /build/rebuild`, which drops the
database. SSO in front of the site is what makes the following worth building.

- [ ] **4.1** Read identity from the proxy headers into a request-scoped user
      (email + groups), with a single place that decides "is this request
      allowed to write".
- [ ] **4.2** **Stop trusting the client-supplied `author`.** Take it from
      `X-Forwarded-Email` so the audit log means something. Until this lands,
      treat every `author` value as self-asserted.
- [ ] **4.3** Gate the destructive endpoints to `Admin` (and `Purchasing` where
      it makes sense): `POST /build/rebuild`, `POST /forecasts/run`,
      `POST /maintenance`, `POST /compat-rules/bulk-confirm`.
- [ ] **4.4** Decide what `Planning` may do — read-only, or adjustments on
      Tier-1 only.
- [ ] **4.5** Tests for each rule, including "no headers at all" (a direct
      request to 8600 that bypasses nginx must not be treated as an admin).
- [ ] **4.6** Then reconsider the shared API key: with SSO enforcing identity at
      the edge, the bundled key stops being the boundary and can become a
      defence-in-depth check rather than the only lock.

## Phase 5 — operational readiness (~half a day)

- [ ] **5.1** CD: register the self-hosted runner per `deploy/README-cd.md`
      (own user, `docker` group, label `dgx`, `svc.sh install`). Deploy a
      trivial commit and watch it roll through.
- [ ] **5.2** Force a deploy failure on purpose (break the build) and confirm
      the rollback step restores the previous image and leaves maintenance mode
      on. Untested rollback is not rollback.
- [ ] **5.3** Verify a `pg_dump` backup is actually restorable into a scratch
      database on the DGX — not just that the file exists.
- [ ] **5.4** Decide backup retention and where dumps are copied off-host.
      `/srv/onefcst/data/backups` on one machine is not a backup.
- [ ] **5.5** Confirm the weekly scheduler fires on the DGX and that the
      non-blocking 「再構築中」 strip appears for users (not the full-screen
      maintenance lock — that is now deployments only).
- [ ] **5.6** Basic monitoring: something that notices `/health` failing and
      tells a human.
- [ ] **5.7** Log rotation for docker and nginx.

## Phase 6 — handover to the team (~half a day)

- [ ] **6.1** Walk two procurement users through the dashboard on the real URL.
- [ ] **6.2** Write down, for them: what to do when the site shows the
      maintenance notice, what the 「再構築中」 strip means, and who to call.
- [ ] **6.3** Tell them explicitly what the system does **not** model —
      no incoming-PO / in-transit quantity (CLAUDE.md #5), so order suggestions
      must still be checked against purchasing's own PO tracker.
- [ ] **6.4** Agree the weekly rhythm: who uploads the sales plan, when the SAP
      extract is refreshed, who reviews the reconciliation report.
- [ ] **6.5** Keep `app/data/onefcst.db` (the SQLite fallback) untouched for at
      least 4 weeks. Unsetting `DATABASE_URL` is still the whole rollback.

---

## Order that minimises risk

1. Phase 0 today — pushes, CI green, private-repo check.
2. Phase 1, and stop at 1.7 if the filenames are wrong. Fix that first.
3. Phase 2 to a working HTTPS site with real data. **This is a deliverable on
   its own** — the shared key plus internal-network-only is the same posture the
   system has had all along.
4. Phase 3 when the credentials arrive.
5. Phase 4 immediately after SSO — until then the audit's F1/F2 stand.
6. Phases 5–6 in the following week.
