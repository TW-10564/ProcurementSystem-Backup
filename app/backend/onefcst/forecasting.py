"""Forecast run generation — populates forecast_runs / forecasts.

Reads each item's monthly history from the ingested 部品予測 (master-level
plan_values), produces a per-SKU baseline forecast with engine/baseline.py
(three candidate methods compete per SKU on that SKU's own hold-out), and
writes the results to this app's own store.

Every run also validates itself, WITHOUT selection bias: the method is
chosen on months −12…−7 and then scored on the untouched months −6…−1, so
the accuracy stored with the run measures the full pipeline (selection
included) out of sample. A flat6 naive baseline is scored on the same
months as the bar the pipeline must clear.

Writes ONLY to the app DB — never to any NAS source (Phase-1 rule).
"""
from __future__ import annotations

import json
from datetime import date

from .db import get_session
from .models import AccuracyScore, Forecast, ForecastRun, Item, PlanValue, Snapshot
from .engine import baseline
from . import planner_accuracy
from . import tier2_forecasting

METHOD_VERSION = "sku-selected-v3"  # v3 adds combo (avg-of-candidates), M+1 reporting
DEFAULT_HORIZON = 12


def _level_series(session, level: str) -> dict[int, dict[date, float]]:
    """{item_id: {month: value}} for one PlanValue.level, latest-snapshot-
    wins per (item, month).

    FIX_PLAN C4: duplicate (item, month) rows happen when a source file is
    re-ingested after a correction — the earlier (now-stale) snapshot's row
    is NOT replaced, it's just added alongside the new one (no unique
    constraint on PlanValue(item_id, month, level)). Averaging them (the old
    behavior) blends the stale and corrected values. Instead, take only the
    LATEST snapshot's value per (item, month) — same latest-snapshot-wins
    rule _stock_by_item already applies to StockState."""
    rows = (session.query(PlanValue.item_id, PlanValue.month, PlanValue.value,
                          PlanValue.snapshot_id, Snapshot.file_mtime)
            .join(Snapshot, Snapshot.id == PlanValue.snapshot_id)
            .filter(PlanValue.level == level, PlanValue.item_id.isnot(None),
                    PlanValue.value.isnot(None))
            .order_by(PlanValue.item_id, PlanValue.month,
                     Snapshot.file_mtime.asc(), PlanValue.snapshot_id.asc())
            .all())
    out: dict[int, dict[date, float]] = {}
    for item_id, month, value, _snapshot_id, _file_mtime in rows:
        if isinstance(month, str):                      # SQLite returns ISO strings
            month = date.fromisoformat(month)
        out.setdefault(item_id, {})[month] = float(value)   # later (latest) overwrites
    return out


def _series_by_item(session) -> dict[int, dict[date, float]]:
    """{item_id: {month: value}} of demand history, master-level plan
    values preferred PER MONTH.

    Cross-check against Data Summary.xlsx (2026-07-08) found 20 items —
    11 OS licenses (ｿﾌﾄｳｪｱS) plus a handful of others — with ZERO
    master-level history: their consumption only ever appears in the
    one_row level (the 17 ONE 部品詳細 sub-files' own series; see
    ingestion/subfiles.py), never in ONE計画 部品予測 itself.

    The merge is per (item, month), not per item: an earlier version fell
    back to one_row only when an item had NO master rows AT ALL, which
    missed the much more common case of an item whose master coverage
    simply STOPS partway through (e.g. a memory SKU being phased out —
    master has real history through April, then goes silent for May
    onward, while the sub-file's own series keeps recording explicit
    zeros for those same months because it's still tracked there). Master
    wins for any month it actually covers; one_row fills in only the
    months master is silent on, for every item, not just the 20 with zero
    master coverage."""
    master = _level_series(session, "master")
    one_row = _level_series(session, "one_row")
    merged: dict[int, dict[date, float]] = {}
    for item_id in set(master) | set(one_row):
        combined = dict(one_row.get(item_id, {}))
        combined.update(master.get(item_id, {}))    # master wins per-month
        merged[item_id] = combined
    return merged


def _unbiased_score(obs: dict[date, float], start: date) -> dict | None:
    """Score the FULL pipeline out of sample: select the method on months
    −12…−7, evaluate that choice on months −6…−1 (never seen by selection).

    Reports two slices of the SAME unbiased test, not two different tests:
    the 6-month blended WAPE (what's stored per-item today) AND the M+1
    (next-month-only) error. Measured 2026-07-07: pooled M+1 WAPE is ~54-70%
    vs ~121% for the 6-month blend — buyers act on the near months given
    lead times, so burying that under the noisier far horizon understates
    what's actually usable."""
    final = baseline.holdout_eval(obs, start)
    if final is None:
        return None
    earlier = baseline.holdout_eval(obs, baseline.add_months(start, -6))
    chosen = earlier["winner"] if earlier else baseline.DEFAULT_METHOD
    if chosen not in final["wapes"]:
        chosen = (baseline.DEFAULT_METHOD if baseline.DEFAULT_METHOD
                  in final["wapes"] else min(final["wapes"], key=final["wapes"].get))
    m1_err = final["m1_err"].get(chosen)
    m1_actual = final["m1_actual"]
    wape_m1 = round(100 * m1_err / m1_actual, 2) if m1_actual else None
    return {"wape": final["wapes"][chosen],
            "naive_wape": final["wapes"].get("flat6"),
            "wape_m1": wape_m1,
            "chosen": chosen, "actual_total": final["actual_total"],
            "m1_err": m1_err, "m1_naive_err": final["m1_err"].get("flat6"),
            "m1_actual": m1_actual}


def generate(session=None, horizon: int = DEFAULT_HORIZON,
             start_month: date | None = None) -> dict:
    """Create one forecast run covering every item with usable history."""
    own_session = session is None
    session = session or get_session()
    try:
        start = start_month or date.today().replace(day=1)
        series = _series_by_item(session)
        items = {i.id: i for i in session.query(Item).all()}

        run = ForecastRun(method_version=METHOD_VERSION)
        session.add(run)
        session.flush()

        n_archived = planner_accuracy.archive_current_plan(session, series, start, horizon)

        n_by_method: dict[str, int] = {}
        scored = []
        forecasted = 0
        already_forecasted: set[int] = set()
        for item_id, obs in series.items():
            result = baseline.make_forecast(obs, start, horizon)
            if result is None:
                continue
            rows, method = result
            n_by_method[method] = n_by_method.get(method, 0) + 1
            forecasted += 1
            already_forecasted.add(item_id)
            session.add_all([Forecast(run_id=run.id, item_id=item_id,
                                      month=m, baseline=v, method=method)
                             for m, v in rows])

            score = _unbiased_score(obs, start)
            if score and score["naive_wape"] is not None:
                item = items.get(item_id)
                scored.append((item.category if item else None, score))
                session.add(AccuracyScore(run_id=run.id, item_id=item_id,
                                          category=item.category if item else None,
                                          month=start, level="baseline",
                                          wape=score["wape"], wape_m1=score["wape_m1"]))

        tier2_summary = tier2_forecasting.generate_tier2(
            session, run, series, items, already_forecasted, start)

        # volume-weighted aggregate: big movers count for what they weigh
        def agg(rows):
            tot = sum(s["actual_total"] for _, s in rows)
            if not tot:
                return None
            return {"wape": round(sum(s["wape"] * s["actual_total"]
                                      for _, s in rows) / tot, 2),
                    "naive_wape": round(sum(s["naive_wape"] * s["actual_total"]
                                            for _, s in rows) / tot, 2),
                    "items": len(rows)}

        by_cat: dict[str, list] = {}
        for cat, score in scored:
            by_cat.setdefault(cat or "（未分類）", []).append((cat, score))

        # near-term (M+1) pooled WAPE — same unbiased test, first month only.
        # Pooled (not per-item averaged) because many SKUs have a small or
        # zero M+1 actual; pooling avoids a handful of tiny denominators
        # dominating the ratio.
        m1_rows = [s for _, s in scored if s.get("m1_actual")]
        near_term = None
        if m1_rows:
            m1_act = sum(s["m1_actual"] for s in m1_rows)
            near_term = {"wape": round(100 * sum(s["m1_err"] for s in m1_rows) / m1_act, 2),
                        "naive_wape": round(100 * sum(s["m1_naive_err"] for s in m1_rows) / m1_act, 2),
                        "items": len(m1_rows)}

        holdout = {"overall": agg(scored), "near_term": near_term,
                   "by_category": {c: agg(rows) for c, rows in
                                   sorted(by_cat.items(),
                                          key=lambda kv: -len(kv[1]))[:12]},
                   "note": "out-of-sample: method chosen on months -12..-7, "
                           "scored on -6..-1; naive = trailing 6-month mean. "
                           "near_term = same test, first forecast month only "
                           "(pooled, not per-item averaged) — closer to what "
                           "a buyer actually acts on given lead times."}

        summary = {"start_month": start.isoformat(), "horizon": horizon,
                   "items_forecasted": forecasted,
                   "items_skipped_stale": len(series) - forecasted,
                   "methods": n_by_method, "holdout": holdout,
                   "planner_snapshots_archived": n_archived,
                   "tier2": tier2_summary}
        run.note = json.dumps(summary, ensure_ascii=False)
        session.commit()
        summary["run_id"] = run.id
        summary["created_at"] = run.created_at.isoformat()
        return summary
    finally:
        if own_session:
            session.close()


def latest_run(session) -> ForecastRun | None:
    return (session.query(ForecastRun).order_by(ForecastRun.id.desc()).first())


if __name__ == "__main__":
    s = generate()
    print(json.dumps(s, ensure_ascii=False, indent=2))
