"""Central configuration — all NAS source paths, env-overridable.

Phase-1 rule: every path here is READ-ONLY input. The only writable location
is DATA_DIR (the app's own store: SQLite DB + generated Excel exports).
"""
import os
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]          # Procurement-System/


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no new dependency): KEY=VALUE lines, # comments,
    optional single/double quotes. Real environment variables always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv(Path(__file__).resolve().parents[1] / ".env")   # app/backend/.env

# --- read-only sources -----------------------------------------------------
ONE_DIR = Path(os.environ.get("ONE_SOURCE_DIR", _ROOT / "ONE" / "ONE"))
PLANNING_DIR = Path(os.environ.get("PLANNING_DIR", _ROOT / "data" / "planning"))
SAP_DIR = Path(os.environ.get("SAP_DIR", _ROOT / "data" / "sap"))
SAP_STOCK_GLOB = os.environ.get("SAP_STOCK_GLOB", str(SAP_DIR / "*在庫.xlsx"))
# The live weekly SAP stock extract (Access DB, refreshed by procurement each
# week) — sits directly in ONE_DIR alongside ONE計画.xlsm. Primary source as
# of 2026-07-14; SAP_STOCK_GLOB (a manually-placed xlsx snapshot) is now only
# the fallback for when this file is unreachable. Requires the system
# `mdbtools` package (mdb-export) — not a pip dependency, see requirements.txt.
SAP_ACCDB_PATH = Path(os.environ.get("SAP_ACCDB_PATH", str(ONE_DIR / "SAP在庫抽出.accdb")))
# Procurement team's own maintained name crosswalk (製品名称読みかえ sheet) —
# used to resolve blank-あだ名 SAP rows. Optional: absence is expected and
# non-blocking (see ingestion/sap.py). Chosen over a regex/heuristic matcher
# 2026-07-07 specifically because it's human-curated, not pattern-guessed.
NAME_CROSSWALK_GLOB = os.environ.get("NAME_CROSSWALK_GLOB",
                                     str(SAP_DIR / "SAP部材管理*.xlsx"))

# 内田さん's weekly SAP drops on the NAS (agreed 2026-07-27): one folder for
# ZM13 週頭在庫 snapshots (在庫_MMDD.xlsx), one for ZP21 組込実績 weeks
# (組込_MMDD_MMDD.xlsx). nas_sync.refresh_from_nas() mirrors both into
# SAP_DIR subfolders on every rebuild; sap_stock_history / sap_zp21 then
# discover the files content-based under SAP_DIR — no further wiring needed.
# UNC paths are used directly (not the Z: drive letter) because these folders
# live under 購買本部\【通常業務】\ONE on the share, not under Z:'s root.
NAS_ZM13_UNC = os.environ.get(
    "ONEFCST_NAS_ZM13_UNC",
    r"\\172.25.20.11\サードウェーブグループ\43上期\サードウェーブ\購買本部\【通常業務】\ONE\★週頭在庫(ZM13)")
NAS_ZP21_UNC = os.environ.get(
    "ONEFCST_NAS_ZP21_UNC",
    r"\\172.25.20.11\サードウェーブグループ\43上期\サードウェーブ\購買本部\【通常業務】\ONE\★組込実績(ZP21)")
NAS_ZM13_CACHE = SAP_DIR / "週頭在庫_ZM13"
NAS_ZP21_CACHE = SAP_DIR / "組込実績_ZP21"

# 企画部's monthly ①②③④ sales-plan drop (agreed 2026-07-30). Until they start
# using it these files arrive by hand, so the folder may not exist yet on the
# share — nas_sync treats an unreachable subfolder as a no-op, keeping whatever
# is already in PLANNING_DIR, rather than failing the rebuild. Mirrored INTO
# PLANNING_DIR itself (not a subfolder) because planning_file() globs that
# directory directly by ①②③④ prefix.
NAS_PLAN_UNC = os.environ.get(
    "ONEFCST_NAS_PLAN_UNC",
    r"\\172.25.20.11\サードウェーブグループ\43上期\サードウェーブ\購買本部\【通常業務】\ONE\★販売計画（企画部）")
NAS_PLAN_CACHE = PLANNING_DIR

ONE_MASTER = ONE_DIR / "ONE計画.xlsm"
NOTE_ONE_MASTER = ONE_DIR / "NoteONE計画.xlsm"
NOTE_STOCK_PLAN = PLANNING_DIR / "ノート在庫使用計画_43期.xlsx"
# Procurement's own category-level max-stock-days policy table (small,
# human-maintained, filename date-suffixed — pick the latest by sort).
# Fallback only: never overrides a per-item 部品マスター value, see
# ingestion/stock_policy.py.
STOCK_POLICY_GLOB = os.environ.get("STOCK_POLICY_GLOB", str(ONE_DIR / "在庫の持ち方*.xlsx"))


def planning_file(prefix: str) -> Path:
    """Find a planning workbook by its circled-number prefix (①②③④)."""
    hits = sorted(PLANNING_DIR.glob(f"{prefix}*.xlsx"))
    if not hits:
        raise FileNotFoundError(f"no {prefix}* file in {PLANNING_DIR}")
    return hits[0]


# --- app-owned (the ONLY writable area) --------------------------------------
DATA_DIR = Path(os.environ.get("ONEFCST_DATA_DIR", _ROOT / "app" / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "onefcst.db"
EXPORT_DIR = DATA_DIR / "exports"
EXPORT_DIR.mkdir(exist_ok=True)

# DATABASE_URL — unset by default (local/WSL dev: sqlite at DB_PATH). Set to
# a real postgresql+psycopg://... URL for the DGX/production deployment; see
# db.py, which picks pool class by dialect (SQLite wants NullPool, Postgres
# wants a real QueuePool — see that module's own comment for why).
DATABASE_URL = os.environ.get("DATABASE_URL")

# --- domain constants --------------------------------------------------------
# Tier-1 = categories the planning dept plans directly; everything else is
# Tier-2 (derived by purchasing via attach/compatibility).
TIER1_CATEGORIES = {"CPU", "グラフィックカード", "ﾏｻﾞｰﾎﾞｰﾄﾞ", "PCｹｰｽ"}

FISCAL_START_MONTH = 8      # 期 runs Aug–Jul

# Order-suggestion flag threshold (engine/stock_projection.py suggest_order):
# an MOQ-rounded order is flagged "MOQ-driven" once it covers more than this
# many months of the item's average demand. Default 6 (see that module's
# 2026-07-17 comment for the packaging-item case that motivated the flag).
MOQ_EXCESSIVE_MONTHS = float(os.environ.get("ONEFCST_MOQ_EXCESSIVE_MONTHS", "6"))

# --- scheduler ----------------------------------------------------------------
# Automatic recurring forecast generation (scheduler.py). Runs in-process
# alongside the API — no external cron needed for this deployment size.
# Set ONEFCST_SCHEDULER_ENABLED=0 to disable (e.g. for manual-only control).
# Default interval is WEEKLY (168h), not daily — confirmed 2026-07-07 the SAP
# stock export only refreshes Mondays, and the procurement team's own review
# cadence is a Friday stock-setting meeting; running more often than the
# underlying source data changes would just recompute the same baseline.
SCHEDULER_ENABLED = os.environ.get("ONEFCST_SCHEDULER_ENABLED", "1") != "0"
SCHEDULER_INTERVAL_HOURS = float(os.environ.get("ONEFCST_SCHEDULER_INTERVAL_HOURS", "168"))
SCHEDULER_POLL_SECONDS = float(os.environ.get("ONEFCST_SCHEDULER_POLL_SECONDS", "300"))

# --- LLM (compat-rule drafting for unclassified parts) ------------------------
# Any OpenAI-compatible chat endpoint. Two supported setups (app/backend/.env):
#   in-house:  ONEFCST_LLM_URL=http://172.16.5.105:8000  (no key; data stays
#              on the internal network — required if prompts ever include
#              社外秘 roadmap content beyond catalogue part names)
#   OpenAI:    ONEFCST_LLM_URL=https://api.openai.com
#              ONEFCST_LLM_API_KEY=sk-…  (or OPENAI_API_KEY)
#              ONEFCST_LLM_MODEL=gpt-4o-mini (or similar)
# Unreachable server = the LLM draft step reports and skips; nothing else in
# the system depends on it.
LLM_URL = os.environ.get("ONEFCST_LLM_URL", "http://172.16.5.105:8000")
LLM_MODEL = os.environ.get("ONEFCST_LLM_MODEL", "Qwen/Qwen3.6-35B-A3B-FP8")
LLM_API_KEY = os.environ.get("ONEFCST_LLM_API_KEY",
                             os.environ.get("OPENAI_API_KEY", ""))
LLM_TIMEOUT = float(os.environ.get("ONEFCST_LLM_TIMEOUT", "120"))

# --- auth ---------------------------------------------------------------------
# Minimal shared-key gate (see api/auth.py). Unset/empty = auth disabled —
# the dev default, so local work isn't blocked. Set ONEFCST_API_KEY to require
# the same value in every request's X-API-Key header.
API_KEY = os.environ.get("ONEFCST_API_KEY", "")

# --- rate limiting ----------------------------------------------------------
# Basic per-client request cap (see api/rate_limit.py) — added 2026-07-23 as
# the cheapest defense against a leaked/guessed ONEFCST_API_KEY being
# hammered or brute-forced. Fixed 60-second window, in-memory (this app
# runs as a single uvicorn process — see NullPool note in db.py — so no
# shared-store coordination is needed). 0 disables it (the dev default,
# same "don't block local work" stance as API_KEY above).
RATE_LIMIT_PER_MINUTE = int(os.environ.get("ONEFCST_RATE_LIMIT_PER_MINUTE", "0"))

# --- CORS -----------------------------------------------------------------
# Comma-separated allowed origins for the SPA (see api/main.py's
# CORSMiddleware). Defaults to localhost-only dev origins — FIX_PLAN item
# "CORS wildcard" (previously allow_origins=["*"], flagged 2026-07-14 as
# unsafe for any non-localhost deployment: a wildcard origin lets ANY
# website's JS read this API's responses in a visitor's browser, not just
# this app's own frontend). Set ONEFCST_CORS_ORIGINS to the real deployed
# dashboard origin(s) before hosting this anywhere but localhost.
CORS_ORIGINS = [o.strip() for o in os.environ.get(
    "ONEFCST_CORS_ORIGINS",
    "http://localhost:5173,http://127.0.0.1:5173"
).split(",") if o.strip()]
