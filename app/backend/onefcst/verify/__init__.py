"""Accuracy/QA gates. No single shared entrypoint signature — genuinely
different modules need genuinely different inputs (documented here so a new
module's shape is a choice, not a guess):

  - DB-backed gates, `run(session=None) -> dict` (opens its own session if
    none given): reconciliation.py, discontinuation.py, mismatch.py, fva.py.
  - Pure-computation gates with no DB dependency at all, `run(**config) ->
    dict`: backtest.py (`min_history`), notepc_backtest.py (`min_history`).
    Adding an unused `session` param here would be dead clutter, not
    consistency — these never touch the DB.
  - Bespoke-shape modules, intentionally not named/shaped like the rest:
    order_suggestions.py's `run_plausibility_check(days_back, limit)` prints
    a human-readable comparison report as its primary output, dict return is
    secondary; report.py's `build(report) -> Path` writes an Excel workbook
    and returns its path, not a dict.

api/main.py's report endpoints already call each of these individually
(see the `/reports/*` routes) — there is no generic "iterate all verify
modules" caller today, so this is a map for whoever adds the next module or
that future caller, not a promise every module already conforms to one
shape.
"""
