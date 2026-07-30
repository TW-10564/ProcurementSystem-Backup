# Continuous deployment onto the DGX (self-hosted runner)

Goal: merge to `main` → CI passes → the DGX rebuilds its own image and restarts,
with no artifact ever crossing the network. The runner executes **on** the
target host, so the build has direct access to the mounted NAS shares, local
storage and (when the in-house LLM lands) the GPUs.

Two workflows, deliberately separate:

| | Where it runs | What it does |
|---|---|---|
| `ci.yml` | GitHub's `ubuntu-latest` | tests on SQLite **and** Postgres, `npm run build`, `docker build`. Runs on every push **and every PR**. |
| `deploy.yml` | the DGX (`self-hosted, dgx`) | rebuild the image, restart the stack, health-check, roll back on failure. Never runs on a PR. |

`deploy.yml` triggers on `workflow_run` of CI — not on `push`. A push-triggered
deploy races CI and can ship a commit the suite is about to fail; this way
nothing reaches the DGX unless CI went green for that exact SHA.

---

## ⚠️ Read before registering the runner

A self-hosted runner runs whatever a workflow tells it to, on your hardware, as
the runner user. Therefore:

- **Keep this repository private.** GitHub's own guidance is not to use
  self-hosted runners on public repositories — a fork PR could otherwise run
  arbitrary code on the DGX.
- **Never add `pull_request` to `deploy.yml`.** That is the specific mistake
  that turns "CD" into "anyone who can open a PR has a shell on the DGX". PRs
  are handled by `ci.yml` on GitHub-hosted runners for exactly this reason.
- The runner needs Docker access, which is effectively root on that host. Give
  it its own unprivileged user, not your login.

---

## 1. Persistent directories and the env file

These live **outside** any git checkout, because the runner's workspace is
disposable — `actions/checkout` runs `git clean -ffdx` on an existing clone.
Anything inside it will be deleted eventually.

```bash
sudo mkdir -p /srv/onefcst/data/backups /srv/onefcst/data/exports /srv/onefcst/sap
# 10001 is the container's `onefcst` user (Dockerfile)
sudo chown -R 10001:10001 /srv/onefcst/data /srv/onefcst/sap

sudo cp deploy/compose.env.example /srv/onefcst/.env
sudo chmod 600 /srv/onefcst/.env
sudo nano /srv/onefcst/.env      # fill POSTGRES_PASSWORD, ONEFCST_API_KEY, …
```

`/srv/onefcst/.env` must set, and the workflow's preflight refuses to deploy
otherwise:

```ini
ONEFCST_DATA_HOST_DIR=/srv/onefcst/data     # absolute, outside the repo
SAP_CACHE_HOST_DIR=/srv/onefcst/sap         # absolute, outside the repo
ONEFCST_API_KEY=<non-empty>                 # empty means auth is OPEN
```

Migrating an existing manual deployment? Move the data first, or the first
CD run starts from an empty backups directory:

```bash
sudo rsync -a /opt/onefcst/app/data/ /srv/onefcst/data/
sudo rsync -a /opt/onefcst/data/sap/ /srv/onefcst/sap/
```

The Postgres data itself is in the named volume `onefcst_pgdata` and is not
affected by any of this — provided the compose project name stays `onefcst`,
which the workflow pins via `COMPOSE_PROJECT_NAME`.

## 2. Register the runner

GitHub → repo **Settings → Actions → Runners → New self-hosted runner →
Linux/x64**, then on the DGX as the runner user:

```bash
sudo useradd -m -s /bin/bash ghrunner
sudo usermod -aG docker ghrunner          # docker access == root on this host
sudo su - ghrunner

mkdir ~/actions-runner && cd ~/actions-runner
curl -o actions-runner-linux-x64.tar.gz -L \
  https://github.com/actions/runner/releases/latest/download/actions-runner-linux-x64-<version>.tar.gz
tar xzf actions-runner-linux-x64.tar.gz

# token comes from that Settings page and expires in ~1 hour
./config.sh --url https://github.com/TW-10532/Procurement-System \
            --token <REGISTRATION_TOKEN> \
            --name dgx-onefcst \
            --labels dgx \
            --work _work
```

The `dgx` label is required — `deploy.yml` asks for `runs-on: [self-hosted,
dgx]` so it can never be picked up by some other self-hosted runner.

Copy the exact download URL/version from the Settings page rather than the
placeholder above; it changes with each runner release.

## 3. Run it as a systemd service

The runner ships its own installer, which is the supported path — do not
hand-write a unit:

```bash
cd ~/actions-runner
sudo ./svc.sh install ghrunner
sudo ./svc.sh start
sudo ./svc.sh status
```

It now starts at boot and reconnects on its own. Logs:

```bash
journalctl -u "actions.runner.*" -f
```

## 4. First deploy

**`deploy.yml` must be on `main` before it can ever fire.** GitHub only honours
a `workflow_run` trigger from the default branch, so while this work sits on
`pre-deploy` nothing will happen — merge to `main` first. (`ci.yml` runs from
any branch, so CI is live immediately.)

```bash
# from your machine
git push origin main
```

Watch it in the repo's **Actions** tab: `CI` runs on GitHub, then `Deploy
(DGX)` picks up on the runner. The job summary reports the ref, the previous
image ID, and whether maintenance mode was already on.

Or deploy any ref by hand — **Actions → Deploy (DGX) → Run workflow**, which
also takes a SHA. That is the rollback path (see below).

---

## What a deploy actually does

1. **Preflight** — env file present, docker reachable, both host dirs absolute
   and existing, API key non-empty. Fails before touching the running site.
2. **Records the image in service** (`onefcst-app:latest`) as the rollback
   target. This is why compose names the image explicitly.
3. **Maintenance mode ON** — a deployment is precisely what that flag is for
   (see CLAUDE.md: rebuilds deliberately do **not** use it). If an operator
   already had it on, the workflow leaves it on at the end.
4. `docker compose build --pull app` then `up -d --remove-orphans`.
5. **Health check** — `/health` *and* the SPA at `/`. Checking both matters:
   the frontend is a separate Dockerfile stage, and a failure there leaves a
   perfectly healthy API serving no dashboard.
6. **On failure, roll back** by re-tagging the previous image and `up -d
   --no-build`, then health-check again. Maintenance mode is deliberately left
   ON — the site is not serving the new code, so users should see a notice
   while you read the logs.
7. **Maintenance mode OFF** on success.

### Rollback by hand

```bash
# Actions -> Deploy (DGX) -> Run workflow -> ref: <previous good SHA>
```

or on the DGX directly:

```bash
docker images onefcst-app            # pick the previous image ID
docker tag <old-id> onefcst-app:latest
docker compose --env-file /srv/onefcst/.env -p onefcst up -d --no-build
```

---

## Things this does NOT do

- **No schema migrations.** There is no migration tool in this project; the
  schema is created by `init_db()` and reshaped by a full rebuild. If a commit
  changes `models.py`, deploy will start a container whose code expects columns
  the database does not have. Run `python -m onefcst.rebuild_preserving` after
  such a deploy (it snapshots and restores the human data), and prefer a quiet
  window for it.
- **No NAS rebuild.** Deploying ships code, not data. The weekly scheduler and
  the UI button still own that, and they no longer touch maintenance mode.
- **No secret rotation.** `ONEFCST_API_KEY` is compiled into the JS bundle, so
  rotating it means editing `/srv/onefcst/.env` and re-running the deploy so
  the image is rebuilt. A restart alone will not do it.
- **No GPU work yet.** Nothing in the current image needs a GPU — the compat
  rule drafting calls an external LLM endpoint. When the in-house model lands,
  this runner is already in the right place for it: add
  `deploy: resources: reservations: devices:` (or `gpus: all`) to the app
  service and the build/run happens next to the weights.

## Troubleshooting

| Symptom | Cause |
|---|---|
| Job stuck "Waiting for a runner" | runner service down (`sudo ./svc.sh status`) or the `dgx` label missing from registration |
| `permission denied … docker.sock` | runner user not in the `docker` group, or added without restarting the service |
| Preflight: "must be an absolute path" | `ONEFCST_DATA_HOST_DIR` / `SAP_CACHE_HOST_DIR` still relative in `/srv/onefcst/.env` |
| Site shows the maintenance screen after a failed deploy | intentional. Fix, redeploy — or clear it: `docker compose --env-file /srv/onefcst/.env exec app python -m onefcst.maintenance off` |
| Deploy healthy but the dashboard is empty | code deployed fine, data did not — check `/build/rebuild/status` and whether the NAS mount is still up |
| Two sets of containers appear | something ran compose without `-p onefcst`; the project name is what keeps one stack and one `pgdata` volume |
