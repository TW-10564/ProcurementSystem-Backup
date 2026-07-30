# Security audit — 2026-07-30 (pre-deployment)

Scope: the whole deployed surface — API auth, input handling, the SPA, Docker
image, nginx, the new CD pipeline, secrets handling, dependencies, and what is
in git history. Read against branch `pre-deploy` at `6e136ba`.

**Verdict: safe to deploy on the internal network. Not safe to expose beyond
it until F1 is resolved.** Nothing found is a remote-code-execution or
data-destruction hole reachable by an outsider. The dominant risk is that the
API's only credential is published to every visitor, so on the LAN the app has
no meaningful authorization boundary at all.

Legend: **Critical** = fix before go-live · **High** = fix in the first week ·
**Medium** = scheduled · **Low** = note it.

---

## Fixed during this audit

| | What | Where |
|---|---|---|
| ✅ | **Shell injection in the deploy workflow.** `${{ inputs.ref }}` was interpolated into a `run:` body, so a dispatched ref containing shell metacharacters would have executed on the DGX. Values now arrive via `env:` and the ref is shape-checked against `^[A-Za-z0-9._/-]+$`. Requires repo write access to reach, but "only trusted people can trigger it" is not a reason to leave an injection in a job that runs on our own hardware. | `.github/workflows/deploy.yml` |
| ✅ | **Client-spoofable `X-Forwarded-For`.** The container runs uvicorn with `--forwarded-allow-ips '*'`, and uvicorn then takes the **leftmost** XFF entry — verified in the installed uvicorn 0.38 source. nginx was *appending* (`$proxy_add_x_forwarded_for`), so any browser could choose the IP the app sees: unlimited rate-limit windows by rotating one header (defeating exactly the API-key-guessing defence that module exists for) and a poisoned access log. nginx now **overwrites** with `$remote_addr`. | both nginx configs |
| ✅ | **No security headers.** Added HSTS (1 year), a strict CSP (`default-src 'self'`, `frame-ancestors 'none'`, `base-uri 'none'`), `nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`. Without HSTS every fresh visit still made one interceptable plaintext request before the 301. Both configs re-validated in a real nginx with a certificate present. | both nginx configs |
| ✅ | **Business data committed to git.** `SAP在庫抽出.db.prev-backup` — a 2.8 MB Microsoft Access database of SAP stock data (社外秘) — was tracked, because `.gitignore` listed only the exact name `SAP在庫抽出.db`. Untracked (kept on disk) and the ignore rule widened to `SAP在庫抽出.db*` / `*.accdb`. **See F3: it is still in history.** | `.gitignore` |

---

## F1 — Critical: the API key is published to everyone who can load the page

The shared `X-API-Key` is compiled into the JS bundle (`VITE_API_KEY`), and the
bundle is served **outside** the auth gate — deliberately, since a browser must
fetch it before it can present any key. Verified concretely: the backend's
`ONEFCST_API_KEY` appears verbatim in `app/frontend/dist/assets/index-*.js`,
and `GET /` returns 200 with no credential.

So the real access boundary is *network reachability*, nothing more. Anyone who
can open the dashboard can read the key out of the bundle in one click and then
call every endpoint directly, including:

- `POST /build/rebuild` — drops and rebuilds the entire database
- `POST /maintenance` — locks every user out of the site
- `POST /compat-rules/bulk-confirm` — mass-approves AI-drafted rules
- `POST /adjustments`, `/order-actions/*` — writes procurement decisions under
  any `author` string they choose, which also makes the audit log unreliable

This is not a coding defect — it is the known Phase-1 design ("a floor, not a
ceiling", `api/auth.py`) meeting a deployment that is about to have real users.
It is listed here because the consequence is stronger than the code comments
imply: **the key is not a secret, and the audit log's `author` field is
self-asserted.**

Fix, in order of value:

1. **Entra ID SSO in front of everything** (the scaffolding in
   `deploy/nginx-onefcst-docker.conf` + `deploy/docker-compose.sso.yml`). This
   is the actual fix: identity comes from the IdP, not from a string in a
   bundle. Blocked on the platform team.
2. **Then stop trusting the client-supplied `author`** — take it from
   `X-Forwarded-Email` once oauth2-proxy sets it. Until then treat the audit
   log as advisory.
3. **Then role checks** (Phase 7). `X-Forwarded-Roles` will arrive and nothing
   reads it; a Planning user can currently trigger a rebuild.

Until SSO lands, mitigate: keep the site on the internal network only (nginx
already binds the app to loopback), and treat "who did what" as untrusted.

## F2 — High: any authenticated caller can destroy the database

`POST /build/rebuild` runs `drop_all` + full re-ingest, and `POST /forecasts/run`
regenerates every forecast. Both sit behind nothing but the shared key from F1.
A curious user — or a mistyped `curl` — takes the dashboard down for ~4½ minutes
and rewrites the whole DB. The backup taken first is what makes this survivable,
not any access control.

Fix: role gate once SSO is in (`Admin`/`Purchasing` only). Cheap interim
option: require a confirmation token in the body that the UI knows and a stray
script would not, or move the rebuild to a `POST` that only the scheduler and
an operator CLI can reach.

## F3 — High: the Access DB is still in git history

Removing it from HEAD does not remove it from history — every clone still
contains the 2.8 MB SAP extract, and it is on GitHub. Two questions to settle:

1. **Is the repository private?** I could not verify that from here. If it is
   public, treat this as a data-exposure incident and rotate nothing but
   *remove the repo from public view immediately*.
2. **Rewrite history or accept it?** `git filter-repo --path 'SAP在庫抽出.db.prev-backup' --invert-paths`
   followed by a force-push cleans it, but rewrites every commit ID and breaks
   anyone else's clone. Given the repo has one contributor, this is realistic —
   but do it deliberately, and *before* more collaborators appear, not after.

Related: `app/frontend/.env` and `app/backend/.env` contain the real dev key and
are correctly gitignored and correctly excluded from the Docker context
(verified: no `.env` in the built image, only `.env.example`).

## F4 — Medium: unbounded `limit` parameters

`/forecasts`, `/compat-rules`, `/discontinuation-suggestions`, `/notifications`,
`/items` and `/build/rebuild/history` all take `limit` with no ceiling.
`GET /forecasts?limit=1000000` makes the single-process API build a huge
response while holding a session — the report endpoints already hold sessions
for minutes, and one worker means one slow request is everyone's slow request.
Rate limiting caps *requests per minute*, not the cost of one request.

Fix: `limit: int = Query(50, ge=1, le=2000)` on each. Not applied here because
it needs a check against what the frontend actually requests (observed: 80).

## F5 — Medium: `pg_dump` backups have no enforced permission mode

`app/data/backups/*.dump` is a complete copy of every forecast, adjustment and
audit record. On this dev box they are `0777` (a WSL `/mnt/c` artifact, not
meaningful), but nothing in `rebuild_preserving.backup_db()` sets a mode, so on
the DGX they inherit the process umask. Anyone with a shell on that host, or
any future container that mounts the data dir, reads the entire business
dataset.

Fix: `os.chmod(path, 0o600)` after writing, and `chmod 700 /srv/onefcst/data/backups`.

## F6 — Medium: `react-router` advisory GHSA-qwww-vcr4-c8h2 (2 × high)

`npm audit` flags `react-router@7.18.1` (range 7.12.0–8.2.0): *RSC Mode CSRF
Bypass Allows Action Execution Before 400 Response*. **Not exploitable here** —
verified the app uses a plain `BrowserRouter` with no data router, no `loader`,
no `action`, and no RSC. `npm audit fix --force` would *downgrade* to 7.11.0, a
breaking change, so do not run it. Track for a patched release ≥ 8.2.1 and
upgrade then. Backend dependencies: `pip-audit` reports **no known
vulnerabilities**.

## F7 — Low: the in-house LLM call is plaintext HTTP with no response cap

`advisory/llm_rules.py` POSTs part names to `config.LLM_URL`
(default `http://172.16.5.105:8000`) with `urllib.request.urlopen` — no TLS, no
response-size limit, and the model's reply is parsed as JSON into rule drafts.
On an internal network the main consequences are (a) part names (社外秘) cross
the LAN in clear text, and (b) a compromised or misbehaving model server can
return an unbounded body or content designed to be confirmed by a human as a
compatibility rule. The human-confirm flow is the mitigation that matters here.

Also note `LLM_API_KEY` falls back to `OPENAI_API_KEY`: if someone sets
`ONEFCST_LLM_URL` to OpenAI, 社外秘 part names leave the company. That was a
deliberate decision earlier for CPU/MB drafting — just keep it deliberate.

## F8 — Low: `POST /maintenance` accepts an unvalidated `dict`

`set_maintenance(body: dict)` takes any JSON. `message` is written to
`maintenance.json` and rendered site-wide. React escapes it, so this is not XSS
(checked: no `dangerouslySetInnerHTML`, no `innerHTML`, no `eval` anywhere in
the frontend), but there is no length bound — a multi-megabyte message would be
re-read and re-sent on every 20-second poll from every open tab.

Fix: a Pydantic model with `message: str = Field(max_length=500)`.

---

## Checked and found sound

- **SQL injection** — everything goes through the SQLAlchemy ORM. The one raw
  `text()` is `SET LOCAL lock_timeout`, which Postgres cannot bind-param; its
  value is validated against `^\d+(ms|s|min)?$` before interpolation. Correct.
- **Command injection** — all three `subprocess` call sites (`pg_dump`,
  `mdb-export`, `wslpath`) pass argument lists, never `shell=True`. The Postgres
  password goes via `PGPASSWORD` in the child environment, not argv, so it does
  not appear in `ps`.
- **Path traversal** — no endpoint accepts a filename. Every `/export/*` route
  builds its own path in `export/excel.py`; `FileResponse` only ever receives
  that. The SPA fallback serves from a fixed `dist` directory via Starlette's
  `StaticFiles`, which normalises paths.
- **Timing-safe key comparison** — `hmac.compare_digest`, on bytes (the
  str-vs-str form raises on non-ASCII, which would have turned a bad key into a
  500 instead of a 401).
- **Rate-limit identity** — keyed on the API key only when it *validates*, else
  on source IP. An unverified header as identity would hand a brute-forcer a
  fresh window per guess; that was already fixed in the 2026-07-24 pass. With
  the nginx XFF fix above, the IP fallback is now trustworthy too.
- **Container posture** — runs as uid 10001, non-root; NAS mounts read-only at
  both the host and container layer; Postgres publishes **no** port; the app
  publishes to `127.0.0.1` only; no Docker socket mounted; no secrets in any
  image layer (verified inside the built image).
- **CORS** — defaults to localhost, explicit origins in production, no wildcard
  with credentials.
- **Auth exemptions** — only `GET /health` and `GET /maintenance`. Both are
  deliberate and neither leaks data (`/maintenance` returns a flag, a message
  and a timestamp).
- **CSRF** — no cookie-based auth, so nothing to forge with; a custom header is
  required, which cross-origin forms cannot set.
- **Secrets in the repo** — no key-shaped literals in any tracked file.

## Recommended order

1. **F1 → SSO** (blocked on the platform team; everything else is smaller)
2. **F3 → decide on the history rewrite** while the repo has one contributor
3. **F2, F5** — role gate on destructive endpoints, `0600` on backups
4. **F4, F8** — bound `limit`, validate the maintenance body
5. **F6, F7** — watch for the router patch; consider TLS on the model endpoint
