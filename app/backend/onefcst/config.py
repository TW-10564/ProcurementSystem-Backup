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
# Procurement team's own maintained name crosswalk (製品名称読みかえ sheet) —
# used to resolve blank-あだ名 SAP rows. Optional: absence is expected and
# non-blocking (see ingestion/sap.py). Chosen over a regex/heuristic matcher
# 2026-07-07 specifically because it's human-curated, not pattern-guessed.
NAME_CROSSWALK_GLOB = os.environ.get("NAME_CROSSWALK_GLOB",
                                     str(SAP_DIR / "SAP部材管理*.xlsx"))

ONE_MASTER = ONE_DIR / "ONE計画.xlsm"
NOTE_ONE_MASTER = ONE_DIR / "NoteONE計画.xlsm"
NOTE_STOCK_PLAN = PLANNING_DIR / "ノート在庫使用計画_43期.xlsx"


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

# --- domain constants --------------------------------------------------------
# Tier-1 = categories the planning dept plans directly; everything else is
# Tier-2 (derived by purchasing via attach/compatibility).
TIER1_CATEGORIES = {"CPU", "グラフィックカード", "ﾏｻﾞｰﾎﾞｰﾄﾞ", "PCｹｰｽ"}

FISCAL_START_MONTH = 8      # 期 runs Aug–Jul

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
