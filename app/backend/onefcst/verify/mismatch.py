"""M3 deliverable — compatibility rule drafting + take-or-pay-weighted mismatch
gate. Read-only itself; delegates the actual CompatRule draft-write to
advisory/rules.py's draft_spec_heuristic_rules (advisory/ is this project's
only writable layer — moved there 2026-07-14, previously written in-line
here).

Per POC_BUILD_PLAN / BR-010: AI (here, an explicit spec-based rule engine —
see engine/compat.py's module docstring — or an LLM, see
advisory/llm_rules.py) DRAFTS rules with a confidence score; nothing is
auto-confirmed. The mismatch gate's socket classification comes primarily
from the deterministic table (engine/compat.py), same as always; for a
part that table can't classify, it falls back to any CONFIRMED CompatRule
pairing (see _confirmed_partner_family) — so confirming a draft in
CompatReview.tsx now has a real, visible effect here. Draft (unconfirmed)
rules are never consulted by this calculation, deterministic or LLM —
only status='confirmed' rows count (fixed 2026-07-10; previously this
module's own docstring claimed that already but it wasn't actually true —
the gate ignored the CompatRule table entirely, draft or confirmed).
"""
from __future__ import annotations

from datetime import date

from ..config import ONE_DIR
from ..db import get_session
from ..models import Item, PlanValue, CompatRule
from ..advisory.rules import draft_spec_heuristic_rules
from ..ingestion.mb_procurement import parse_mb_takeorpay, chipset_liability_rollup


def _confirmed_partner_family(item_id: int, session, is_cpu: bool) -> str | None:
    """Socket family implied by CONFIRMED CompatRule pairings, for an item
    the deterministic engine table can't classify on its own — e.g. a
    genuinely new/obscure part an LLM drafted a pairing for, that a human
    then reviewed and confirmed. Without this, confirming a rule in
    CompatReview.tsx had NO effect on the mismatch gate at all (found
    2026-07-10) — the gate always re-derived sockets from the static table
    only, ignoring the CompatRule table entirely.

    Only trusts a family when every confirmed partner agrees; conflicting
    confirmed pairs are treated as still-unclassified rather than guessed
    at — same "don't substitute a plausible-looking answer" principle used
    in the LLM drafting prompt itself."""
    from ..engine.compat import classify_cpu_socket, classify_mb_socket
    if is_cpu:
        rules = session.query(CompatRule).filter(
            CompatRule.part_a_id == item_id, CompatRule.relation == "fits",
            CompatRule.status == "confirmed").all()
        partner_ids = [r.part_b_id for r in rules]
        classify = classify_mb_socket
    else:
        rules = session.query(CompatRule).filter(
            CompatRule.part_b_id == item_id, CompatRule.relation == "fits",
            CompatRule.status == "confirmed").all()
        partner_ids = [r.part_a_id for r in rules]
        classify = classify_cpu_socket
    if not partner_ids:
        return None
    partners = session.query(Item).filter(Item.id.in_(partner_ids)).all()
    families = {fam for p in partners
               for fam in [classify(p.canonical_name)[0]] if fam}
    return families.pop() if len(families) == 1 else None


def _latest_master_month(session) -> date:
    """Anchor to the CURRENT month, not the raw max — far-future columns in
    the ONE master carry stray/placeholder formula artifacts (e.g. a
    473-million-unit outlier in 2027-03), the same class of defect already
    confirmed in ①'s frozen-window bug. Falls back to the nearest month with
    any master data if the current month itself is missing.
    """
    today = date.today().replace(day=1)
    exact = session.query(PlanValue.month).filter(
        PlanValue.level == "master", PlanValue.month == today).first()
    if exact:
        return today
    # Nearest-month pick is done in Python, not SQL. This used to sort by
    # func.julianday(), which exists ONLY in SQLite — under PostgreSQL it
    # raises UndefinedFunction. Worse, this is the fallback branch (reached
    # only when the current month has no master rows at all), so the failure
    # would not surface in any test and would first appear in production at
    # a month boundary. The candidate set is one row per distinct month —
    # tens of values — so sorting it here costs nothing and is dialect-free.
    # (SQLite hands Date columns back as ISO strings, Postgres as real
    # date objects — normalise before arithmetic, same as
    # forecast_views._as_date does for its own query results.)
    months = [date.fromisoformat(m) if isinstance(m, str) else m
              for (m,) in session.query(PlanValue.month)
              .filter(PlanValue.level == "master").distinct().all()
              if m is not None]
    if not months:
        return today
    return min(months, key=lambda m: abs((m - today).days))


def compute_mismatch(session) -> dict:
    """Group Tier-1 CPU/MB latest-month master demand by socket family and
    compare, weighted by take-or-pay liability. Socket family comes from
    the deterministic table first; for items it can't classify, falls back
    to any CONFIRMED CompatRule pairing (see _confirmed_partner_family) —
    this is what makes confirming a rule in CompatReview.tsx actually
    change this report, rather than being a record with no live effect.
    """
    from ..engine.compat import classify_cpu_socket, classify_mb_socket

    month = _latest_master_month(session)
    cpu = session.query(Item).filter(Item.category == "CPU").all()
    mb = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()

    def demand(item_id):
        v = (session.query(PlanValue.value)
            .filter(PlanValue.item_id == item_id, PlanValue.level == "master",
                    PlanValue.month == month).first())
        return (v[0] or 0.0) if v else 0.0

    confirmed_rule_classifications = {"cpu": [], "mb": []}

    cpu_by_socket: dict[str, float] = {}
    for item in cpu:
        socket, conf = classify_cpu_socket(item.canonical_name)
        if not socket:
            socket = _confirmed_partner_family(item.id, session, is_cpu=True)
            if socket:
                confirmed_rule_classifications["cpu"].append(item.canonical_name)
        if socket:
            cpu_by_socket[socket] = cpu_by_socket.get(socket, 0.0) + demand(item.id)

    mb_by_socket: dict[str, float] = {}
    for item in mb:
        socket, conf = classify_mb_socket(item.canonical_name)
        if not socket:
            socket = _confirmed_partner_family(item.id, session, is_cpu=False)
            if socket:
                confirmed_rule_classifications["mb"].append(item.canonical_name)
        if socket:
            mb_by_socket[socket] = mb_by_socket.get(socket, 0.0) + demand(item.id)

    liability_rows = parse_mb_takeorpay(ONE_DIR)
    chipset_liability = chipset_liability_rollup(liability_rows)
    # roll chipset-level liability up to socket family
    socket_liability: dict[str, dict] = {}
    from ..engine.compat import CHIPSET_SOCKET
    for chipset, info in chipset_liability.items():
        socket = CHIPSET_SOCKET.get(chipset)
        if not socket:
            continue
        agg = socket_liability.setdefault(socket, {"exclusive": False, "takeorpay_n1": False})
        agg["exclusive"] |= info["exclusive"]
        agg["takeorpay_n1"] |= info["takeorpay_n1"]

    results = []
    for socket in sorted(set(cpu_by_socket) | set(mb_by_socket)):
        c, m = cpu_by_socket.get(socket, 0.0), mb_by_socket.get(socket, 0.0)
        if c == 0 and m == 0:
            continue
        gap = m - c
        pct = (100 * gap / c) if c else None
        liab = socket_liability.get(socket, {"exclusive": False, "takeorpay_n1": False})
        severity = "HIGH" if (liab["takeorpay_n1"] and pct is not None and abs(pct) > 15) else \
                  "MEDIUM" if (pct is not None and abs(pct) > 15) else "LOW"
        results.append({"socket": socket, "cpu_demand": round(c, 1),
                        "mb_demand": round(m, 1), "gap": round(gap, 1),
                        "pct": round(pct, 1) if pct is not None else None,
                        "exclusive": liab["exclusive"], "takeorpay_n1": liab["takeorpay_n1"],
                        "severity": severity})
    results.sort(key=lambda r: (r["severity"] != "HIGH", r["severity"] != "MEDIUM",
                                -abs(r["gap"])))
    return {"month": month.isoformat() if month else None, "groups": results,
           "confirmed_rule_classifications": confirmed_rule_classifications}


def historical_scan(session, min_cpu_demand: float = 50.0) -> list[dict]:
    """M3 acceptance test: does the gate reproduce a known-shape historical
    mismatch? Scans ALL master-plan months (excluding the far-future columns
    already confirmed to carry stray formula artifacts — see
    _latest_master_month) for CPU-vs-MB socket-family divergence.

    min_cpu_demand filters out near-zero-CPU months where a % gap is
    meaningless noise, not a real signal.
    """
    from ..engine.compat import classify_cpu_socket, classify_mb_socket
    from collections import defaultdict

    cpu = session.query(Item).filter(Item.category == "CPU").all()
    mb = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()
    # same confirmed-rule fallback as compute_mismatch — a part's physical
    # socket doesn't change over time, so today's confirmed classification
    # applies consistently across the whole historical scan too.
    cpu_socket = {i.id: (classify_cpu_socket(i.canonical_name)[0]
                        or _confirmed_partner_family(i.id, session, is_cpu=True))
                 for i in cpu}
    mb_socket = {i.id: (classify_mb_socket(i.canonical_name)[0]
                       or _confirmed_partner_family(i.id, session, is_cpu=False))
                for i in mb}
    ids = [i for i in list(cpu_socket) + list(mb_socket)]

    rows = (session.query(PlanValue.item_id, PlanValue.month, PlanValue.value)
           .filter(PlanValue.level == "master", PlanValue.item_id.in_(ids)).all())
    cpu_by, mb_by = defaultdict(float), defaultdict(float)
    cutoff = date(2027, 1, 1)     # 2027-01 onward carries the confirmed formula artifact
    for item_id, month, val in rows:
        if month >= cutoff:
            continue
        socket = cpu_socket.get(item_id)
        if socket:
            cpu_by[(month, socket)] += (val or 0)
            continue
        socket = mb_socket.get(item_id)
        if socket:
            mb_by[(month, socket)] += (val or 0)

    out = []
    for key in set(cpu_by) | set(mb_by):
        c, m = cpu_by.get(key, 0.0), mb_by.get(key, 0.0)
        if c < min_cpu_demand:
            continue
        pct = 100 * (m - c) / c
        out.append({"month": key[0].isoformat(), "socket": key[1],
                   "cpu_demand": round(c, 1), "mb_demand": round(m, 1),
                   "pct": round(pct, 1)})
    out.sort(key=lambda r: -abs(r["pct"]))
    return out


MIN_PERSISTENT_MONTHS = 6
MIN_GAP_PCT = 100.0


def acceptance_test(hist: list[dict]) -> dict:
    """M3 acceptance check as a REAL assertion (FIX_PLAN H2) — the previous
    version printed an unconditional "PASS" with no pass/fail condition at
    all, and would raise IndexError/ValueError on an empty or short `hist`
    instead of reporting FAIL. Explicit thresholds: the top-gap socket must
    show a >MIN_GAP_PCT% divergence in at least MIN_PERSISTENT_MONTHS
    separate months to count as a persistent, reproducible mismatch rather
    than a one-off data blip."""
    if not hist:
        return {"passed": False,
               "message": "FAIL — no CPU/MB socket-family history found to "
               "scan (empty result, not a mismatch finding)"}
    socket = hist[0]["socket"]
    persistent = [r for r in hist if r["socket"] == socket and abs(r["pct"]) > MIN_GAP_PCT]
    if len(persistent) < MIN_PERSISTENT_MONTHS:
        return {"passed": False, "socket": socket, "persistent_months": len(persistent),
               "message": f"FAIL — {socket} shows only {len(persistent)} month(s) with "
               f">{MIN_GAP_PCT:.0f}% gap (need >= {MIN_PERSISTENT_MONTHS} to call it a "
               "persistent mismatch, not a data blip)"}
    return {"passed": True, "socket": socket, "persistent_months": len(persistent),
           "message": f"PASS — {socket} shows a PERSISTENT mismatch (>{MIN_GAP_PCT:.0f}% "
           f"gap in {len(persistent)} separate months from "
           f"{min(r['month'] for r in persistent)} to "
           f"{max(r['month'] for r in persistent)}), not a one-off data blip. This is "
           "exactly the shape of mismatch the gate exists to catch — cause (legitimate "
           "board-only demand vs a genuine over-forecast) needs confirmation with "
           "planning/purchasing."}


def run(session=None) -> dict:
    print("=== M3: compatibility rule drafting + mismatch gate ===")
    own_session = session is None
    if own_session:
        session = get_session()
    draft_stats = draft_spec_heuristic_rules(session)
    print(f"[compat] CPU items={draft_stats['cpu_total']} "
          f"(unclassified {draft_stats['cpu_unclassified']})  "
          f"MB items={draft_stats['mb_total']} "
          f"(unclassified {draft_stats['mb_unclassified']})  "
          f"draft rules stored={draft_stats['rules_drafted']}")
    print("[compat] status=draft for ALL rules — none auto-confirmed "
          "(BR-010: AI suggests, human confirms)")

    mismatch = compute_mismatch(session)
    n_from_confirmed = (len(mismatch["confirmed_rule_classifications"]["cpu"])
                       + len(mismatch["confirmed_rule_classifications"]["mb"]))
    print(f"\n[mismatch] socket-family demand comparison for {mismatch['month']} "
          f"(deterministic table + {n_from_confirmed} item(s) classified via a "
          "human-confirmed rule — draft/unconfirmed rules are never used here)")
    print(f"{'socket':<12}{'CPU demand':>12}{'MB demand':>12}{'gap':>10}{'gap%':>8}"
          f"{'excl.':>7}{'take-or-pay':>13}{'severity':>10}")
    for g in mismatch["groups"]:
        print(f"{g['socket']:<12}{g['cpu_demand']:>12}{g['mb_demand']:>12}"
              f"{g['gap']:>10}{(str(g['pct'])+'%' if g['pct'] is not None else '—'):>8}"
              f"{'YES' if g['exclusive'] else '':>7}"
              f"{'YES' if g['takeorpay_n1'] else '':>13}{g['severity']:>10}")
    n_high = sum(1 for g in mismatch["groups"] if g["severity"] == "HIGH")
    print(f"\n[mismatch] {n_high} HIGH-severity group(s) this month — take-or-pay "
          "exposure + >15% demand gap between CPU and matched motherboard sockets")

    print("\n[acceptance test] scanning full history (2017-2026) for a "
          "reproducible known-shape mismatch, min CPU demand 50 units/month...")
    hist = historical_scan(session)
    print(f"{'month':<12}{'socket':<10}{'CPU demand':>12}{'MB demand':>12}{'gap%':>10}")
    for r in hist[:10]:
        print(f"{r['month']:<12}{r['socket']:<10}{r['cpu_demand']:>12}"
              f"{r['mb_demand']:>12}{r['pct']:>9}%")
    result = acceptance_test(hist)
    print(f"\n[acceptance test] {result['message']}")
    if own_session:
        session.close()
    return {"draft": draft_stats, "mismatch": mismatch, "historical_scan": hist[:20],
           "acceptance_test": result}


if __name__ == "__main__":
    run()
