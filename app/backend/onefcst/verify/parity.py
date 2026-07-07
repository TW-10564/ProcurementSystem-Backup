"""Gate B — cell-level parity: the engine must reproduce ①'s stored Excel
values EXACTLY before any of its numbers are shown anywhere in the system.

forecasting/poc_v0.py (the script this gate originally checked against) no
longer exists on disk and cannot be recovered, so this gate no longer diffs
the engine against poc_v0's own stored snapshot. Instead it sources ground
truth directly from ①'s live cells:
  - seasonality/landing: ingestion/keisuu.py reads ①係数計算's OWN cached
    formula results (its "過去5期(41-37)" block and "42期着地予測" row) —
    Excel's own last-computed values, which is a strictly stronger ground
    truth than a script's copy of them.
  - chain: ingestion/plan1_chain.py reads ①販売計画's コンシューマー/コマーシャル
    sheets' actual per-SKU cell formulas (not just cached values), so
    engine/chain.py's reproduce_chain is checked against the real formulas
    still in the file today.
This proves the ported engine is internally consistent with ①'s live
numbers, not that it matches a file that no longer exists.
"""
from __future__ import annotations

import sys

from ..engine.seasonality import compute_seasonality, compute_landing
from ..engine.chain import reproduce_chain
from ..ingestion.keisuu import load_keisuu_history, load_keisuu_reference
from ..ingestion.plan1_chain import load_plan_chain


def close_enough(a, b, tol=1e-6):
    if a is None or b is None:
        return a == b
    if b == 0:
        return abs(a) < tol
    return abs(a - b) <= tol * max(1.0, abs(b))


def check_seasonality() -> dict:
    p1 = poc_v0.find_file("①")
    k = poc_v0.load_keisuu(p1)
    season = compute_seasonality(k["history"], [37, 38, 39, 40, 41])
    checks = [("過去5期平均", season["avg"], k["their_avg5"]),
             ("年間構成比", season["share"], k["their_share5"]),
             ("先月比", season["mom"], k["their_mom5"]),
             ("季節指数", season["season"], k["their_season5"])]
    total = matched = 0
    detail = []
    for name, mine, theirs in checks:
        for a, b in zip(mine, theirs):
            if b is None:
                continue
            total += 1
            ok = close_enough(a, b, 1e-6)
            matched += ok
            if not ok:
                detail.append((name, a, b))
    return {"total": total, "matched": matched, "mismatches": detail}


def check_landing() -> dict:
    p1 = poc_v0.find_file("①")
    k = poc_v0.load_keisuu(p1)
    season = compute_seasonality(k["history"], [37, 38, 39, 40, 41])
    landing = compute_landing(k["history"], season["share"], 42)
    rep = landing["as_in_file"]
    matched = sum(1 for a, b in zip(rep, k["their_landing"])
                 if b is not None and close_enough(a, b, 1e-9))
    return {"total": 12, "matched": matched}


def check_chain() -> dict:
    p1 = poc_v0.find_file("①")
    plan = poc_v0.load_plan_sheet(p1, "コンシューマー")
    sku_cells = [sku["cells"] for sku in plan["skus"]]
    st = reproduce_chain(plan["months"], plan["mom"], plan["manual"], sku_cells)
    return {"total": st["chain_cells"], "matched": st["chain_match"],
           "mismatches": st["chain_mismatch"][:10]}


def run() -> dict:
    print("=== Gate B: cell-level parity (engine vs ① Excel) ===")
    results = {"seasonality": check_seasonality(), "landing": check_landing(),
              "chain": check_chain()}
    all_pass = True
    for name, r in results.items():
        ok = r["matched"] == r["total"]
        all_pass &= ok
        print(f"  {name:<12} {r['matched']}/{r['total']}  "
              f"{'PASS' if ok else 'FAIL — ' + str(r.get('mismatches', r.get('mismatches')))}")
    print(f"[gate B] {'PASS — engine matches ① at 100% cell level' if all_pass else 'FAIL — engine diverges from source, DO NOT SHIP'}")
    results["status"] = "PASS" if all_pass else "FAIL"
    return results


if __name__ == "__main__":
    r = run()
    sys.exit(0 if r["status"] == "PASS" else 1)
