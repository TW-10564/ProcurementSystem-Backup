"""
Face 1 — Consolidation portal (FastAPI + hand-built HTML/CSS/JS).

Replaces the Streamlit dashboard with a fully controlled frontend so the UI can
actually look clean. The engine (consolidate / reconcile / excel_writer) is
untouched — this is purely the presentation layer.

Routes:
  GET  /          → the dashboard (data embedded as JSON into index.html)
  POST /refresh   → re-run reconcile.run() against the source files
  GET  /download  → the formatted Excel (reuses excel_writer)

Run:  python3 -m uvicorn app:app --host 0.0.0.0 --port 8501
  or: python3 app.py
"""
import json
import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse

import consolidate
import reconcile
import excel_writer
import master_desktop
import anomalies
import purchasing
import stock_paste
from config_all import TOTAL_EXPECTED_SKUS

HERE = Path(__file__).parent
DB_PATH = consolidate.DB_PATH
TEMPLATE = HERE / "index.html"

app = FastAPI(title="ONE Consolidation Portal")


def _conn():
    return sqlite3.connect(DB_PATH)


def build_payload() -> dict:
    """Assemble everything the page needs into one JSON-able dict."""
    if not DB_PATH.exists():
        reconcile.run()

    with _conn() as c:
        rows = c.execute("SELECT COUNT(*) FROM consolidated_skus").fetchone()
        if not rows or rows[0] == 0:
            reconcile.run()

    with _conn() as c:
        n, exc, low, neg, snap = c.execute(
            """SELECT COUNT(*), COALESCE(SUM(flag_excess),0),
                      COALESCE(SUM(flag_low),0), COALESCE(SUM(flag_negative),0),
                      MAX(snapshot_date) FROM consolidated_skus""").fetchone()

        recon_rows = c.execute(
            """SELECT category, source_file, skus_parsed, expected_skus,
                      blocks_seen, status, COALESCE(note,'')
               FROM reconciliation ORDER BY
                 CASE status WHEN 'FAIL' THEN 0 WHEN 'WARN' THEN 1 ELSE 2 END,
                 category""").fetchall()
        n_pass = sum(1 for r in recon_rows if r[5] == "PASS")
        n_warn = sum(1 for r in recon_rows if r[5] == "WARN")
        n_fail = sum(1 for r in recon_rows if r[5] == "FAIL")

        summary = [
            {"category": cat, "file": src, "skus": skus,
             "exc": exc_, "low": low_, "neg": neg_}
            for cat, src, skus, exc_, low_, neg_ in c.execute(
                """SELECT category, source_file, COUNT(*),
                          COALESCE(SUM(flag_excess),0), COALESCE(SUM(flag_low),0),
                          COALESCE(SUM(flag_negative),0)
                   FROM consolidated_skus GROUP BY category, source_file
                   ORDER BY COUNT(*) DESC""")
        ]

        skus = [
            {"cat": cat, "sku": sku, "src": src,
             "safety": safety, "bal": bal, "days": days,
             "exc": int(e), "low": int(l), "neg": int(ng)}
            for cat, sku, src, safety, bal, days, e, l, ng in c.execute(
                """SELECT category, sku, source_file, safety_days, stock_balance,
                          stock_days, flag_excess, flag_low, flag_negative
                   FROM consolidated_skus ORDER BY category, stock_days DESC""")
        ]

    gate_ok = n_fail == 0 and n == TOTAL_EXPECTED_SKUS
    return {
        "snapshot": snap or date.today().isoformat(),
        "expected_total": TOTAL_EXPECTED_SKUS,
        "kpi": {"total": n, "excess": exc, "low": low, "negative": neg,
                "gate_ok": gate_ok, "pass": n_pass, "warn": n_warn, "fail": n_fail},
        "summary": summary,
        "skus": skus,
        "recon": [{"cat": r[0], "file": r[1], "parsed": r[2], "expected": r[3],
                   "blocks": r[4], "status": r[5], "note": r[6]} for r in recon_rows],
    }


@app.get("/", response_class=HTMLResponse)
def index():
    html = TEMPLATE.read_text(encoding="utf-8")
    payload = json.dumps(build_payload(), ensure_ascii=False)
    html = html.replace("/*__DATA__*/{}", payload)
    return HTMLResponse(html)


@app.post("/refresh")
def refresh():
    """Single refresh: re-read NAS files → consolidation + reconciliation + anomalies."""
    reconcile.run()      # consolidation + accuracy gate
    anomalies.run()      # rebuild anomaly findings (reuses the fresh consolidation)
    purchasing.run()     # overdue POs + price history + incoming-excess
    try:
        stock_paste.run()    # regenerate 実在庫 paste Excel (skips if SAP file missing)
    except FileNotFoundError:
        pass
    return RedirectResponse("/", status_code=303)


@app.get("/anomalies")
def anomalies_data():
    from fastapi.responses import JSONResponse
    return JSONResponse(anomalies.build_portal_data())


@app.get("/procurement")
def procurement_data():
    from fastapi.responses import JSONResponse
    return JSONResponse(purchasing.build_portal_data())


@app.get("/master")
def master_data():
    """Lazy-loaded data for the 'master automation' tab (reads master + sub-files)."""
    from fastapi.responses import JSONResponse
    return JSONResponse(master_desktop.build_portal_data())


@app.get("/master/download")
def master_download():
    out = master_desktop.HERE / f"部品予測_自動生成_{date.today():%Y%m%d}.xlsx"
    master_desktop.run()  # regenerate fresh
    return FileResponse(
        out, filename=out.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/stock")
def stock_data():
    from fastapi.responses import JSONResponse
    try:
        return JSONResponse(stock_paste.build_portal_data())
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.get("/stock/download")
def stock_download():
    from fastapi.responses import JSONResponse
    try:
        out = stock_paste.run()
        return FileResponse(
            out, filename=out.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except FileNotFoundError as e:
        return JSONResponse({"error": str(e)}, status_code=404)


@app.get("/download")
def download():
    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        path = excel_writer.export(tmp.name)
    return FileResponse(
        path, filename=f"統合出力_{date.today():%Y%m%d}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8501)
