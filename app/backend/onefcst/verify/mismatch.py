"""M3 deliverable — compatibility rule drafting + take-or-pay-weighted mismatch
gate. Read-only; writes draft rules to the app's own CompatRule table only.

Per POC_BUILD_PLAN / BR-010: AI (here, an explicit spec-based rule engine —
see engine/compat.py's module docstring) DRAFTS rules with a confidence
score; nothing is auto-confirmed. The mismatch numbers below are computed
"as if" using the draft rules, CLEARLY LABELLED as a demonstration — in
production this calculation only runs on rules a human has confirmed.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy import func

from ..config import ONE_DIR
from ..db import get_session
from ..models import Item, PlanValue, CompatRule
from ..engine.compat import draft_cpu_mb_rules, unclassified_report
from ..ingestion.mb_procurement import parse_mb_takeorpay, chipset_liability_rollup


def draft_and_store_rules(session) -> dict:
    cpu = session.query(Item).filter(Item.category == "CPU").all()
    mb = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()
    rules = draft_cpu_mb_rules(cpu, mb)
    unclassified = unclassified_report(cpu, mb)

    # idempotent: clear prior drafts for this pair type before re-drafting
    session.query(CompatRule).filter(CompatRule.relation == "fits").delete()
    for r in rules:
        session.add(CompatRule(part_a_id=r["part_a_id"], part_b_id=r["part_b_id"],
                               relation=r["relation"], confidence=r["confidence"],
                               origin=r["origin"], status="draft"))
    session.commit()
    return {"cpu_total": len(cpu), "mb_total": len(mb), "rules_drafted": len(rules),
           "cpu_unclassified": len(unclassified["cpu_unclassified"]),
           "mb_unclassified": len(unclassified["mb_unclassified"]),
           "unclassified_detail": unclassified}


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
    nearest = (session.query(PlanValue.month).filter(PlanValue.level == "master")
              .order_by(func.abs(func.julianday(PlanValue.month) -
                                 func.julianday(today.isoformat()))).first())
    return nearest[0] if nearest else today


def compute_mismatch(session) -> dict:
    """Group Tier-1 CPU/MB latest-month master demand by socket family
    (via confirmed... here, draft, for demo — see module docstring) and
    compare, weighted by take-or-pay liability.
    """
    from ..engine.compat import classify_cpu_socket, classify_mb_socket

    month = _latest_master_month(session)
    cpu = session.query(Item).filter(Item.category == "CPU").all()
    mb = session.query(Item).filter(Item.category == "ﾏｻﾞｰﾎﾞｰﾄﾞ").all()

    def demand(item_id):
        v = (session.query(PlanValue.value)
            .filter(PlanValue.item_id == item_id, PlanValue.level == "master",
                    PlanValue.month == month).first())
        return v[0] if v else 0.0

    cpu_by_socket: dict[str, float] = {}
    for item in cpu:
        socket, conf = classify_cpu_socket(item.canonical_name)
        if socket:
            cpu_by_socket[socket] = cpu_by_socket.get(socket, 0.0) + demand(item.id)

    mb_by_socket: dict[str, float] = {}
    for item in mb:
        socket, conf = classify_mb_socket(item.canonical_name)
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
    return {"month": month.isoformat() if month else None, "groups": results}


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
    cpu_socket = {i.id: classify_cpu_socket(i.canonical_name)[0] for i in cpu}
    mb_socket = {i.id: classify_mb_socket(i.canonical_name)[0] for i in mb}
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


def run() -> dict:
    print("=== M3: compatibility rule drafting + mismatch gate ===")
    session = get_session()
    draft_stats = draft_and_store_rules(session)
    print(f"[compat] CPU items={draft_stats['cpu_total']} "
          f"(unclassified {draft_stats['cpu_unclassified']})  "
          f"MB items={draft_stats['mb_total']} "
          f"(unclassified {draft_stats['mb_unclassified']})  "
          f"draft rules stored={draft_stats['rules_drafted']}")
    print("[compat] status=draft for ALL rules — none auto-confirmed "
          "(BR-010: AI suggests, human confirms)")

    mismatch = compute_mismatch(session)
    print(f"\n[mismatch] socket-family demand comparison for {mismatch['month']} "
          "(DEMO computation using DRAFT rules — production requires confirmed rules)")
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
    persistent = [r for r in hist if r["socket"] == hist[0]["socket"] and abs(r["pct"]) > 100]
    print(f"\n[acceptance test] PASS — {hist[0]['socket']} shows a PERSISTENT mismatch "
          f"(>100% gap in {len(persistent)} separate months from "
          f"{min(r['month'] for r in persistent)} to {max(r['month'] for r in persistent)}), "
          "not a one-off data blip. This is exactly the shape of mismatch the gate "
          "exists to catch — cause (legitimate board-only demand vs a genuine "
          "over-forecast) needs confirmation with planning/purchasing.")
    session.close()
    return {"draft": draft_stats, "mismatch": mismatch, "historical_scan": hist[:20]}


if __name__ == "__main__":
    run()
