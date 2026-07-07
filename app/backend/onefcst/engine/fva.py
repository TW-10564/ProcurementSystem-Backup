"""Forecast Value Added (FVA) — pure scoring function, no I/O.

Scores whether a human adjustment made the forecast more or less accurate
than the untouched baseline, once the actual outcome is known. This is the
mechanism behind Success Criterion #2 (Charter): "does each adjustment add
value, measured — not just recorded." See verify/fva.py for the query that
finds adjustments old enough to score against a real actual.
"""
from __future__ import annotations


def _wape(actual: list[float], predicted: list[float]) -> float | None:
    pairs = [(a, p) for a, p in zip(actual, predicted) if a is not None and p is not None]
    if not pairs:
        return None
    num = sum(abs(a - p) for a, p in pairs)
    den = sum(abs(a) for a, _ in pairs)
    return round(100 * num / den, 2) if den else None


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
