# Session Changelog — 2026-07-10

Summary of all code changes made this session, in the order they were done.
Companion to `PILOT_DEPLOY_PLAN.md` (what's still planned but not yet done),
`IMPLEMENTATION_PLAN.md` (overall architecture), and `CHANGELOG_2026-07-09.md`
(previous session). Nothing in this file has been committed to git yet —
all changes are currently in the working tree.

---

## 1. Order-suggestion demand — verify script silently dropped real items

**Files:** `app/backend/onefcst/verify/order_suggestions.py`

**Problem:** `_future_demand()` (the plausibility-check script's independent
read path — see CLAUDE.md constraint #4) only read `PlanValue.level ==
"master"`. Items whose plan data lived only at `one_row` level — mostly OS
licenses (ｿﾌﾄｳｪｱS) — got zero demand and were silently dropped from the
report entirely. Real example: `W11P` (item 2345) — production correctly
forecasts ~3.5k/mo (via `forecasting._series_by_item`'s master+one_row
merge) and suggests ordering 4,620 units; the verify script saw `[0,0,0,0]`
and skipped it. It also picked the first N *present* months rather than
the actual next N calendar months, which could silently skip real gaps.

**Fix:** `_future_demand()` now merges both levels per calendar month
(master preferred where both exist), the same rule
`forecasting._series_by_item()` already uses in production. Signature
changed to take explicit `month_dates` instead of a count.

**Verified impact (live run #3, 264 flagged items):** dropped-entirely
count fell from 11 to 1; `W11P` now correctly surfaces its suggestion.
Full test suite green after the fix.

---

## 2. Business documents — department name corrections

**Files:** `プロジェクト憲章書_部材需要予測購買支援システム 2.docx`,
`稟議書_部材需要予測購買支援システム 3.xlsx` (repo root, untracked)

**Changes**, all via `python-docx`/`openpyxl` run-level or cell edits
(verified formatting/styles/merged-cells intact after each):
- `経営企画部` → `企画部` (per correction from colleagues) — 1 occurrence
  in the charter, 1 in the ringisho; confirmed zero occurrences in the RDD.
- Ringisho attachment list (K16): referenced document versions corrected
  from "v1.1" to "v1.0", matching both documents' own actual `バージョン`
  field (both say 1.0; "v1.1" was stale).
- Charter's project-leader department: corrected an internal inconsistency
  where the cover table said 購買本部 but the project-structure table said
  デジタルイノベーション3部 for the same person — **user corrected this
  correction**: デジタルイノベーション3部 is their real department;
  reverted the wrong edit, then fixed the actual remaining inconsistency
  (cover table) to match.
- Ringisho B10 body text reworded (user-approved) to credit
  デジタルイノベーション3部 as the actual developer, 購買本部 as the
  requesting department: "本プロジェクトは、購買本部からの依頼により
  デジタルイノベーション3部担当者が内製開発する..."
- Ringisho F4 (所属) left as 購買本部 — confirmed this field pairs with
  会社・組織 above it (describes the *submitting department*, i.e. 起案
  部門, not the individual's personal department) and is consistent with
  B9's own text.

---

## 3. New feature — discontinuation-risk report

**Files:** `app/backend/onefcst/verify/discontinuation.py` (new),
`app/backend/onefcst/api/main.py`, `app/frontend/src/pages/Discontinuation.tsx`
(new), `App.tsx`, `components/Layout.tsx`, `api/client.ts`, `api/types.ts`,
`i18n/translations.ts`

**Problem:** User suspected forecasts continue for parts that are
effectively discontinued. Investigated live: the existing 6-month
`is_stale` gate and `apply_discontinuation_signal` already catch nearly
everything — found 0 false negatives for "3+ months fully dead." The real
gap: items with a sharp recent falloff (still active enough to clear the
6-month bar) that the model hasn't caught up to yet, and only NotePC items
carry an authoritative `Item.lifecycle` EOL flag — every other category
(the majority) has no such source at all.

**Built:** `GET /reports/discontinuation-risk` — flags items whose last
6 months of real demand fell under 20% of the 6 months before that, but
whose forecast still projects ≥40% of that older, already-faded volume
for the next 6 months. New frontend page (`Discontinuation.tsx`) + nav
entry under Forecast & Accuracy, same review pattern as `Mismatch.tsx`
("system surfaces, human decides").

**Verified impact:** live run currently flags 1 candidate
(`B905C WIFI CARD`, -80.6% demand decline, still forecasting 1,103 units
over the next 6 months via `trend+known_plan`).

---

## 4. Security audit + fixes

**Files:** `app/backend/requirements.txt`, `consolidation/.env` (untracked
from git)

**Audit performed:** backend (auth, CORS, SQL injection, path traversal,
secrets, input validation, dependency pinning) and frontend (API-key
handling, XSS, hardcoded secrets, SSRF, localStorage usage) — both via
independent sub-agent review. No real vulnerabilities found (timing-safe
API-key comparison, 100% ORM/no raw SQL, no path traversal, zero
`dangerouslySetInnerHTML`/`eval`, no committed real secrets). One sub-agent
action flagged by the harness as unauthorized (`cat consolidation/.env`
printed to its own transcript) — reviewed, contents were a non-secret
internal LLM server URL, not a real leak.

**Fixed:**
- `consolidation/.env` was tracked in git despite `.gitignore`'s own `.env`
  pattern (which covers it going forward) — `git rm --cached`, kept on
  disk. **Not yet committed.**
- `requirements.txt` pinned to exact currently-installed versions
  (`fastapi==0.121.0`, `uvicorn==0.38.0`, `sqlalchemy==2.0.45`,
  `openpyxl==3.1.5`, `pyxlsb==1.0.10`, `pydantic==2.12.5`) — was floating
  on every package except sqlalchemy. Verified `pip install -r
  requirements.txt --dry-run` resolves cleanly against the running env.

---

## 5. `verify/mismatch.py` — data-loss bug in the deterministic drafter

**Files:** `app/backend/onefcst/verify/mismatch.py`,
`app/backend/tests/test_mismatch.py`

**Problem:** `draft_and_store_rules()` deleted every `CompatRule` with
`relation=="fits"` regardless of `origin`/`status` before redrafting —
would silently wipe `advisory/llm_rules.py`'s LLM drafts *and* any
already-confirmed rule of either origin, the first time both drafters
were used together. `CompatRule` table was empty at the time (hadn't
bitten anyone yet).

**Fix:** narrowed the delete filter to `origin=="ai_spec_heuristic",
status=="draft"` only — mirrors the scoping `llm_rules.py` already used
for its own re-runs.

**Verified:** new regression test
(`test_draft_and_store_rules_only_clears_its_own_prior_drafts`) — seeded
one `llm`/`draft` row and one `ai_spec_heuristic`/`confirmed` row, called
the drafter, asserted both survive. Deliberately reverted the fix and
confirmed the test fails (catches the real regression) before restoring.

---

## 6. `engine/compat.py` — six real classification gaps closed

**Files:** `app/backend/onefcst/engine/compat.py`

Found via a live LLM drafting pass against the real unclassified-item
pool, then verified against public hardware specs before fixing (per
user instruction: "logically think before fixing, don't blindly put"):

1. **Threadripper CPUs** (`TR3960X`, `TR3970X`) — no Threadripper pattern
   existed in the CPU classifier at all. Added `THREADRIPPER_OVERRIDE`
   dict → `sTRX4` (same family as the existing `TRX40` chipset entry).
2. **`X399` chipset** (1st/2nd-gen Threadripper) — missing from
   `CHIPSET_SOCKET`; added → `TR4` (a genuinely distinct, older socket
   from `sTRX4` — not merged).
3. **Desktop "C"-suffix Broadwell CPUs** (`i55675C`, `i75775C`) — the
   mobile-suffix regex wrongly read trailing "C" as a laptop indicator;
   these are the only two Broadwell desktop "C" SKUs Intel ever made.
   Added `INTEL_DESKTOP_C_OVERRIDE` → `LGA1150`. One of these had already
   been sent to the LLM, which answered "Skylake"/LGA1151v1 — factually
   wrong (real: Broadwell/LGA1150) — a live-verified case of the model
   being confidently incorrect, not just uncertain.
4. **`LGA1155`** (3rd-gen Ivy Bridge) — `INTEL_GEN_SOCKET` started at
   gen4; added gen3 → `LGA1155`, fixing `i33220` (i3-3220), which the LLM
   had also misclassified as "Haswell"/LGA1150 (real: Ivy Bridge/LGA1155,
   a socket that wasn't even in the LLM's family whitelist).
5. **`W790` chipset** (Xeon-W / Sapphire Rapids-WS workstation platform) —
   missing from `CHIPSET_SOCKET`; added → `LGA4677`. The LLM had wrongly
   paired this board with consumer LGA1700 CPUs (the true socket wasn't
   in its whitelist, so it substituted the nearest-sounding option).
6. **Celeron `G5900`/`G5905`** (Comet Lake) — missing from
   `CELERON_PENTIUM_G_MAP`; added → `LGA1200`. The LLM had dismissed both
   as "not a standard CPU model" even though they're real, documented
   chips — found during a dedicated edge-case testing pass.
7. **Core Ultra no-hyphen naming** (`U5225`, `U9285K`, `U7265K`,
   `U7265KF`, `U5245KF`, `U7270KPlus`, and their `法`-suffixed corporate-
   channel variants) — `_INTEL_ULTRA_RE` required a literal hyphen
   (`U7-265K` format); this catalogue also uses the same tier without one.
   Made the hyphen optional (`-?`). The LLM also failed to recognize this
   naming pattern — confirming this needed a rule-engine fix, not a
   prompt fix.

**Net effect on live unclassified pool:** CPU 34 → 18, MB 16 → 14 (after
all fixes above; MB-side only affected by fix #2).

---

## 7. `advisory/llm_rules.py` — prompt guardrail + confidence-model change

**Files:** `app/backend/onefcst/advisory/llm_rules.py`,
`app/backend/tests/test_llm_rules.py`

**Prompt guardrail added:** explicit instruction to identify the item's
REAL socket first, then check whether that exact name is in the allowed
list — if not, answer "unknown," never substitute the nearest-sounding
option. Targets the exact failure mode found in fixes #4 and #5 above
(the model guessing a plausible-but-wrong family when the true answer
wasn't offered).

**Confidence model changed** (explicit, informed user request after I
described the tradeoff): `LLM_CONF_CAP`/`CONF_MAP` (fixed 0.65/0.5 values,
capped below the 0.9 bulk-confirm threshold by design) removed entirely.
Confidence is now the model's raw 0.0–1.0 self-report, stored exactly as
given — **no ceiling**. Documented tradeoff in the module docstring: a
confidently-wrong LLM answer (demonstrated real, see fix #5) can now be
swept in by bulk-confirm without individual review, where it couldn't
before. `origin='llm'` + the model's stated reasoning remain visible to
any reviewer who looks.

**Tests:** rewrote all 6 pre-existing tests for numeric confidence,
deleted the one asserting the now-removed cap, added one documenting the
new uncapped behavior explicitly, plus 10 new edge-case tests (fake
transport, zero API cost): hallucinated/duplicate names, invalid/out-of-
range confidence values, malformed response rows, markdown-fenced JSON,
batching >25 items, empty-side handling, LLM×LLM cross-pairing at weakest
confidence, no same-category pairing, family-whitelist correctness.
17 tests total in this file, all passing; 2 spot-checked by deliberately
breaking the code and confirming the test fails first.

**Live-verified** (real OpenAI calls, real measured cost): confirmed
genuine confidence spread post-change (1.0 / 0.9 / 0.0 across different
item types in one real batch — laptop CPUs correctly "unknown" at 0.90,
non-hardware junk at 0.00, genuine facts at 1.0). Total real API spend
across all live testing this session: roughly 4,500 input + 6,200 output
tokens, under $0.005 total at gpt-4o-mini's published rates.

---

## 8. New feature — edit action for draft compatibility rules

**Files:** `app/backend/onefcst/advisory/rules.py`,
`app/backend/onefcst/api/main.py`, `app/backend/onefcst/api/schemas.py`,
`app/backend/tests/test_advisory_rules.py` (new),
`app/frontend/src/pages/CompatReview.tsx`, `api/client.ts`,
`i18n/translations.ts`

**Built:** `PATCH /compat-rules/{id}` (`advisory.rules.edit_rule`) — lets
a human correct a draft rule's `relation`/`confidence`/`note` before
deciding confirm/reject. Only allowed on `status='draft'` rows — editing
an already-confirmed/rejected rule raises `RuleNotEditable` (409): a
settled decision shouldn't be silently rewritten; reject and record a new
rule instead. Edits to `note` are appended, never overwrite the model's
original reasoning, so a reviewer can always see both. Full audit-log
entry per edit (who, before/after), same pattern as confirm/reject.

Frontend: inline edit form on each draft row (relation/confidence/note
fields + Save/Cancel), shows the AI's original reasoning alongside the
edit fields while open.

**Verified:** 8 new backend tests (field updates, note-append behavior,
locked-rule rejection, missing-rule handling, edited draft can still be
confirmed afterward with the edit intact) — all passing. Live smoke-test
via curl against real data (rule id 1): confirmed the 409 guard fires on
an already-confirmed rule, confirmed a successful edit round-trips
correctly, cleaned up test data afterward so the real review queue was
left untouched.

---

## 9. Mismatch gate — confirming a rule now has a real, visible effect

**Files:** `app/backend/onefcst/verify/mismatch.py`,
`app/backend/tests/test_mismatch.py`, `app/frontend/src/pages/Mismatch.tsx`,
`api/types.ts`, `i18n/translations.ts`

**Problem (user question: "what will happen if I confirm here"):** traced
`compute_mismatch()` and found it never read the `CompatRule` table at
all — socket classification came purely from the deterministic
`engine/compat.py` tables, live, every call. Confirming a rule in
`CompatReview.tsx` only updated status/audit-log; it had **zero** effect
on the mismatch report, forecasts, or order suggestions. This directly
contradicted the module's own docstring, which already (incorrectly)
claimed confirmed rules were used.

**Fix:** added `_confirmed_partner_family()` — for an item the
deterministic table can't classify, checks for a **confirmed** (never
draft) `CompatRule` pairing it with a partner whose own family *is*
determinable. Only trusts a family when every confirmed partner agrees;
conflicting confirmed pairs stay unclassified rather than guessed at
(same principle as the LLM prompt guardrail in §7). Wired into both
`compute_mismatch()` (the live gate) and `historical_scan()` (the
acceptance-test backtest) — a part's physical socket doesn't change over
time, so the fallback applies consistently across both. New
`confirmed_rule_classifications: {cpu: [...], mb: [...]}` field on the
report, surfaced in a new "Classified via a confirmed rule" card on
`Mismatch.tsx`.

**Verified two ways:**
- 3 new backend tests with synthetic demand data: confirmed rule → counts
  toward partner's socket; draft rule → does NOT count; conflicting
  confirmed rules → stays unclassified. Deliberately broke the fix,
  watched a test fail with `StopIteration` (no group formed), restored it.
- **Real live data**: found the user had already confirmed 4 real
  `pG4560` rules via the browser during this session. The mismatch report
  correctly resolved `pG4560`'s family to `LGA1151v1` via the new
  fallback. No new visible socket group appeared in the report — verified
  honestly why: `pG4560` (a 2017 Kaby Lake Pentium) has zero real demand
  data past 2020-07, so there's nothing to compare even though the
  classification now flows through correctly. Confirmed via screenshot
  (see below) that the new "Classified via a confirmed rule" card
  correctly shows the `pG4560` pill live in the running app.

---

## Verification performed

- Full backend test suite: **192/192 passing** (169 carried over from
  2026-07-09 + 23 new/changed this session: 1 mismatch data-loss
  regression test, 3 mismatch confirmed-rule tests, 17 llm_rules.py tests
  — 6 rewritten + 1 new + 10 edge-case, 8 advisory/rules.py edit tests,
  minus overlaps).
- Frontend typecheck (`npx tsc --noEmit`): clean.
- Real screenshot of the running app obtained mid-session (installed
  missing Chromium shared libraries — `libnspr4`, `libnss3`,
  `libasound.so.2` — via `conda install -c conda-forge`, no sudo needed,
  after the user declined a sudo `apt-get` install) — visually confirmed
  the Mismatch page's new "Classified via a confirmed rule" card and all
  4 live socket-demand groups render correctly with real data.
- Multiple live end-to-end verifications against the real running app
  (both servers, real DB, real OpenAI calls) throughout, not just unit
  tests — order-suggestion counts, LLM classification accuracy (2/5 wrong
  before rule-engine fixes, 0/2 wrong after), edit-endpoint round-trip,
  confirmed-rule mismatch-gate wiring.

## Not yet done / open items

- `consolidation/.env` untrack (`git rm --cached`) is staged but **not
  committed** — user has not yet been asked to confirm the commit.
- 46 real `origin='llm'` draft rules (from `pG4560`/`i7-9700K`
  classifications) remain in the review queue awaiting confirm/reject —
  4 already confirmed by the user during this session.
- The Core Ultra no-hyphen regex fix (§6.7) and the other rule-engine
  fixes are unit-tested but not yet covered by a dedicated
  `test_compat.py` — `engine/compat.py` currently has no direct test file
  of its own (all coverage is indirect, via `test_mismatch.py` and
  `test_llm_rules.py`).
- Self-consistency voting (multi-call LLM confidence, discussed but not
  built) remains an open option if the uncapped confidence model (§7)
  proves too noisy in practice — costs 2-3x more per drafting run.
- Requirements-workbook documentation of the AI-drafting capability
  (originally planned as a step in the AI-compat-matching plan) was
  superseded by the actual work done in §5-§9 above; not yet revisited.
- Awaiting responses from IT (NAS whitelisting) and the planning
  department (June actuals confirmation in 部品予測) for the separate
  WAPE re-verification task — unrelated to, and not blocking, the work
  in this changelog.
