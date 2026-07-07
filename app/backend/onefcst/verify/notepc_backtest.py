"""Gate C (NotePC) — walk-forward backtest, same discipline as desktop's
verify/backtest.py, applied to NotePC's own brand-level unit history
(ingestion/notepc_seasonality.py) instead of ①係数計算.

Answers the concrete question behind "calculate NotePC like we do
desktops": does the SAME seasonal-share method (engine/seasonality.py,
unchanged) actually beat naive baselines when fit on NotePC's own 9 clean
fiscal years (期34-42) per brand? Proven, not assumed — exactly the bar
backtest.py already set for desktop.
"""
from __future__ import annotations

from ..engine.seasonality import compute_seasonality
from ..ingestion.notepc_seasonality import load_notepc_history


def _wape(actual: list[float], predicted: list[float]) -> float:
    num = sum(abs(a - p) for a, p in zip(actual, predicted))
    den = sum(abs(a) for a in actual)
    return 100 * num / den if den else float("nan")


def _shape(values: list[float]) -> list[float]:
    total = sum(values)
    return [v / total for v in values] if total else [0.0] * len(values)


def _run_brand(brand: str, history: dict[int, list], min_history: int = 5) -> dict:
    kis = sorted(k for k in history if all(v is not None for v in history[k]))
    rows = []
    for idx in range(min_history, len(kis)):
        test_ki = kis[idx]
        train_kis = kis[idx - min_history:idx]
        actual = history[test_ki]
        if any(v is None for v in actual):
            continue
        total = sum(actual)
        seasonal = compute_seasonality(history, train_kis)["share"]
        flat = [1 / 12] * 12
        last_year = _shape(history[kis[idx - 1]])

        row = {"brand": brand, "ki": test_ki, "total": round(total),
              "seasonal_wape": round(_wape(actual, [s * total for s in seasonal]), 2),
              "flat_wape": round(_wape(actual, [s * total for s in flat]), 2),
              "last_year_wape": round(_wape(actual, [s * total for s in last_year]), 2)}
        rows.append(row)
    return rows


def run(min_history: int = 5) -> dict:
    print("=== Gate C (NotePC): walk-forward backtest per brand ===")
    print(f"{'brand':<7}{'期':<6}{'total':>10}{'seasonal WAPE%':>16}"
          f"{'flat WAPE%':>12}{'last-yr WAPE%':>14}")
    history_by_brand = load_notepc_history()
    all_rows = []
    for brand, history in history_by_brand.items():
        rows = _run_brand(brand, history, min_history)
        all_rows.extend(rows)
        for row in rows:
            print(f"{row['brand']:<7}{row['ki']:<6}{row['total']:>10}"
                  f"{row['seasonal_wape']:>16}{row['flat_wape']:>12}"
                  f"{row['last_year_wape']:>14}")

    def avg(rows, key):
        vals = [r[key] for r in rows]
        return round(sum(vals) / len(vals), 2) if vals else None

    per_brand = {}
    for brand in history_by_brand:
        brand_rows = [r for r in all_rows if r["brand"] == brand]
        per_brand[brand] = {
            "n_periods": len(brand_rows),
            "avg_seasonal_wape": avg(brand_rows, "seasonal_wape"),
            "avg_flat_wape": avg(brand_rows, "flat_wape"),
            "avg_last_year_wape": avg(brand_rows, "last_year_wape"),
        }

    summary = {"n_periods": len(all_rows),
              "avg_seasonal_wape": avg(all_rows, "seasonal_wape"),
              "avg_flat_wape": avg(all_rows, "flat_wape"),
              "avg_last_year_wape": avg(all_rows, "last_year_wape"),
              "per_brand": per_brand, "rows": all_rows}
    print(f"\n[gate C / NotePC] avg WAPE — seasonal={summary['avg_seasonal_wape']}%  "
          f"flat={summary['avg_flat_wape']}%  last-year={summary['avg_last_year_wape']}%")
    best = min(("seasonal", summary["avg_seasonal_wape"]),
              ("flat", summary["avg_flat_wape"]),
              ("last_year", summary["avg_last_year_wape"]), key=lambda x: x[1])
    summary["winner"] = best[0]
    print(f"[gate C / NotePC] winner (lowest error, out-of-sample): {best[0]} ({best[1]}%)")
    for brand, stats in per_brand.items():
        print(f"  {brand}: seasonal={stats['avg_seasonal_wape']}%  "
              f"flat={stats['avg_flat_wape']}%  last-year={stats['avg_last_year_wape']}%  "
              f"(n={stats['n_periods']})")
    return summary


if __name__ == "__main__":
    run()
