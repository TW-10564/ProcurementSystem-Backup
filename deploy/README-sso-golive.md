# Deploying the SSO build to the DGX — `https://one-forecast.twave.co.jp`

Written 2026-07-30, for the first deployment that carries Entra ID sign-in.
Follow it in order: each step has a check that isolates one failure, because
almost every symptom here ("it just shows the dashboard", "it 401s", "it
redirects forever") has three unrelated causes that look identical.

**The hostname is `one-forecast.twave.co.jp`, never the bare `twave.co.jp`.**
The certificate's SAN is `DNS:*.twave.co.jp` — a wildcard covers exactly one
label, so it does not cover the apex — and `one-forecast` is the name with the
A record to 172.16.5.106.

---

## 0. What the DGX looked like before this deployment

Recorded so you can tell what changed:

```
:80    nginx/1.24.0 (Ubuntu), answering for one-forecast.twave.co.jp
:8600  onefcst running, pre-SSO bundle (index-2I4zALBX.js, built 06:25)
:443   CLOSED — no TLS
```

`GET /auth/config` returned **HTML** rather than JSON, which is the reliable
tell that a deployment predates the SSO code: unknown paths fall through to the
SPA and answer 200, so a 200 alone proves nothing.

## 1. Get the code there

The SSO work is 10 commits on `pre-deploy`. Either push and pull:

```bash
# on the dev box
git push origin pre-deploy
# on the DGX
cd /srv/onefcst/checkout && git fetch && git checkout pre-deploy && git pull
```

…or copy it directly if pushing is not available:

```bash
rsync -av --delete \
  --exclude .git --exclude node_modules --exclude app/data \
  --exclude '*.pem' --exclude '*.crt' \
  ./ user@172.16.5.106:/srv/onefcst/checkout/
```

**Check:** `git log --oneline -1` on the DGX shows the planning-resolution
commit (`19b5bd0` or later).

## 2. TLS on 443 — first, and not optional

Entra permits plain `http` for `localhost` only. A redirect URI on a hostname
**must** be https, so without this nothing else in this file matters.

Copy the three files out-of-band (scp/USB — never through the repo; they are
gitignored deliberately), then:

```bash
sudo deploy/install-tls-cert.sh star_twave_co_jp.crt "DigiCertCA 1.crt" newkey.pem
sudo cp deploy/nginx-onefcst-docker.conf /etc/nginx/sites-available/onefcst
sudo ln -sf /etc/nginx/sites-available/onefcst /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

The script refuses to install unless the key matches the certificate and the
chain verifies, and writes the fullchain **leaf-first** (reversed works in
browsers and fails on older Android).

**Check** — from another machine, so you are testing DNS and the chain, not
just the file:

```bash
echo | openssl s_client -connect one-forecast.twave.co.jp:443 \
     -servername one-forecast.twave.co.jp 2>/dev/null \
   | grep -E "subject=|Verify return code"
```

Expect `Verify return code: 0 (ok)`. Anything else is the chain, not the cert.
Certificate expires **2026-12-02** — renewal is the same command plus
`--reload`.

## 3. Register the origin in Entra

App registration → **認証 (Authentication)** → **プラットフォームを追加** →
**シングルページ アプリケーション**:

```
https://one-forecast.twave.co.jp
```

Origin only — no path, no trailing slash. MSAL sends `window.location.origin`.

It must be under **SPA**, not **Web**. Under Web, Entra demands a client secret
at token exchange and sign-in fails with `AADSTS9002326` — registered, but
unusable by this design. Leave 「暗黙的な許可およびハイブリッド フロー」
unticked; PKCE does not need implicit grant.

**Check:** the URI appears in the list under the SPA heading. This is worth
reading rather than assuming — a wrong platform is invisible until sign-in.

## 4. Configure `/srv/onefcst/.env`

```bash
ONEFCST_ENTRA_TENANT_ID=1a15a0a6-60b5-4bd3-8cb9-302fca61af77
ONEFCST_ENTRA_CLIENT_ID=69dd97ea-e383-4362-8b39-39f4964810e3
ONEFCST_CORS_ORIGINS=https://one-forecast.twave.co.jp

# Until Entra "assignment required" is set, admin consent alone lets ANY
# account in the tenant sign in. This closes that window.
ONEFCST_ENTRA_ALLOWED_EMAILS=m_balamurukan@twave.co.jp,m_thivya@twave.co.jp

# 1 keeps the shared key usable for scripted callers; 0 makes a Microsoft
# identity the only way in.
ONEFCST_ENTRA_ALLOW_API_KEY=1
```

Keep `ONEFCST_API_KEY` set — it still guards port 8600 against anything on the
internal network that bypasses nginx.

## 5. Build — and it must be `--build`

**The single most likely thing to go wrong.** `VITE_ENTRA_TENANT_ID` and
`VITE_ENTRA_CLIENT_ID` are compiled INTO the JS bundle; compose passes them as
build args from the same `ONEFCST_ENTRA_*` values set above. Copying the source
and restarting is not enough.

```bash
export COMPOSE="docker compose --env-file /srv/onefcst/.env -p onefcst"
$COMPOSE up -d --build
```

Skip `--build` and the old image is reused: the site comes up looking perfectly
healthy **with no sign-in at all**, because `ssoEnabled` is false in that
bundle. There is no error to notice — that is what makes it dangerous.

Build the SPA **without** `VITE_API_KEY` once SSO is enforced. While the key is
in the bundle, anyone who can load the page holds it (audit finding F1).

`-p onefcst` is not optional: the project name determines container and volume
names, so driving compose from a different directory without it creates a
second stack with an empty `pgdata`.

## 6. Verify, in this order

```bash
# a) is the NEW code live? JSON here, HTML means the old build is still up
curl -s http://127.0.0.1:8600/auth/config | jq

# b) is the API actually closed?
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8600/items      # 401

# c) do the public endpoints still answer? (probes depend on these)
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8600/health     # 200
```

`(a)` must show `enabled: true` with the right ids, and
`restricted_to_named_users: 2` if you set the allowlist. The addresses
themselves are never returned — that endpoint is public and the list is a
roster of employees.

Then in a browser:

1. Open `https://one-forecast.twave.co.jp` → straight to Microsoft, no button.
2. Sign in.
3. Check `GET /me` reports `source: "entra"` and your address. **If it says
   `api-key`, the browser is not sending a token** and every write would be
   attributed anonymously — stop and fix that before letting anyone use it.

## 7. Known failure modes, by symptom

| Symptom | Cause |
|---|---|
| Dashboard loads, never asks to sign in | built without `VITE_ENTRA_*` — you skipped `--build` |
| `AADSTS50011` redirect URI mismatch | origin not registered, or has a trailing slash/path |
| `AADSTS9002326` cross-origin redemption | registered under **Web** instead of **SPA** |
| `AADSTS65001` / needs admin approval | admin consent not granted for the tenant |
| "required to register an authentication method" | the user has no MFA method registered — account issue, not ours |
| 401 from the portal after a successful Microsoft sign-in | the address is not in `ONEFCST_ENTRA_ALLOWED_EMAILS` |
| Redirect loop | cookie/session issue, or the page was loaded over `http` |
| `/auth/config` returns HTML | old build still running |

## 8. Rollback

SSO is opt-in by configuration, so backing it out needs no code change:

```bash
# comment out ONEFCST_ENTRA_TENANT_ID / _CLIENT_ID in /srv/onefcst/.env
docker compose --env-file /srv/onefcst/.env -p onefcst up -d
```

The API returns to the shared-key gate exactly as before. The bundle still
contains MSAL but `ssoEnabled` is false, so it does nothing. To remove the
redirect entirely, rebuild without the `VITE_ENTRA_*` values.

---

## What is NOT done by this deployment

**Authorization.** Every signed-in user can call every endpoint, including
`POST /build/rebuild`, which drops and reloads the database. `Identity.groups`
and `.roles` are captured and unread. Until that gate exists, the Entra
assignment list (and the email allowlist above) is the *only* access control —
adequate for a pilot with three people, not for general release. See
`docs/SSO_TO_GOLIVE_TASKS.md` phase 4.

**A real-token test.** Everything is verified against locally-minted tokens and
a fake JWKS (39 tests, `tests/test_entra_auth.py` + `tests/test_entra_gate.py`).
The first sign-in with a genuine Microsoft token is still unproven, wherever it
happens.
