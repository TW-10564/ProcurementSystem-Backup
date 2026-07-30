# DGX pilot deployment — auth, HTTPS, process supervision

Addresses `app/PILOT_DEPLOY_PLAN.md` blockers #2–4 (auth disabled by
default, no HTTPS, no process supervisor). Scope decided 2026-07-21:
systemd (not Docker), internal-network-only (self-signed cert, no public
DNS/ACME needed).

Architecture: nginx is the only thing reachable from the network. It
terminates TLS, serves the frontend's static build directly, and reverse-
proxies `/api/*` to the backend — which itself listens on `127.0.0.1:8600`
only, never on `0.0.0.0`. Browser sees one origin the whole time.

## One-time setup on the DGX box

```bash
# 1. Checkout + Python venv
sudo mkdir -p /opt/onefcst && sudo chown $USER /opt/onefcst
git clone <this repo> /opt/onefcst
cd /opt/onefcst/app/backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. Backend secrets — fill in a real value for ONEFCST_API_KEY:
#    python3 -c "import secrets; print(secrets.token_urlsafe(32))"
cp ../../deploy/backend.env.production.example .env.production
$EDITOR .env.production

# 3. Frontend build — VITE_API_KEY must match ONEFCST_API_KEY above exactly
cd ../frontend
cp ../../deploy/frontend.env.production.example .env.production
$EDITOR .env.production
npm ci
npm run build          # writes to app/frontend/dist/

# 4. TLS cert (self-signed, internal-network use)
sudo bash ../../deploy/generate-self-signed-cert.sh one-forecast.twave.co.jp

# 5. Dedicated non-root service user
sudo useradd --system --no-create-home --shell /usr/sbin/nologin onefcst
sudo chown -R onefcst:onefcst /opt/onefcst/app/data

# 6. systemd unit
sudo cp ../../deploy/onefcst-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now onefcst-backend
sudo systemctl status onefcst-backend      # confirm it's running

# 7. nginx — edit server_name in the conf first if not using "one-forecast.twave.co.jp"
sudo cp ../../deploy/nginx-onefcst.conf /etc/nginx/sites-available/onefcst
sudo ln -s /etc/nginx/sites-available/onefcst /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## Verifying it worked

```bash
curl -k https://one-forecast.twave.co.jp/api/health        # {"status":"ok",...}
curl -k https://one-forecast.twave.co.jp/                  # frontend HTML
# then open https://one-forecast.twave.co.jp/ in a browser — expect a self-signed
# cert warning (expected, see nginx-onefcst.conf's comment), everything
# else should behave exactly like local dev.
```

## After a NAS rebuild or code update

- Code update: `git pull`, re-run `pip install -r requirements.txt` if
  `requirements.txt` changed, `sudo systemctl restart onefcst-backend`.
  Frontend changes need `npm run build` again (static output, not
  hot-reloaded — this isn't the Vite dev server).
- Data refresh: same as local dev — `python3 -m onefcst.rebuild_preserving`
  (not bare `onefcst.build`, see `CLAUDE.md` constraint #2) — no service
  restart needed, the backend reads the SQLite/Postgres file live.

## Still open after this

- Postgres migration itself (`DATABASE_URL` in `.env.production`) — this
  deploy setup supports it (see `db.py`'s dialect-aware pooling from this
  same session) but standing up the actual Postgres instance and DGX's own
  NAS mount are separate, not yet done.
- Log aggregation/alerting (`PILOT_DEPLOY_PLAN.md` medium-priority item) —
  the backend still only `print()`s; systemd captures that into the
  journal (`journalctl -u onefcst-backend -f`), which is a real improvement
  over the current bare-terminal setup but still not alerting/monitoring.
- Cert renewal — the self-signed cert is valid 825 days; nothing
  auto-renews it. Calendar-remind yourself, or switch to an internal CA
  that supports automated reissuance if this deployment outlives that.
