"""Central configuration — all NAS source paths, env-overridable.

Phase-1 rule: every path here is READ-ONLY input. The only writable location
is DATA_DIR (the app's own store: SQLite DB + generated Excel exports).
"""
import os
import re
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

# 企画部's sales-plan drop (agreed 2026-07-30). Mirrored INTO PLANNING_DIR
# itself, preserving 企画部's own subfolders — find_planning_file() searches
# recursively, so the nesting is fine and the ①②③④ renaming we used to do by
# hand is no longer needed. nas_sync treats an unreachable subfolder as a
# no-op, keeping whatever is already in PLANNING_DIR rather than failing the
# rebuild.
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


# --- planning workbook resolution -------------------------------------------
# The four planning inputs used to be hand-renamed with a circled-number
# prefix (①②③④) and dropped FLAT into PLANNING_DIR. On 2026-07-30 企画部 built
# their own structure on the NAS and named the files their way:
#
#   ★販売計画（企画部）/デスクPlan/販売計画6月提出分_….xlsx          ← ①
#                      /ノートPlan/                                  ← (empty)
#                      /Others/Monthly/☆値のみ【月次】【係数】月次推移20260701.xlsx  ← ③
#                      /Others/Weekly/【日次】ノート集計【2026年6月】_0602 1.xlsx    ← ②
#
# A prefix glob finds NOTHING in there: no circled numbers, and the files are
# nested. On the DGX, PLANNING_DIR points straight at that folder, so every
# rebuild aborted in _check_required_sources. Locally it was worse than an
# error — stale ①②③④ copies still sat at the top of PLANNING_DIR, so ingestion
# reported success while reading June data with July on disk unread.
#
# So match on the workbook's OWN distinctive name fragment, which is what
# survived their rename, and keep the legacy prefix as an alternative. Both
# layouts work, in one code path, with no cutover.
PLANNING_SOURCES: dict[str, dict] = {
    "①": {"label": "販売計画",     "fragments": ("販売計画",)},
    "②": {"label": "ノート集計",   "fragments": ("ノート集計",)},
    "③": {"label": "係数月次推移", "fragments": ("月次推移",)},
    "④": {"label": "部材利用数",   "fragments": ("部材利用数",)},
}

# Our own archive of superseded copies, and Excel's lock files. Including
# either would let a file we deliberately retired win the "newest" contest.
_PLANNING_IGNORED_DIRS = ("_archive",)


def _planning_period_key(path: Path) -> tuple:
    """Sort key: which copy holds the most RECENT data.

    Prefers a date embedded in the FILENAME over the file's mtime, because the
    name states the data period while mtime only records when someone copied
    the file. A June workbook re-uploaded today would otherwise beat a July
    one that has sat on the NAS untouched — exactly backwards.
    """
    name = path.name
    stamps = re.findall(r"(20\d{6})", name)              # 20260701
    if stamps:
        key = max(stamps)
    else:
        m = re.search(r"(20\d{2})年\s*(\d{1,2})月", name)  # 【2026年6月】
        key = f"{m.group(1)}{int(m.group(2)):02d}00" if m else ""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (key, mtime, name)


def _planning_candidates() -> list[Path]:
    if not PLANNING_DIR.exists():
        return []
    out = []
    for p in PLANNING_DIR.rglob("*.xls*"):
        if not p.is_file() or p.name.startswith(("~$", ".")):
            continue          # Excel lock files masquerade as real workbooks
        if any(part.startswith(_PLANNING_IGNORED_DIRS) for part in p.parts):
            continue
        out.append(p)
    return out


def find_planning_file(key: str) -> Path | None:
    """Resolve a planning workbook, or None if it is not present.

    `key` is the historical circled number (①②③④). Matches either that literal
    prefix or the file's own name fragment, anywhere under PLANNING_DIR.
    """
    spec = PLANNING_SOURCES.get(key, {})
    fragments = spec.get("fragments", ())
    hits = [p for p in _planning_candidates()
            if p.name.startswith(key) or any(f in p.name for f in fragments)]
    if not hits:
        return None
    return max(hits, key=_planning_period_key)


def planning_file(prefix: str) -> Path:
    """As find_planning_file, but raises when the workbook is missing.

    Kept raising for the inputs a build genuinely cannot proceed without;
    callers that can degrade should use find_planning_file and say so loudly.
    """
    hit = find_planning_file(prefix)
    if hit is None:
        label = PLANNING_SOURCES.get(prefix, {}).get("label", "")
        raise FileNotFoundError(
            f"no {prefix}{f' ({label})' if label else ''} planning workbook "
            f"found anywhere under {PLANNING_DIR}")
    return hit


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

# --- Entra ID (Microsoft SSO) -----------------------------------------------
# Set BOTH of these to require a real Microsoft identity on every request (see
# api/entra.py). Unset = the API_KEY gate above is the only auth, which is the
# dev default and the pre-SSO deployed state.
#
# The SPA is a PUBLIC client: it does the code+PKCE exchange in the browser via
# MSAL and sends the resulting token as `Authorization: Bearer …`. That is why
# no client secret appears anywhere in this file or in the environment — there
# isn't one to leak. This backend's whole job is to VERIFY the token, which
# needs only public information: the tenant's published signing keys.
ENTRA_TENANT_ID = os.environ.get("ONEFCST_ENTRA_TENANT_ID", "")
ENTRA_CLIENT_ID = os.environ.get("ONEFCST_ENTRA_CLIENT_ID", "")

# Expected `aud` claim. Defaults to the client ID, which is what an ID token
# carries when the SPA and this API are the same app registration — the setup
# that needs NO extra Entra configuration. If the registration later exposes an
# API scope (Entra -> Expose an API -> api://<client-id>/access_as_user), set
# this to that api:// URI and the access token is used instead. Preferred where
# possible: access tokens are the credential actually intended for calling an
# API, whereas an ID token is intended to describe a sign-in.
ENTRA_AUDIENCE = os.environ.get("ONEFCST_ENTRA_AUDIENCE", "") or ENTRA_CLIENT_ID

# When Entra is on, should the shared API key still be accepted? Kept true by
# default so scripted callers (monitoring, curl, the export cron) keep working
# without acquiring a token. Set to 0 to make a Microsoft identity the ONLY way
# in. Note this is not a browser bypass: once SSO is on, the frontend is built
# WITHOUT VITE_API_KEY, so no key is shipped to any browser.
ENTRA_ALLOW_API_KEY = os.environ.get("ONEFCST_ENTRA_ALLOW_API_KEY", "1") not in (
    "0", "false", "False", "no", "")

# Optional second gate, on top of Entra's own "assignment required": only these
# groups/roles may call the API. Comma-separated group object IDs (the `groups`
# claim) or app-role values (the `roles` claim). Empty = anyone Entra lets sign
# in, which is the right default when app assignment is already restricted to
# the named staff.
ENTRA_ALLOWED_GROUPS = [g.strip() for g in os.environ.get(
    "ONEFCST_ENTRA_ALLOWED_GROUPS", "").split(",") if g.strip()]

# Named-user allowlist — comma-separated work addresses. Compared
# case-insensitively against the token's `email`/`preferred_username`.
#
# This exists because the two controls live in different hands. Restricting
# sign-in properly belongs in Entra ("assignment required" + assign the staff),
# but that needs a tenant admin. Until they act, admin consent alone means
# ANY account in the tenant can sign in, and this closes that window without
# waiting for anyone. It is also the honest fallback if assignment is ever
# switched off in Entra without telling us.
#
# Not a replacement for Entra assignment: it stops a signed-in stranger from
# using the app, but they can still authenticate. Empty = no email filter.
ENTRA_ALLOWED_EMAILS = [e.strip().lower() for e in os.environ.get(
    "ONEFCST_ENTRA_ALLOWED_EMAILS", "").split(",") if e.strip()]

ENTRA_ENABLED = bool(ENTRA_TENANT_ID and ENTRA_CLIENT_ID)


# Addresses that are ALWAYS administrators, regardless of the user_access
# table. This exists to break a chicken-and-egg problem: the permissions
# roster lives in the database, and somebody has to be able to edit it before
# anyone is in it. Keep it to one or two people — it is the recovery path if
# the last admin row is deleted by mistake, so it must not be routine access.
BOOTSTRAP_ADMINS = [e.strip().lower() for e in os.environ.get(
    "ONEFCST_BOOTSTRAP_ADMINS", "").split(",") if e.strip()]

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
