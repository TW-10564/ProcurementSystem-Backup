# UAT Test Cases — ONE Forecasting & Procurement Portal

> **Test environment:** https://one-forecast.twave.co.jp (sign in with your own
> twave.co.jp Microsoft account — Entra SSO)
> **Document status:** Draft for UAT execution
> **Date:** 2026-08-03
> **Scope:** every user-facing flow, from sign-in through every page's write
> actions, plus the audit trail that records who changed what.

---

## 0. How to use this document

1. Each test case has a **Test ID**, **Steps**, **Expected Result**, and blank
   **Actual Result / Pass–Fail / Tester / Date** columns for you to fill in
   while testing.
2. Section 8 assigns each functional area to the person who owns those parts
   in `deploy/roster.csv`. Run the tests in **your own section** first, then
   optionally spot-check one or two cases from another section as a second
   pair of eyes.
3. **Report every failure**, but also report anything in Section 1
   ("things to expect — not bugs") if you see the *opposite* of what's
   described there — that would mean the system's actual behavior has
   drifted from what this document assumes.
4. Log in as **yourself** (your own twave.co.jp account) for every test — do
   not share logins. Part of what's being tested is that actions are
   correctly attributed to the real person who did them.

---

## 1. Read this first — known behavior, not bugs to report as surprises

These are things the current build actually does. Testers should confirm
they still behave this way, but should **not** file them as new bugs — they
are documented here so nobody spends time chasing something already known.
If you see the deployment behave differently from what's written here (in
either direction), that itself is worth flagging.

- **No incoming purchase orders are modeled.** Every order suggestion assumes
  zero stock is already on the way. Always cross-check purchasing's own PO
  tracker before ordering — the system will never subtract a PO that hasn't
  arrived yet.
- **The UI does not visibly restrict any button by role.** Every signed-in
  user sees the same buttons everywhere (Rebuild from NAS, Recalculate,
  Confirm/Reject, Bulk-confirm, etc.), regardless of whether their account is
  admin/purchasing/planning/none. The *only* place a permission difference
  shows up is **after** clicking — some actions then succeed or fail
  depending on role. This is expected for this release; Section 3 test
  cases exist specifically to verify what actually happens per role.
- **Error messages are inconsistent in detail.** When an action is rejected:
  - On the **Adjustments page** and the **order-action controls** (mark
    ordered / confirm received / dismiss), you'll see the real reason (e.g.
    "X is outside your assigned parts (owned by: MEM)").
  - On the **Forecast page's "Recalculate"/"Rebuild from NAS"/"Tune"**
    actions, a rejection shows only a generic message like "Request failed
    with status code 403" — not the specific reason.
  - On **Compat Review** and **Discontinuation Review**'s Confirm/Reject
    buttons, a rejection currently shows **nothing at all** — the button's
    spinner just clears and the row stays as it was. If this happens, it is
    the current (documented) behavior, not a crash.
- **Some write actions have no server-side permission check yet** (any
  signed-in user, including a role of "none", can currently do these):
  individual Confirm/Reject/Edit of a single compatibility rule, the
  "AI-draft unclassified parts" button, and Discontinuation
  Confirm/Reject/order Confirm-received/Dismiss/Extend. Rebuild from NAS,
  Recalculate, Maintenance toggle, Bulk-confirm, and Access management ARE
  admin-only and enforced. Adjustments and "mark as ordered" ARE scoped to
  the caller's own assigned parts. Section 3 asks you to verify this
  concretely with a non-admin account, per action.
- **There is no in-app screen to grant roles or assigned parts.** `Profile`
  only shows your own identity (read-only) — it is not where access is
  managed. Access changes are made by an administrator outside the app
  (via `deploy/roster.csv` + a backend script, or a direct API call).
- **The audit-log Excel export has no button in the app.** The Notifications
  page shows the same history on-screen with search/filter, but downloading
  it as an Excel file (for a monthly report) currently requires an
  administrator to call the export URL directly — there is nothing to click
  in the UI for it.
- **Signing out does not end your Microsoft session.** "Sign out" clears
  this app's own session but leaves you signed into Microsoft in the
  browser. On a **shared PC**, the next person who clicks "Enter the portal"
  may be silently signed back in as you, with no prompt. Always fully close
  the browser (or sign out of Microsoft separately) on a shared machine.

---

## 2. Roles, at a glance

| Role | Can do | Notes |
|---|---|---|
| **admin** | Everything, on every part: rebuild from NAS, recalculate forecasts, toggle maintenance mode, bulk-confirm compat rules, grant/revoke access. | 購買本部 admins: 内田暁久, 粕壁一輝, 大貫勝男. 企画部 admins: 筒井愼平, 久末翔希. |
| **purchasing** | Record adjustments and order actions (mark ordered / confirm received / dismiss) **only on the ONE sub-file parts they personally own** (`deploy/roster.csv` 担当パーツ column). | See Section 8 for the actual name-to-parts mapping. |
| **planning** | Record adjustments across **all** CPU / グラフィックカード(VGA) / マザーボード(MB) / PCケース(Case) items, regardless of which sub-file they came from — the four categories 企画部 plans. | All 企画部 non-admin staff (see Section 8). |
| **(none)** | Can sign in and read everything (every forecast, every audit-log entry) but every write should be rejected. | Anyone signed in but not yet granted a role. |

**Reads are never restricted** — everyone can see every part's forecast and
every audit-log entry, on purpose (so a colleague can cover for someone who's
out). Only writes differ by role.

---

## 3. Section A — Sign-in and session (everyone runs this section first)

| ID | Steps | Expected Result |
|---|---|---|
| A1 | Open `https://one-forecast.twave.co.jp` in a private/incognito window. Click "ポータルに入る / Enter the portal". | Redirected to Microsoft's own sign-in page (not a form on our own site). |
| A2 | Sign in with your twave.co.jp account. | Redirected back into the app, landing on the page you originally tried to reach (or `/app` if you started from the landing page). |
| A3 | Once signed in, open your browser's dev tools or ask IT to check the `GET /me` response (or check the Profile page loads your real name/department). | Your name, email, and department shown match your real Entra/Azure directory entry. If checking `/me` directly: `source` field reads `"entra"`, not `"api-key"`. |
| A4 | Go to the **Profile** page. | Shows your name, email, job title, department, office location as set in Azure — fields Azure doesn't have set for you show as "—", not blank/hidden. Confirm there is **no** button here to change your own role or assigned parts (expected — access is admin-managed outside the app). |
| A5 | Stay signed in and leave a tab open past a normal work session (several hours); come back and click something that loads data. | You're transparently redirected through Microsoft and back to the same page (should not need to type credentials again if your Microsoft session is still alive) — OR, if your Microsoft session has also expired, you get a real interactive sign-in prompt. Record which happened. |
| A6 | Click "Sign out" (if present) then immediately click "Enter the portal" again on the same machine, without closing the browser. | Per Section 1: you may be signed back in without a prompt (Microsoft session survives). This is expected — flag it only if you see a *different* account's data. |
| A7 | (If you have a test account without Entra "assignment required" access, or ask IT to attempt this) — try signing in with a twave.co.jp account that has NOT been assigned to this application in Entra. | Sign-in is blocked before reaching our app (Microsoft's own error page), OR our own generic error page appears. Record exactly what you see — this is a known thin spot (Section 1's "no branded rejection screen" note). |

---

## 4. Section B — Dashboard (everyone)

| ID | Steps | Expected Result |
|---|---|---|
| B1 | Sign in, land on the Dashboard. | KPI tiles show: forecast accuracy (WAPE%), HIGH mismatch count, MEDIUM mismatch count, pending compat-rule drafts — all populate within a few seconds. |
| B2 | Click each "Quick action" (Review demand forecast / CPU-MB mismatch / Review compatibility rules / Adjust forecasts manually). | Each navigates to the correct page (`/forecast`, `/mismatch`, `/compat`, `/adjustments`). The mismatch/compat tiles are tinted red/amber when there are HIGH/MEDIUM mismatches or pending drafts. |
| B3 | Check the "要対応 / Needs attention" card. | Lists HIGH/MEDIUM mismatch groups, most severe first. "View all" opens the full Mismatch page. |
| B4 | Check the "監査ログ / Audit log" card. | Shows the 6 most recent recorded actions (who / what / when). "View all" opens the full Notifications page. |
| B5 | Check the "精度検証 / Accuracy validation" card. | Read-only table of backtest WAPE by period/method — no buttons, just data. |
| B6 | With the backend intentionally stopped or unreachable (ask an admin to simulate, or test right after a deploy), reload the Dashboard. | A clear red error banner appears instead of a blank/broken page. |

---

## 5. Section C — Forecast page

**Run C1–C5 as an admin. Run C6–C10 as a purchasing or planning user, once
with a part you own/plan and once with a part you don't.**

| ID | Steps | Expected Result |
|---|---|---|
| C1 (admin) | Click "NASから再取込み / Rebuild from NAS". | Button becomes disabled and reads "Rebuilding (~4 min)…"; a progress banner appears with an elapsed/estimated timer and a progress bar. Open the Forecast page in a **second browser tab** while it's running — the second tab should show the same in-progress timer (not start over from zero). |
| C2 (admin) | Wait for the rebuild to finish. | A green "✅ NAS re-import complete" banner appears, telling you to click Recalculate to apply it. Click "再取込み履歴を表示 / Show rebuild history" — your rebuild appears with trigger=manual, status=done. |
| C3 (admin) | Click "予測を再計算 / Recalculate forecast". | Button disables while running; on success, a banner shows "Forecast updated (run #N)" and the list/KPIs refresh with new numbers. |
| C4 (admin) | Use the export dropdown to pick each of the three scopes (全部材/All, 企画4部材/Planning tier-1, 購買部材/Purchasing tier-2) and click "Excel出力 / Export to Excel" each time. | Each produces a downloadable `.xlsx` file named appropriately for the chosen scope, with no confirmation dialog. |
| C5 (**non-admin** — purchasing or planning account) | Click "Rebuild from NAS", then separately "Recalculate forecast". | Per Section 1: expect these to be **rejected** (you're not admin) but the on-screen message will likely be generic ("Request failed with status code 403"), not a friendly explanation. Record exactly what appears. |
| C6 (purchasing/planning) | Open the detail panel for a part **you own/plan**. Find a future month, click "調整 / Tune", enter a new value and a reason, save. | Saves successfully; the monthly table shows your adjusted value (amber) with a tooltip crediting your name and reason; the audit trail (Notifications / Adjustments page) shows a new entry attributed to you. |
| C7 (purchasing/planning) | Repeat C6 but leave the reason blank, or type a non-numeric value. | Blocked client-side with an inline message ("please choose a target item" / invalid value / reason required) — no request sent. |
| C8 (purchasing/planning) | Open the detail panel for a part **you do NOT own/plan** (e.g., a purchasing buyer trying a part outside their assigned sub-files) and attempt "Tune". | Per Section 1: expect rejection, but the message shown is the **generic** HTTP error, not the friendly "outside your assigned parts" text (that friendly text only appears on the separate Adjustments page — see Section 6). Record what you actually see. |
| C9 (any role) | Click the "発注が必要 / Orders needed" KPI tile to open the Order Suggestions panel. Search for an item, toggle "show already-ordered items". | Panel opens, scrolls into view; search filters the table; toggle shows/hides already-ordered rows. |
| C10 (purchasing, own part with a suggested order) | Click "発注済みにする / Mark as ordered" on a row for a part you own, fill in quantity/date/note, save. Then click "受領確認 / Confirm received" once it shows as ordered. | Both actions succeed; status badge updates; the change appears in the audit trail attributed to you. |

---

## 6. Section D — Adjustments page

Run as a **purchasing** user (test both your own part and a part you don't
own) and as a **planning** user (test a Tier-1 part: CPU / VGA / MB / Case).

| ID | Steps | Expected Result |
|---|---|---|
| D1 | Open Adjustments. Search for a part you own/plan, pick a month, leave scope at SKU-level, enter a new value and a reason, submit. | "Saved" message; the new row appears at the top of the audit trail below with your name as author (read-only, pulled from your signed-in identity — you cannot type a different author under SSO), old→new value, % delta, reason, timestamp. |
| D2 | Try submitting with the reason field empty. | Blocked with "A reason is required (for accountability)" — no request sent. |
| D3 | Try submitting with no item selected. | Blocked with "Please select a target item." |
| D4 | Submit an adjustment for a part **outside** your scope (a purchasing buyer picking a part owned by a different buyer, or a category outside CPU/VGA/MB/Case for a planning user). | Rejected with a specific, readable reason — e.g. "X is outside your assigned parts (owned by: MEM)" for purchasing, or naming the four planning categories for planning. This is the page where the **real** reason should be visible (contrast with Forecast's Tune, Section C8). |
| D5 | Click "Excel出力 / Export to Excel" on the adjustment history. | Downloads a spreadsheet of the adjustment audit trail. |
| D6 | Click an item name in the adjustment history list. | Deep-links to the Forecast page with that item's detail panel already open. |

---

## 7. Section E — Mismatch page (CPU/MB/VGA-relevant buyers + planning; read-only)

| ID | Steps | Expected Result |
|---|---|---|
| E1 | Open Mismatch. | KPI tiles show HIGH/MEDIUM/LOW counts and a severity bar; per-socket cards show CPU demand vs MB demand with a gap and gap %. |
| E2 | Use the severity filter (All/HIGH/MEDIUM/LOW). | List filters instantly, no reload. |
| E3 | Check the "確定済み分類 / Confirmed classifications" card. | Lists CPU/MB parts whose compatibility rule has been confirmed (should match what you've confirmed in Compat Review, Section G). |
| E4 | Click "Excel出力 / Export to Excel". | Downloads the mismatch report as a spreadsheet. |
| E5 | Confirm there is nothing to click that changes data on this page. | Entirely read-only except the Excel export — no save/confirm/reject actions live here. |

---

## 8. Section F — Accuracy page (planning + admin; read-only)

| ID | Steps | Expected Result |
|---|---|---|
| F1 | Open Accuracy. | "Verdict" banner shows system-vs-planner WAPE for a matched month, colored by winner. |
| F2 | Check the FVA ("did the human adjustment help") card. | Shows helped/hurt counts as a stacked bar, or a "nothing scoreable yet" note if there's no data yet. |
| F3 | Check the SAP↔ONE stock reconciliation and sales reconciliation tables. | Both load independently — if one source is temporarily unavailable, only that section shows a load-failed message, the rest of the page still works. |
| F4 | Confirm this page has no buttons that change anything. | Entirely read-only. |

---

## 9. Section G — Compat Review (buyers responsible for CPU/MB/VGA/RTX parts + planning + admin)

| ID | Steps | Expected Result |
|---|---|---|
| G1 | Open Compat Review, "draft" status tab. Enter your name in the reviewer field (if not already filled from SSO). | Draft rules list with confidence scores; count badges on draft/confirmed/rejected tabs. |
| G2 | Click "承認 / Confirm" on a draft rule. | Rule moves to "confirmed"; recorded in the audit trail. |
| G3 | Click "却下 / Reject" on a draft rule, and in the prompt, **cancel** it (don't type a reason). | Nothing happens — no request sent (this is by design; only proceeds once you type a reason). |
| G4 | Click "却下 / Reject" again, this time typing a reason. | Rule moves to "rejected"; audit trail records the reason. |
| G5 | Click "編集 / Edit" on a draft rule, change the relation text/confidence/note, save. | Rule updates; if someone else already confirmed it in the meantime, expect a conflict error (409) rather than a silent overwrite. |
| G6 (**as a non-admin** account) | Set the confidence threshold, click "しきい値以上を一括承認 / Bulk-confirm at/above threshold", confirm the dialog. | Per Section 1: this is admin-only; expect it to fail, but there's **no error handling on this button at all** — record exactly what you see (most likely: nothing visibly changes after the confirm dialog closes). |
| G7 (**as a role-less/no-role account**, if you can arrange one for testing) | Try Confirm/Reject/Edit on an individual draft rule. | Per Section 1, these currently have **no server-side permission check** — expect them to succeed even for an account with no assigned role. Confirm this is still the case; if it now correctly blocks you, that's worth reporting as a change from this document. |
| G8 (admin) | Click "AI下書き生成（未分類部材）/ AI-draft unclassified parts". | Shows a summary of classified/drafted/needs-review/skipped counts, or a clear "cannot reach the LLM server" message if the LLM endpoint is down. |
| G9 | Click "Excel出力 / Export to Excel" for each status tab. | Downloads the compat-rules list for that status. |

---

## 10. Section H — Notifications (audit log) page (everyone)

| ID | Steps | Expected Result |
|---|---|---|
| H1 | Open Notifications. | Shows a paginated feed of every recorded action across the whole system (not just your own), newest first, grouped by day. |
| H2 | Filter by author (chip list), then by free-text search, then by event kind (Adjustments / Approvals / Warnings / System). | Each filter narrows the list correctly; combining filters works (e.g. one author + one kind). |
| H3 | Click "さらに表示 / Load more". | Loads the next 50 entries without the list jumping around or duplicating rows already shown. |
| H4 | Cross-check: find an action you performed in an earlier section (an adjustment, a compat-rule confirm, an order marked) in this feed. | It appears here, attributed to your real name/email — confirming the audit trail captured it correctly. |
| H5 (admin, via direct API URL — there is no button for this) | Fetch `https://one-forecast.twave.co.jp/export/audit-log` (optionally with `?year=2026&month=7`) directly. | Downloads an Excel file of the audit log (last 500 entries, or the specified month with author/department resolved). Confirm this matches what's shown on-screen in Notifications for the same period. |

---

## 11. Section I — Discontinuation Review (buyers + planning + admin)

| ID | Steps | Expected Result |
|---|---|---|
| I1 | Open Discontinuation Review, enter your reviewer name if needed. | Shows AI-detected discontinuation candidates (draft/confirmed/rejected tabs), plus read-only "zero-stock candidates" and "declining-sales candidates" tables below. |
| I2 | Click "廃番として確定 / Confirm discontinued" on a draft row. | Row moves to confirmed; recorded in the audit trail with your name. |
| I3 | Click "却下（現行品のまま）/ Reject" and cancel the reason prompt. | Nothing happens. |
| I4 | Click Reject again with a reason typed. | Row moves to rejected with the reason recorded. |
| I5 (**non-admin/no-role account**) | Attempt Confirm/Reject here. | Per Section 1, no server-side permission check currently exists on this action — expect it to succeed regardless of role. Confirm and record. |
| I6 | Check the "零在庫候補 / Zero-stock candidates" and "販売減少候補 / Declining-sales candidates" tables. | Populate with data, or show a specific "not enough history yet" message (rather than "none found") if SAP data depth is under 60 days. |

---

## 12. Section J — Maintenance mode & the rebuild-in-progress strip (admin triggers; everyone observes)

Coordinate this section with an admin — it needs someone to actually flip
the switch while others watch the effect from a normal account.

| ID | Steps | Expected Result |
|---|---|---|
| J1 (admin) | Run `python3 -m onefcst.maintenance on "UATテスト中"` (or ask a developer to run it). Have a non-admin tester with a tab already open, and another opening the site fresh. | Within ~20 seconds (or immediately, if a request errors), **both** testers see a full-screen takeover: 🔧 icon, "System under development", the custom message, and "Reload" / "Continue anyway" buttons. |
| J2 (non-admin tester) | Click "Continue anyway". | Full-screen takeover is replaced by a thin persistent banner strip at the top; the rest of the app is usable underneath. |
| J3 (admin) | Run `... maintenance off`. | Within one poll cycle, the strip disappears for everyone — including the tester who clicked "Continue anyway" (their dismissal should reset, so the *next* maintenance window shows full-screen again, not just the strip). |
| J4 (admin) | Trigger "Rebuild from NAS" from the Forecast page. While it's running, have another tester open a **different** page (e.g. Mismatch, not Forecast). | The non-blocking "⏳ Rebuilding data" strip appears on every page, not just Forecast — and the app stays fully navigable underneath. |
| J5 | Wait for the rebuild to finish. | Strip changes to "✅ Data rebuild finished — reload to see the new numbers" with a Reload button. Confirm it does **not** auto-reload on its own. |
| J6 (admin) | Turn maintenance ON while a rebuild is also running. | Only the full-screen maintenance takeover is visible — the rebuild strip is suppressed underneath it, even after dismissing maintenance to the thin-strip state. |

---

## 13. Section K — Help page (everyone; informational only)

| ID | Steps | Expected Result |
|---|---|---|
| K1 | Open Help. | Demo video plays (language-specific if available, otherwise a shared fallback); if no video file exists yet, a clear "missing" panel names the exact file path needed instead of a broken player. |
| K2 | Review the WAPE metric explainer. | Formula and worked example render correctly; the four confidence-band colors (good/rough/poor/unknown) match the same colors/thresholds used on the Forecast page's confidence dots. |

---

## 14. Section L — Access management (admin only; no UI page exists for this)

| ID | Steps | Expected Result |
|---|---|---|
| L1 (admin) | Confirm there is no page in the app to grant/revoke a role or assigned parts (checked: Profile is read-only). | Confirmed — access changes happen only via `deploy/roster.csv` + the backend `access_roster.py` script, or a direct call to the `/access` API. |
| L2 (admin) | After changing a person's role or assigned parts via the roster/script, have that person sign out and back in (or just retry an action). | Their new permissions take effect — e.g. someone newly granted a part can now Tune/adjust it; someone who lost a part can no longer. |
| L3 (admin) | Grant or revoke access via the API/script. | A corresponding entry appears in the Notifications audit trail ("access granted: … " / "access revoked: …"), attributed to the admin who made the change. |

---

## 15. Section assignment — who tests what

Based on `deploy/roster.csv` (department, role, 担当パーツ). Everyone runs
**Section 3 (Sign-in)** and **Section 4 (Dashboard)** individually first.

### Admins (購買本部) — Sections J, L, plus C1–C5, G6, G8
| Name | Email |
|---|---|
| 内田 暁久 | a_uchida@twave.co.jp |
| 粕壁 一輝 | k_kasukabe@twave.co.jp |
| 大貫 勝男 | k_onuki@twave.co.jp |

### Admins (企画部) — Sections J, L, plus F (accuracy), G (compat review)
| Name | Email |
|---|---|
| 筒井 愼平 | s_tsutsui@twave.co.jp |
| 久末 翔希 | s_hisasue@twave.co.jp |

### Purchasing buyers — Sections C6–C10, D, and Section G if their parts include CPU/MB/VGA/RTX
| Name | Email | Assigned parts (test with these; then confirm rejection on a colleague's parts) |
|---|---|---|
| Miker Yu | yumiker@twave.co.jp | NotePC |
| 金 意 | y_jin@twave.co.jp | Case, Case_other, Case_梱包 |
| 張 冬 | t_cho@twave.co.jp | MB, VGA, NotePC — also run Section G (compat review) |
| 大西 凌 | r_onishi@twave.co.jp | HDD, MEM, SSD |
| 林 健 | ke_hayashi@twave.co.jp | FAN, Mouse&KB, ODD, PSU |
| Kim Francisco Xavier Jr | x_kim@twave.co.jp | MB, RTX, VGA — also run Section G (compat review) |
| 佐久間 進一 | s_sakuma@twave.co.jp | (no parts currently assigned — use this account specifically to confirm that a purchasing role with **no** assigned parts can read everything but write nothing; good candidate for the "no-role"-style negative tests in G7/I5) |

### Planning (企画部) — Sections D (Tier-1 items: CPU/VGA/MB/Case), E (Mismatch), F (Accuracy), G (Compat Review), I (Discontinuation)
All of the following share the same **planning** role (all four Tier-1
categories, not scoped per person) — pick 2–3 from this list to actually
execute the section; the rest are eligible testers with identical
permissions, useful if the first pick is unavailable:

市村久瑠美, 岩﨑眞, 江川沙耶子, 小野雄己, 松岡怜, 古川凌, 萩原祥平, 大内実,
宍戸訓之, 伊東桃子, 渋谷亮太, 古泉勝幸, 丹波瞭, 桑野進, 田中信次郎,
小林健人, 小森至晃 (all @twave.co.jp, see `deploy/roster.csv` for exact
addresses).

---

## 16. Sign-off

| Section | Tester | Date | Result (Pass/Fail/Partial) | Issues filed |
|---|---|---|---|---|
| A — Sign-in | | | | |
| B — Dashboard | | | | |
| C — Forecast | | | | |
| D — Adjustments | | | | |
| E — Mismatch | | | | |
| F — Accuracy | | | | |
| G — Compat Review | | | | |
| H — Notifications | | | | |
| I — Discontinuation | | | | |
| J — Maintenance/Rebuild strip | | | | |
| K — Help | | | | |
| L — Access management | | | | |
