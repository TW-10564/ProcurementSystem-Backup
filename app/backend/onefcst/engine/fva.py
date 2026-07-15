"""Forecast Value Added (FVA) — pure scoring function, no I/O.

Scores whether a human adjustment made the forecast more or less accurate
than the untouched baseline, once the actual outcome is known. This is the
mechanism behind Success Criterion #2 (Charter): "does each adjustment add
value, measured — not just recorded." See verify/fva.py for the query that
finds adjustments old enough to score against a real actual.
"""
from __future__ import annotations

from .baseline import wape as _wape_core


def _wape(actual: list[float], predicted: list[float]) -> float | None:
    return _wape_core(actual, predicted, skip_none=True, round_to=2)


def score_fva(baseline: list[float], adjusted: list[float], actual: list[float]) -> dict:
    baseline_wape = _wape(actual, baseline)
    adjusted_wape = _wape(actual, adjusted)
    verdict = None
    if baseline_wape is not None and adjusted_wape is not None:
        if adjusted_wape < baseline_wape - 0.5:
            verdict = "adjustment_helped"
        elif adjusted_wape > baseline_wape + 0.5:
            verdict = "adjustment_hurt"
        else:
            verdict = "no_material_difference"
    return {"baseline_wape": baseline_wape, "adjusted_wape": adjusted_wape,
           "verdict": verdict}
