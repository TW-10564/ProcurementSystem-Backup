# Pilot Deployment Plan — POC → Real Deployment Transition

Companion to `IMPLEMENTATION_PLAN.md` (build architecture) and `CLAUDE.md`
(project context). This file tracks what has to change before `app/` moves
from "POC on a dev machine" to a real pilot deployment with real users
making real adjustments. Written 2026-07-09, after a full-system audit
(data integrity, forecast accuracy, backend security, frontend security)
plus five bugs found and fixed in that audit.

Everything below is organized by whether it blocks pilot go-live, or can
follow shortly after. **Nothing here has been implemented yet unless
explicitly marked ✅ DONE** — this is a plan, not a changelog, except for
the short list of fixes already shipped this session (kept here for
continuity so the next person doesn't re-discover them).

---

## Already fixed this session (✅ DONE, for reference)

| Fix | File | Why it mattered |
|---|---|---|
| SAP stock matching keyed on SYOCODE (part code) primarily, あだ名 fallback | `ingestion/sap.py`, `build.py` | Recovered ~415 more resolved stock rows (566 via SYOCODE vs ~41 via name-crosswalk before); directly improves order-suggestion accuracy |
| Seasonal-forecast decline gate (year-over-year, not all-time-peak) | `engine/baseline.py` | Fixed a real case (TFX300, PSU) where `seasonal` won its own hold-out then scored 1171% WAPE out-of-sample; portfolio-wide WAPE 99.44%→98.37% |
| "B650" cross-system alias collision — a PSU's own 綽名 and a motherboard's planning nickname were the same string; `resolve()`'s lowest-id tie-break silently routed the motherboard's Tier-1 sales-plan data to the unrelated PSU | `registry/build.py` (`resolve_via_source`), `build.py` (`_resolve_planning1`) | Real financial-downside bug: an active Tier-1 (planning-owned) category's demand signal was going to the wrong item |
| ④ﾏｽﾀ plan-name alias resolution now uses the same normalization fallback every other ingestion path uses | `registry/build.py` (step 4) | Recovered 7 of 11 previously-unresolved rows (pure case/spacing mismatches) |
| Frontend export/download buttons now go through the authenticated API client instead of raw `<a href>`/`fetch()` | `api/client.ts` (`downloadReport`), 5 page files | Every export button and two Accuracy report cards would have silently 401'd the moment `ONEFCST_API_KEY` is set — i.e. the moment anyone follows the standard "don't deploy off localhost" advice |
| `Notifications.tsx` missing `.catch` on two API calls | `pages/Notifications.tsx` | Backend errors rendered as silent empty state instead of the error banner every other page shows |
| API-key comparison now constant-time (`hmac.compare_digest`) | `api/auth.py` | Removed a theoretical timing side-channel on the shared secret |

All verified: 169/169 backend tests pass, frontend typechecks clean (one
pre-existing, unrelated `Forecast.tsx` type error was confirmed present
before this session's changes too — not introduced by this work).

---

## 🔴 Blockers — must fix before pilot go-live

### 1. Every `onefcst.build` run destroys all human-entered data — ✅ DONE

**Fixed via `onefcst/rebuild_preserving.py`** (new module, wired into
`scheduler.py`'s weekly loop in place of a bare `build.run()` call).
Rather than the natural-key migration originally scoped here (real schema
surgery — `Item.id` is still a plain autoincrement column, unchanged),
this takes the snapshot/translate/restore approach flagged as the interim
stopgap: `rebuild_preserving.run()` wraps `build.run()`'s full
drop+recreate by (1) snapshotting every human-entered table before the
rebuild — `Adjustment`, `CompatRule`, `ForecastRun`/`Forecast`,
`AccuracyScore`, `PlannerSnapshot`, `AuditLog` (which `Notifications.tsx`
reads as a view over, per `advisory/notifications.py` — there's no
separate `Notification` table), (2) running the real rebuild, then
(3) re-inserting the snapshot rows translated onto the new `Item.id`s via
each item's `canonical_name` (綽名 — the stable natural key across
rebuilds, per the original diagnosis). Rows whose item no longer exists
after the rebuild are dropped and counted (`dropped: {...}` in the
returned report), not silently kept with a dangling `item_id`.
`ForecastRun.created_at` is preserved verbatim (not just re-ordered)
because `verify/fva.py` resolves `run_id=None` adjustments to "whichever
run was live at the time" by comparing timestamps — shifting them would
silently repoint old adjustments at the wrong historical run. `AuditLog`
has no item FK (`before`/`after` are free-text JSON, not references) so
those rows carry over verbatim with no drop case.

**Verified:** `tests/test_rebuild_preserving.py` (3 new tests) exercises
`_snapshot`/`_restore` directly against an in-memory DB, simulating a
rebuild that deliberately reassigns item ids in a different order than
original creation (proving the `canonical_name` translation — not
insertion-order luck — is what makes restore work): adjustment/compat-
rule/audit-log/accuracy-score/planner-snapshot survival, dangling-row
drop-and-count behavior for items removed by the rebuild, and audit-log
carrying over even when zero items exist. Full backend suite green
(195/195) after landing.

**Still open:** this is the snapshot/restore stopgap, not the originally-
scoped `Item` primary-key migration — acceptable for pilot scale (data
volume is small, `_restore` runs synchronously inside the weekly
scheduler cycle) but worth revisiting if item counts grow enough to make
snapshot/restore slow, or if a future change adds another human-entered
table that needs the same treatment (grep `rebuild_preserving.py` for the
model list before assuming a new table is automatically covered).

### 2. Auth is disabled by default
`api/auth.py`'s `require_api_key` no-ops when `ONEFCST_API_KEY` is unset —
the documented dev default. **Before any pilot deployment reachable by
more than one trusted machine, set `ONEFCST_API_KEY`** on the backend and
`VITE_API_KEY` (matching value) on the frontend build.

Known limitation to accept or address: `VITE_API_KEY` gets compiled into
the frontend's static JS bundle in plaintext (Vite's standard behavior for
`VITE_`-prefixed vars) — anyone who can view-source the deployed page can
read the key. For a pilot on a private/internal network with a small,
known user group, a shared secret is probably an acceptable stopgap; it
is **not** sufficient once the pilot's user base grows or the app is
reachable from outside a trusted network. Real multi-user auth (the
`author` field already tracks who-did-what for adjustments/compat
confirmations, but nothing currently verifies the claimed identity) is a
post-pilot upgrade, already noted as such in `api/auth.py`'s own docstring.

### 3. CORS is wildcarded
`api/main.py:46` — `CORSMiddleware(allow_origins=["*"], allow_methods=["*"], ...)`.
Restrict `allow_origins` to the actual pilot frontend's origin(s) before
deployment. Trivial change, high value once auth is also enabled (an open
CORS policy undermines a shared-secret auth model less than it would a
cookie-based one, but there's no reason to leave it wildcarded once the
real origin is known).

### 4. No HTTPS / reverse proxy / process management
Both servers currently run as bare foreground/background processes
(`uvicorn ... --host 0.0.0.0`), per the `CLAUDE.md` "how to run" section —
appropriate for local dev, not for a pilot:
- No TLS termination — `X-API-Key` and all data currently travel in
  cleartext over the network the moment this isn't `localhost`. Put a
  reverse proxy (nginx/Caddy) in front with a real certificate before any
  non-localhost pilot use.
- No process supervisor — nothing restarts the backend/frontend if they
  crash or the host reboots. Needs systemd units (or equivalent) or a
  container/orchestration setup.
- No documented deployment target (bare metal / container / cloud) yet —
  needs a decision before the above can be finalized.

---

## 🟡 High priority — data quality, before or during early pilot

These don't block turning the lights on, but pilot users will be looking
at these numbers immediately, so they should be resolved or clearly
caveated in the UI before real people start trusting them.

### 5. Gate A reconciliation: 62 item-months flagged, 53% concentrated in CPU
`verify/reconciliation.py`'s report (`/reports/source-reconciliation`) shows
master vs sub-file disagreement >15% on 20 items, split 42
`methodology_disagreement` / 15 `likely_formula_bug` / 5 `insufficient_data`.
The CPU concentration (33 of 62) is a systemic signal, not scattered noise
— worth a planning-side review of CPU-specific source data before pilot
users rely on CPU forecasts specifically. The module already classifies
these correctly; this is a data-review task, not a code fix.

### 6. Known-weak forecast categories: SSD, HDD(内蔵), 電源S(partial), ファンN, グラフィックカード
Backtested and root-caused this session — SSD and HDD are genuinely noisy/
low-volume categories where a 6-month hold-out has limited signal (not a
code defect); ファンN/グラフィックカード misses are noise-level (<2pt).
電源S was substantially improved by the seasonal-decline-gate fix but
still has some residual weakness. **Recommendation for pilot: surface a
confidence/reliability indicator per category in the dashboard** (the data
already exists — `holdout.by_category`'s `wape` vs `naive_wape` — it's
just not currently rendered anywhere in the frontend) so pilot users don't
treat every category's forecast as equally trustworthy.

### 7. Residual unresolved data (documented, monitor don't chase)
- ①販売計画: 369/1175 rows unresolved — confirmed structural (case/chassis
  color variants with no 綽名 counterpart in the source file), not a bug.
- SAP: 150 rows still unnamed after this session's SYOCODE fix — confirmed
  floor (no あだ名, no SYOCODE hit, no crosswalk hit).
- `cross_system_alias_conflicts`: 138 found, ~all duplicate `商品コード`
  rows (expected/benign) — **but this is exactly the category the B650 bug
  came from.** Worth a periodic (e.g. per-rebuild) automated check that
  flags any *new* cross-system conflict for human review, rather than only
  discovering the next one by accident.

---

## 🟢 Medium priority — hygiene, can follow shortly after go-live

- **`bulk_confirm`'s `author` param has no identity verification** — same
  root cause as auth item #2 above (no real user auth yet). Low risk with
  a small trusted pilot group; revisit alongside real multi-user auth.
- **Dependency vulnerability scan not yet done** — `requirements.txt` has
  no pinned versions (`fastapi`, `uvicorn`, `openpyxl`, `pyxlsb`,
  `pydantic`, `sqlalchemy>=2.0`). Pin versions and run a scanner
  (`pip-audit` or equivalent) before pilot; unpinned deps mean the exact
  code running in the pilot isn't reproducible from this repo alone.
- **No structured logging/monitoring** — the app currently communicates
  state via `print()` (build/forecast run output) with no log aggregation,
  error alerting, or uptime monitoring. Fine for one developer watching a
  terminal; not fine for a pilot where something breaking needs to be
  noticed without someone actively looking.
- **`_report_cache` (api/main.py) is an unsynchronized dict** — harmless
  today (single-process `uvicorn`), becomes a real (if low-severity) race
  the moment the backend moves to multi-worker/threaded mode.

---

## Suggested order of work

1. **Blockers #2–4** (auth key, CORS allow-list, reverse
   proxy/TLS/process manager) — these are all small, well-understood
   changes with no design decisions required, and nothing else matters if
   the pilot can't be safely reached over a network.
2. ~~**Blocker #1** (rebuild-safe persistence)~~ — ✅ DONE (snapshot/restore
   stopgap via `rebuild_preserving.py`, see above; the full natural-key
   migration remains optional future work, not a pilot blocker anymore).
3. **Item #5** (CPU reconciliation review) — hand off to planning/domain
   experts; not an engineering task.
4. **Item #6** (per-category confidence indicator in the dashboard) —
   small frontend addition, meaningfully improves pilot user trust
   calibration, worth doing before go-live rather than after.
5. **Medium-priority items** — can trail into the pilot period itself,
   revisit based on what the pilot actually surfaces.

## Verification checklist before flipping the pilot live

- [ ] `ONEFCST_API_KEY` set on backend, matching `VITE_API_KEY` on frontend build
- [ ] `CORSMiddleware(allow_origins=[...])` restricted to real frontend origin
- [ ] Reverse proxy with valid TLS cert in front of both services
- [ ] Backend + frontend under a process supervisor (auto-restart, boot-start)
- [x] Rebuild-safe persistence for `Adjustment`/`CompatRule`/`AuditLog`/
      `AccuracyScore`/`ForecastRun`/`Forecast`/`PlannerSnapshot` landed
      (`rebuild_preserving.py`, wired into the scheduler's weekly cycle) —
      run `onefcst.rebuild_preserving` (not bare `onefcst.build`) for any
      pilot-time source-data refresh
- [ ] CPU reconciliation mismatches reviewed by planning/domain owner
- [ ] Per-category confidence indicator visible in the dashboard
- [ ] `requirements.txt` pinned + dependency scan run
- [ ] Full backend test suite passing (`python3 -m pytest tests/ -q`)
- [ ] Frontend typecheck clean (`npx tsc --noEmit`) — note the one
      pre-existing `Forecast.tsx` error unrelated to this plan should be
      fixed too, just not treated as a pilot blocker by itself
