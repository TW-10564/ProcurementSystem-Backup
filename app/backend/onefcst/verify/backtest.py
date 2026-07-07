"""Gate C — walk-forward backtest: proves the seasonal baseline earns its
place with evidence, not opinion (POC_BUILD_PLAN §"no ML" decision).

Uses the 10 fiscal years of monthly PC-unit history in ①係数計算 (33期-42期).
For each 期 walked forward, predict that 期's monthly shape from the PRIOR
5 完了 期 (exactly the method already proven at 48/48 cell parity) and score
against what actually happened, next to two naive baselines:
  - flat:      every month gets an equal 1/12 share of the 期 total
  - last-year: this 期's shape = the immediately prior 期's shape

If the seasonal method doesn't clearly beat 'last-year' out of sample, that's
a real finding, not a foregone conclusion — this harness is what would catch it.
"""
from __future__ import annotations

from ..engine.seasonality import compute_seasonality
from ..ingestion.keisuu import load_keisuu_history


def _wape(actual: list[float], predicted: list[float]) -> float:
    num = sum(abs(a - p) for a, p in zip(actual, predicted))
    den = sum(abs(a) for a in actual)
    return 100 * num / den if den else float("nan")


def _shape(values: list[float]) -> list[float]:
    total = sum(values)
    return [v / total for v in values] if total else [0.0] * len(values)


def run(min_history: int = 5) -> dict:
    history = load_keisuu_history()               # {期: [12 monthly units]}
    kis = sorted(k for k in history if all(v is not None for v in history[k]))

    print("=== Gate C: walk-forward backtest (seasonal share vs naive baselines) ===")
    print(f"{'期':<6}{'total':>10}{'seasonal WAPE%':>16}{'flat WAPE%':>12}{'last-yr WAPE%':>14}")
    rows = []
    for idx in range(min_history, len(kis)):
        test_ki = kis[idx]
        train_kis = kis[idx - min_history:idx]
        actual = history[test_ki]
        if any(v is None for v in actual):
            continue
        total = sum(actual)
        actual_shape = _shape(actual)

        seasonal = compute_seasonality(history, train_kis)["share"]
        flat = [1 / 12] * 12
        last_year = _shape(history[kis[idx - 1]])

        seasonal_pred = [s * total for s in seasonal]
        flat_pred = [s * total for s in flat]
        last_year_pred = [s * total for s in last_year]

        row = {"ki": test_ki, "total": round(total),
              "seasonal_wape": round(_wape(actual, seasonal_pred), 2),
              "flat_wape": round(_wape(actual, flat_pred), 2),
              "last_year_wape": round(_wape(actual, last_year_pred), 2)}
        rows.append(row)
        print(f"{row['ki']:<6}{row['total']:>10}{row['seasonal_wape']:>16}"
              f"{row['flat_wape']:>12}{row['last_year_wape']:>14}")

    def avg(key):
        vals = [r[key] for r in rows]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {"n_periods": len(rows),
              "avg_seasonal_wape": avg("seasonal_wape"),
              "avg_flat_wape": avg("flat_wape"),
              "avg_last_year_wape": avg("last_year_wape"), "rows": rows}
    print(f"\n[gate C] avg WAPE — seasonal={summary['avg_seasonal_wape']}%  "
          f"flat={summary['avg_flat_wape']}%  last-year={summary['avg_last_year_wape']}%")
    best = min(("seasonal", summary["avg_seasonal_wape"]),
              ("flat", summary["avg_flat_wape"]),
              ("last_year", summary["avg_last_year_wape"]), key=lambda x: x[1])
    summary["winner"] = best[0]
    print(f"[gate C] winner (lowest error, out-of-sample): {best[0]} ({best[1]}%)")
    print("[gate C] this is the evidence bar any future model (incl. ML) must clear.")
    return summary


if __name__ == "__main__":
    run()
