"""Successor ramp/fade transition logic — pure functions, no I/O.

PC parts have short lifecycles (12-18 months); a plain history×seasonality
model fails right at the transition (new SKU has no history, old SKU's
history overstates its future). This module encodes the behavior described
by planning (2026-07-06 meeting): the successor inherits the predecessor's
base volume, ramping in while the old part fades out over an overlap window
(their rule of thumb: ~1 month, but always a judgment call — kept editable).

Not yet parity-tested against a real transition (no formal rule exists at
Thirdwave today — this is genuinely new logic, validated by backtesting new
launches once historical transition data is available, per POC_BUILD_PLAN §4.)
"""
from __future__ import annotations


def ramp_fade_curve(predecessor_level: float, overlap_months: int,
                    horizon: int) -> dict:
    """Linear ramp/fade over `overlap_months`, holding steady after — the
    simplest defensible curve, deliberately not a fitted/ML shape (see
    'no ML models' decision). old_share + new_share always sum to 1.0 during
    the overlap so the COMBINED volume matches the predecessor's base level.
    """
    old, new = [], []
    for m in range(horizon):
        if overlap_months <= 0:
            new_share = 1.0 if m >= 0 else 0.0
        else:
            new_share = min(1.0, (m + 1) / overlap_months)
        old_share = 1.0 - new_share
        old.append(predecessor_level * old_share)
        new.append(predecessor_level * new_share)
    return {"old": old, "new": new}


def seed_new_item(predecessor_baseline: list[float], attach_rate_ratio: float = 1.0,
                  platform_changed: bool = False) -> dict:
    """Seed a brand-new item's forecast from its predecessor's baseline.

    attach_rate_ratio: adjustment if the new part's expected attach rate
    differs from the predecessor's (e.g. AI-suggested from spec similarity).
    platform_changed: True when socket/chipset/interface changed (e.g. Nova
    Lake's LGA1954) — flags that inheritance is LOW CONFIDENCE and should
    route to human review rather than auto-apply.
    """
    seeded = [v * attach_rate_ratio if v is not None else None
             for v in predecessor_baseline]
    return {"seeded_baseline": seeded,
            "confidence": "low" if platform_changed else "medium",
            "needs_review": platform_changed}
