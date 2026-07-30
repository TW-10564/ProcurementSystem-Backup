import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadReport } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type {
  ForecastDetail,
  ForecastList,
  ForecastSummary,
  OrderActionInfo,
  OrderSuggestions,
  PlanVsActual,
  RebuildHistoryEntry,
} from "../api/types";
import type { TranslationKey } from "../i18n/translations";
import { currentAuthor } from "../auth/author";

const METHOD_KEY: Record<string, TranslationKey> = {
  seasonal: "fc_method_seasonal",
  trend: "fc_method_trend",
  flat3: "fc_method_flat3",
  flat6: "fc_method_flat6",
  median6: "fc_method_median6",
  combo: "fc_method_combo",
  tier2_attach_rate: "fc_method_tier2_attach_rate",
  tier2_category_average: "fc_method_tier2_category_average",
};

// Forecast.method is a base method optionally suffixed with "+known_plan"
// (a real, already-committed source number overrode the statistical guess
// for at least one month — see engine/baseline.apply_known_plan) and/or
// "+discontinuing" (a sustained run of confirmed-zero known months got
// carried into the unguessed tail instead of reverting to the old average
// — see engine/baseline.apply_discontinuation_signal). Parsed once here so
// every place a method is shown can render the base label plus badges
// instead of an unreadable raw compound string.
function parseMethod(method: string | null): {
  base: string | null;
  knownPlan: boolean;
  discontinuing: boolean;
  tier2: boolean;
} {
  if (!method) return { base: null, knownPlan: false, discontinuing: false, tier2: false };
  // tier2_* methods never get a per-SKU backtest for their known_plan rows
  // (tier2_forecasting.py only records an AccuracyScore for the
  // tier2_attach_rate-flavored rows, not bare tier2_known_plan — see its
  // generate_tier2()) — checked on the RAW string before any stripping so
  // it survives every suffix combination (tier2_known_plan,
  // tier2_attach_rate+known_plan, +discontinuing, ...).
  const tier2 = method.startsWith("tier2_");
  // explicit `string | null`: the known_plan branch below clears it to null
  // ("came entirely from the real plan, no statistical method underneath"),
  // which TS otherwise rejects since the initialiser narrows it to string
  let base: string | null = method;
  let discontinuing = false;
  let knownPlan = false;
  if (base.endsWith("+discontinuing")) {
    discontinuing = true;
    base = base.slice(0, -"+discontinuing".length);
  }
  if (base.endsWith("+known_plan")) {
    knownPlan = true;
    base = base.slice(0, -"+known_plan".length);
  } else if (base === "known_plan" || base === "tier2_known_plan") {
    knownPlan = true;
    base = null; // every month came from the real plan — no statistical method underneath
  }
  return { base, knownPlan, discontinuing, tier2 };
}

// Confidence tiers by hold-out WAPE — thresholds chosen from this session's
// own distribution analysis (184 scored SKUs: 6 at <=30%, 33 at 30-60%,
// 40 at 60-100%, the rest >100%). "poor" also dims the row's own numbers so
// a buyer can't mistake a noisy forecast for a precise one at a glance.
type ConfidenceTier = "good" | "rough" | "poor" | "unknown";

function confidenceTier(wape: number | null): ConfidenceTier {
  if (wape === null) return "unknown";
  if (wape <= 60) return "good";
  if (wape <= 100) return "rough";
  return "poor";
}

const CONFIDENCE_COLOR: Record<ConfidenceTier, string> = {
  good: "var(--green)",
  rough: "var(--amber)",
  poor: "var(--red)",
  unknown: "var(--faint)",
};

const CONFIDENCE_KEY: Record<ConfidenceTier, TranslationKey> = {
  good: "fc_confidence_good",
  rough: "fc_confidence_rough",
  poor: "fc_confidence_poor",
  unknown: "fc_confidence_unknown",
};

function ConfidenceDot({ wape }: { wape: number | null }) {
  const tier = confidenceTier(wape);
  return (
    <span
      title={tier}
      style={{
        display: "inline-block",
        width: 8,
        height: 8,
        borderRadius: "50%",
        background: CONFIDENCE_COLOR[tier],
        marginRight: 6,
        flexShrink: 0,
      }}
    />
  );
}

function DiscontinuingBadge({ label }: { label: string }) {
  return (
    <span
      style={{
        marginLeft: 6, fontSize: "0.68rem", fontWeight: 700, color: "var(--red)",
        whiteSpace: "nowrap",
      }}
      title={label}
    >
      ⚠ {label}
    </span>
  );
}

function fmtMonth(iso: string, lang: string): string {
  const d = new Date(iso);
  return lang === "ja"
    ? `${d.getFullYear()}/${d.getMonth() + 1}`
    : d.toLocaleDateString("en-US", { year: "2-digit", month: "short" });
}

const STOCK_STALE_DAYS = 10; // SAP ZM13 extract refreshes weekly (Mondays) — see CLAUDE.md

function fmtDate(iso: string, lang: string): string {
  const d = new Date(iso);
  return lang === "ja"
    ? `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
    : d.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

function isStale(iso: string): boolean {
  const days = (Date.now() - new Date(iso).getTime()) / 86400000;
  return days > STOCK_STALE_DAYS;
}

function fmtQty(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return Math.round(v).toLocaleString();
}

/** History bars (gray) + forecast bars (blue) + ONE-plan dashed line. */
function ForecastChart({ detail, lang }: { detail: ForecastDetail; lang: string }) {
  const hist = detail.history;
  const fcst = detail.forecast;
  const planByMonth = new Map(detail.one_plan.map((p) => [p.month, p.qty]));
  const all = [...hist.map((h) => ({ ...h, kind: "h" })), ...fcst.map((f) => ({ ...f, kind: "f" }))];
  if (!all.length) return null;

  const W = 760, H = 220, padL = 46, padB = 30, padT = 12;
  const max = Math.max(
    1,
    ...all.map((a) => a.qty ?? 0),
    ...detail.one_plan.map((p) => p.qty ?? 0),
  );
  const bw = (W - padL - 8) / all.length;
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);

  const planPts = all
    .map((a, i) => {
      const q = planByMonth.get(a.month);
      return q === undefined || q === null
        ? null
        : `${padL + i * bw + bw / 2},${y(q)}`;
    })
    .filter(Boolean)
    .join(" ");

  return (
    <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", height: "auto" }}>
      {[0, 0.5, 1].map((f) => (
        <g key={f}>
          <line
            x1={padL} x2={W - 4} y1={y(max * f)} y2={y(max * f)}
            stroke="currentColor" opacity={0.12}
          />
          <text x={padL - 6} y={y(max * f) + 4} textAnchor="end" fontSize={10} opacity={0.55}>
            {Math.round(max * f).toLocaleString()}
          </text>
        </g>
      ))}
      {all.map((a, i) => (
        <rect
          key={a.month}
          x={padL + i * bw + 1}
          y={y(a.qty ?? 0)}
          width={Math.max(1, bw - 2)}
          height={H - padB - y(a.qty ?? 0)}
          fill={a.kind === "h" ? "#a6adc4" : "#2c33b8"}
          opacity={a.kind === "h" ? 0.55 : 0.9}
        >
          <title>{`${fmtMonth(a.month, lang)}: ${fmtQty(a.qty)}`}</title>
        </rect>
      ))}
      {planPts && (
        <polyline points={planPts} fill="none" stroke="#d97706" strokeWidth={2} strokeDasharray="5 4" />
      )}
      {all.map((a, i) =>
        i % Math.ceil(all.length / 12) === 0 ? (
          <text
            key={`t${a.month}`}
            x={padL + i * bw + bw / 2}
            y={H - 10}
            textAnchor="middle"
            fontSize={9.5}
            opacity={0.6}
          >
            {fmtMonth(a.month, lang)}
          </text>
        ) : null,
      )}
    </svg>
  );
}

/** Small uppercase section label used inside the detail panel. */
function SectionLabel({ children }: { children: import("react").ReactNode }) {
  return (
    <div className="eyebrow" style={{ margin: "18px 0 8px" }}>
      {children}
    </div>
  );
}

/** Manual order-status tracking (Phase-1: no PO/ERP integration — a human
 * records what they actually did). Renders as: a "mark ordered" button with
 * an inline qty/date/note form when there's no active order_action, or a
 * status badge + confirm-received/dismiss actions when there is one. Used
 * both in the order-suggestions table (compact) and the per-item detail
 * panel's suggestion block. stopPropagation everywhere since its container
 * rows are themselves click-to-open-detail. */
function OrderActionControl({
  itemId, action, suggestedQty, onChanged, t, lang,
}: {
  itemId: number;
  action: OrderActionInfo | null;
  suggestedQty: number;
  onChanged: () => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
  lang: string;
}) {
  const [formOpen, setFormOpen] = useState(false);
  const [qty, setQty] = useState(String(suggestedQty || ""));
  const [expected, setExpected] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const stop = (e: { stopPropagation: () => void }) => e.stopPropagation();

  const submitMarkOrdered = async (e: React.MouseEvent) => {
    stop(e);
    setBusy(true);
    setErr(null);
    try {
      await api.markOrdered(itemId, {
        author: currentAuthor(),
        qty_ordered: qty.trim() ? Number(qty) : undefined,
        expected_arrival_date: expected || undefined,
        note: note.trim() || undefined,
      });
      setFormOpen(false);
      onChanged();
    } catch (ex: any) {
      setErr(String(ex.message ?? ex));
    } finally {
      setBusy(false);
    }
  };

  const confirmReceived = async (e: React.MouseEvent) => {
    stop(e);
    if (!action) return;
    setBusy(true);
    setErr(null);
    try {
      await api.confirmOrderReceived(action.id, currentAuthor());
      onChanged();
    } catch (ex: any) {
      setErr(String(ex.message ?? ex));
    } finally {
      setBusy(false);
    }
  };

  const dismissAction = async (e: React.MouseEvent) => {
    stop(e);
    if (!action) return;
    setBusy(true);
    setErr(null);
    try {
      await api.dismissOrderAction(action.id, currentAuthor());
      onChanged();
    } catch (ex: any) {
      setErr(String(ex.message ?? ex));
    } finally {
      setBusy(false);
    }
  };

  if (action) {
    return (
      <div onClick={stop} style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <span className={"order-action-badge" + (action.awaiting_confirmation ? " awaiting" : "")}>
          {action.awaiting_confirmation ? t("fc_order_awaiting_confirmation") : t("fc_order_ordered_badge")}
          {action.expected_arrival_date ? ` · ${fmtDate(action.expected_arrival_date, lang)}` : ""}
        </span>
        <div style={{ display: "flex", gap: 10 }}>
          <button className="btn-link" disabled={busy} onClick={confirmReceived}>
            {t("fc_order_confirm_received")}
          </button>
          <button className="btn-link danger" disabled={busy} onClick={dismissAction}>
            {t("fc_order_dismiss")}
          </button>
        </div>
        {err && <div style={{ color: "var(--red)", fontSize: "0.68rem" }}>{err}</div>}
      </div>
    );
  }

  if (!formOpen) {
    return (
      <button className="btn btn-sm" onClick={(e) => { stop(e); setFormOpen(true); }}>
        {t("fc_order_mark_ordered")}
      </button>
    );
  }

  return (
    <div onClick={stop} style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 170 }}>
      <input type="number" min={0} value={qty} onChange={(e) => setQty(e.target.value)}
            placeholder={t("fc_order_qty_placeholder")} style={{ width: "100%" }} />
      <input type="date" value={expected} onChange={(e) => setExpected(e.target.value)}
            title={t("fc_order_date_placeholder")} style={{ width: "100%" }} />
      <input type="text" value={note} onChange={(e) => setNote(e.target.value)}
            placeholder={t("fc_order_note_placeholder")} style={{ width: "100%" }} />
      <div style={{ display: "flex", gap: 6 }}>
        <button className="btn btn-sm primary" disabled={busy} onClick={submitMarkOrdered}>
          {busy ? t("fc_running") : t("fc_order_save")}
        </button>
        <button className="btn btn-sm" onClick={(e) => { stop(e); setFormOpen(false); }}>
          {t("fc_order_cancel")}
        </button>
      </div>
      {err && <div style={{ color: "var(--red)", fontSize: "0.68rem" }}>{err}</div>}
    </div>
  );
}

export default function Forecast() {
  const { t, lang } = useLang();
  const [summary, setSummary] = useState<ForecastSummary | null>(null);
  const [list, setList] = useState<ForecastList | null>(null);
  const [tier2Preview, setTier2Preview] = useState<ForecastList | null>(null);
  const [detail, setDetail] = useState<ForecastDetail | null>(null);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [methodFilter, setMethodFilter] = useState<string | null>(null);
  // ownership tier (Item.tier: 1=CPU/VGA/MB/Case, 2=everything purchasing
  // owns) — a DIFFERENT split from methodFilter's "tier2" (forecasting
  // method: only the minority with no history, attach-rate/category-avg
  // derived). Kept as separate state deliberately so the two are never
  // conflated in the UI the way they were before this was added.
  const [tierFilter, setTierFilter] = useState<1 | 2 | null>(null);
  const [running, setRunning] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [rebuildFlash, setRebuildFlash] = useState<string | null>(null);
  const [rebuildError, setRebuildError] = useState<string | null>(null);
  const [rebuildHistory, setRebuildHistory] = useState<RebuildHistoryEntry[]>([]);
  const [showRebuildHistory, setShowRebuildHistory] = useState(false);
  // Progress bar state. started_at comes from the SERVER, not from the click:
  // a rebuild triggered in another browser (or by the scheduler) must show
  // its real elapsed time when this page attaches to it mid-run, not restart
  // the clock at zero. `now` ticks once a second only while a rebuild runs.
  const [rebuildStartedAt, setRebuildStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [planVsActual, setPlanVsActual] = useState<PlanVsActual | null>(null);
  const [tuneMonth, setTuneMonth] = useState<string | null>(null);
  const [tuneValue, setTuneValue] = useState("");
  const [tuneReason, setTuneReason] = useState("");
  const [tuneSubmitting, setTuneSubmitting] = useState(false);
  const [tuneError, setTuneError] = useState<string | null>(null);
  const [orders, setOrders] = useState<OrderSuggestions | null>(null);
  const [showOrders, setShowOrders] = useState(false);
  const [orderSearch, setOrderSearch] = useState("");
  const [showOrderedItems, setShowOrderedItems] = useState(false);
  // Which ownership slice the Excel export covers: all parts, planning's
  // 4 categories (tier 1), or purchasing's derived parts (tier 2).
  const [exportTier, setExportTier] = useState<"all" | "1" | "2">("all");
  const listRef = useRef<HTMLDivElement | null>(null);
  const ordersRef = useRef<HTMLDivElement | null>(null);
  // Rebuild polling lives in refs, not component state: the timer must be
  // cancellable on unmount (tab switch) and must never be started twice.
  const rebuildTimer = useRef<number | null>(null);
  const rebuildPolling = useRef(false);

  const loadSummary = () =>
    api.forecastSummary().then(setSummary).catch((e) => setError(String(e.message ?? e)));

  const loadTier2Preview = () =>
    api.forecasts({ method: "tier2", limit: 10 }).then(setTier2Preview).catch(() => {});

  const loadOrders = () =>
    api.orderSuggestions().then(setOrders).catch((e) => setError(String(e.message ?? e)));

  // Poll /build/rebuild/status until it leaves "running". Transient fetch
  // failures are EXPECTED here — the backend is busy re-ingesting every NAS
  // source and a poll can time out — so a failed poll retries (slower)
  // instead of surfacing "Network Error"; only ~2 minutes of consecutive
  // failures gives up. Idempotent: a second call while active is a no-op,
  // so mount-resume and the button can't double-poll.
  const startRebuildPolling = () => {
    if (rebuildPolling.current) return;
    rebuildPolling.current = true;
    setRebuilding(true);
    let failures = 0;
    const tick = async () => {
      try {
        const s = await api.rebuildStatus();
        failures = 0;
        if (s.status === "running") {
          // started_at carries an explicit +00:00 offset (backend
          // onefcst/timeutil) — no manual "Z" patching. The four sites that
          // used to append one silently broke as soon as the field gained a
          // real offset ("...+00:00Z" parses as Invalid Date).
          if (s.started_at) setRebuildStartedAt(new Date(s.started_at).getTime());
          rebuildTimer.current = window.setTimeout(tick, 3000);
          return;
        }
        rebuildPolling.current = false;
        setRebuilding(false);
        setRebuildStartedAt(null);
        api.rebuildHistory(10).then(setRebuildHistory).catch(() => {});
        if (s.status === "done") {
          const ship = s.report?.sap_shipments;
          const nas = s.report?.nas_refresh;
          const base = ship
            ? t("fc_rebuild_done_with_shipments", { resolved: ship.resolved, unresolved: ship.unresolved })
            : t("fc_rebuild_done");
          const nasNote =
            nas?.status === "ok"
              ? t("fc_rebuild_nas_refreshed", { files: nas.files_in_cache ?? 0 })
              : nas?.status === "skipped"
                ? t("fc_rebuild_nas_skipped")
                : nas?.status === "error"
                  ? t("fc_rebuild_nas_error", { reason: nas.reason ?? "" })
                  : "";
          setRebuildFlash(nasNote ? `${base} ${nasNote}` : base);
          loadSummary();
        } else if (s.status === "error") {
          setRebuildError(s.error ?? t("fc_rebuild_error_unknown"));
        }
      } catch (e: unknown) {
        failures += 1;
        if (failures < 20) {
          rebuildTimer.current = window.setTimeout(tick, 6000);
          return;
        }
        rebuildPolling.current = false;
        setRebuilding(false);
        setRebuildStartedAt(null);
        setRebuildError(String((e as Error).message ?? e));
      }
    };
    tick();
  };

  // 1 Hz clock, alive ONLY while a rebuild is running — no idle re-render.
  useEffect(() => {
    if (!rebuilding) return;
    setNow(Date.now());
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [rebuilding]);

  // Expected duration, learned from this deployment's own past rebuilds
  // (median of completed ones) rather than hard-coded: NAS speed and source
  // size differ per site. Falls back to 7 min, the observed local figure.
  const rebuildEstimateMs = useMemo(() => {
    const durations = rebuildHistory
      .filter((h) => h.status === "done" && h.started_at && h.finished_at)
      .map((h) => new Date(h.finished_at!).getTime() - new Date(h.started_at).getTime())
      .filter((d) => d > 5000)
      .sort((a, b) => a - b);
    if (!durations.length) return 7 * 60_000;
    return durations[Math.floor(durations.length / 2)];
  }, [rebuildHistory]);

  const rebuildElapsedMs = rebuildStartedAt ? Math.max(0, now - rebuildStartedAt) : 0;
  // Deliberately capped at 95%: the bar must never sit at 100% while work is
  // still going — that reads as "hung". It completes only when the poll says
  // the rebuild finished, at which point the success banner replaces it.
  const rebuildPct = Math.min(95, (rebuildElapsedMs / rebuildEstimateMs) * 95);
  const fmtDuration = (ms: number) => {
    const s = Math.floor(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };

  useEffect(() => {
    loadSummary();
    loadTier2Preview();
    api.rebuildHistory(10).then(setRebuildHistory).catch(() => {});
    // A rebuild started before this mount (other tab, other browser, or a
    // scheduled one) must re-attach: without this, navigating away and back
    // silently dropped the progress indicator.
    api.rebuildStatus()
      .then((s) => { if (s.status === "running") startRebuildPolling(); })
      .catch(() => {});
    return () => {
      // Unmount (tab switch): stop the timer so no setState fires on a dead
      // component; the mount check above resumes polling on return.
      if (rebuildTimer.current !== null) window.clearTimeout(rebuildTimer.current);
      rebuildPolling.current = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Lazy-load: the order-suggestions detail is only fetched the first time
  // the KPI card is opened, not on every page load.
  const toggleOrders = () => {
    const next = !showOrders;
    setShowOrders(next);
    if (next) {
      if (!orders) loadOrders();
      requestAnimationFrame(() =>
        ordersRef.current?.scrollIntoView({ behavior: "smooth", block: "start" }),
      );
    }
  };

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .forecasts({ q: q || undefined, category: category ?? undefined,
                   method: methodFilter ?? undefined, tier: tierFilter ?? undefined, limit: 80 })
        .then(setList)
        .catch((e) => setError(String(e.message ?? e)));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, category, methodFilter, tierFilter]);

  const rebuildFromNas = async () => {
    setRebuilding(true);
    setRebuildFlash(null);
    setRebuildError(null);
    try {
      await api.triggerRebuild();
    } catch (e: unknown) {
      // 409 = a rebuild was already running elsewhere — fine, just poll it
      const status = (e as { response?: { status?: number } })?.response?.status;
      if (status !== 409) {
        setRebuildError(String((e as Error).message ?? e));
        setRebuilding(false);
        return;
      }
    }
    startRebuildPolling();
  };

  const run = async () => {
    setRunning(true);
    setFlash(null);
    try {
      const r = await api.runForecast();
      setFlash(t("fc_run_done", { id: r.run_id }));
      await loadSummary();
      await loadTier2Preview();
      if (orders) await loadOrders();
      setList(await api.forecasts({ q: q || undefined, category: category ?? undefined,
                                  method: methodFilter ?? undefined, tier: tierFilter ?? undefined,
                                  limit: 80 }));
    } catch (e: unknown) {
      setError(String((e as Error).message ?? e));
    } finally {
      setRunning(false);
    }
  };

  const openDetail = (itemId: number) => {
    api.forecastDetail(itemId).then(setDetail).catch((e) => setError(String(e.message ?? e)));
    api.planVsActual(itemId).then(setPlanVsActual).catch(() => setPlanVsActual(null));
  };

  const startTune = (month: string, currentPlanned: number) => {
    setTuneMonth(month);
    setTuneValue(String(Math.round(currentPlanned)));
    setTuneReason("");
    setTuneError(null);
  };

  const submitTune = async () => {
    if (!detail || !tuneMonth) return;
    const newValue = Number(tuneValue);
    if (!Number.isFinite(newValue) || newValue < 0) {
      setTuneError(t("fc_tune_invalid_value"));
      return;
    }
    if (!tuneReason.trim()) {
      setTuneError(t("fc_tune_missing_reason"));
      return;
    }
    setTuneSubmitting(true);
    setTuneError(null);
    try {
      const author = currentAuthor();
      await api.createAdjustment({
        item_id: detail.item.id, month: tuneMonth, new_value: newValue,
        scope: "sku", reason: tuneReason.trim(), author,
      });
      setTuneMonth(null);
      await openDetail(detail.item.id);
    } catch (e: unknown) {
      setTuneError(String((e as Error).message ?? e));
    } finally {
      setTuneSubmitting(false);
    }
  };

  // Deep-link support: /forecast?item=123 (used by the Adjustments page's
  // "view in forecast" link) opens that item's detail automatically, so
  // cross-checking a recorded adjustment against the live forecast is one
  // click instead of a manual name search.
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const itemParam = searchParams.get("item");
    if (itemParam) {
      openDetail(Number(itemParam));
      setSearchParams({}, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const run_ = summary?.run;
  const holdout = run_?.holdout?.overall;
  const nearTerm = run_?.holdout?.near_term;
  const tier2 = run_?.tier2;
  const tier2Total = tier2
    ? tier2.items_forecasted + tier2.category_fallback.items_forecasted
    : 0;
  const next3All = useMemo(
    () => (summary ? summary.categories.reduce((s, c) => s + c.next3_total, 0) : 0),
    [summary],
  );
  const maxNext6 = useMemo(
    () =>
      summary
        ? Math.max(1, ...summary.categories.map((c) => c.next6_total))
        : 1,
    [summary],
  );
  const monthCols = list?.months.slice(0, 6) ?? [];

  const methodLabel = (m: string | null) => {
    const { base, tier2 } = parseMethod(m);
    if (base === null) return t(tier2 ? "fc_method_tier2_known_plan" : "fc_method_known_plan");
    return METHOD_KEY[base] ? t(METHOD_KEY[base]) : base;
  };

  const viewAllTier2 = () => {
    setCategory(null);
    setMethodFilter("tier2");
    listRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const lastRunAt = run_?.created_at
    ? new Date(run_.created_at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")
    : "—";

  return (
    <div>
      {/* ── Analytical-list header: title + primary actions + run facts ── */}
      <div className="hero" style={{ padding: "22px 30px" }}>
        <div
          style={{
            display: "flex", justifyContent: "space-between", alignItems: "flex-start",
            gap: 16, flexWrap: "wrap", position: "relative",
          }}
        >
          <div>
            <h2>{t("fc_title")}</h2>
            <p>{t("fc_sub")}</p>
          </div>
          <div style={{ display: "flex", gap: 8, flexShrink: 0, alignItems: "center", flexWrap: "wrap" }}>
            <select
              value={exportTier}
              onChange={(e) => setExportTier(e.target.value as "all" | "1" | "2")}
              title={t("fc_export_scope_hint")}
              style={{ padding: "6px 8px" }}
            >
              <option value="all">{t("fc_export_scope_all")}</option>
              <option value="1">{t("fc_export_scope_tier1")}</option>
              <option value="2">{t("fc_export_scope_tier2")}</option>
            </select>
            <button
              className="btn"
              onClick={() =>
                downloadReport(
                  exportTier === "all" ? "/export/forecasts" : `/export/forecasts?tier=${exportTier}`,
                  exportTier === "1" ? "需要予測_企画4部材.xlsx"
                    : exportTier === "2" ? "需要予測_購買部材.xlsx"
                    : "需要予測_全部材.xlsx",
                )
              }
            >
              {t("common_download_excel")}
            </button>
            <button className="btn" onClick={rebuildFromNas} disabled={rebuilding} title={t("fc_rebuild_hint")}>
              {rebuilding ? t("fc_rebuilding") : t("fc_rebuild")}
            </button>
            <button className="btn" onClick={run} disabled={running}>
              {running ? t("fc_running") : t("fc_run")}
            </button>
          </div>
        </div>
        {rebuilding && (
          <div className="banner info rebuild-progress">
            <span aria-hidden>⏳</span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
                <strong>{t("fc_rebuild_progress_title")}</strong>
                <span className="mono dim" style={{ fontSize: "0.76rem", flexShrink: 0 }}>
                  {rebuildStartedAt
                    ? `${fmtDuration(rebuildElapsedMs)} / ~${fmtDuration(rebuildEstimateMs)}`
                    : t("fc_rebuild_progress_starting")}
                </span>
              </div>
              <div
                className="rebuild-bar"
                role="progressbar"
                aria-valuemin={0}
                aria-valuemax={100}
                aria-valuenow={Math.round(rebuildPct)}
              >
                <i style={{ width: `${rebuildStartedAt ? rebuildPct : 8}%` }} />
              </div>
              <div className="dim" style={{ fontSize: "0.74rem", marginTop: 5 }}>
                {t("fc_rebuild_progress_hint")}
              </div>
            </div>
          </div>
        )}
        {rebuildFlash && <div className="banner info">✅ {rebuildFlash}</div>}
        {rebuildError && <div className="banner warn">⚠️ {t("fc_rebuild_error_prefix")}: {rebuildError}</div>}
        {rebuildHistory.length > 0 && (
          <div className="rebuild-history">
            {/* A disclosure control, not a primary action — .btn made it
                compete visually with "Rebuild from NAS" right above it. */}
            <button
              type="button"
              className="rebuild-history-toggle"
              aria-expanded={showRebuildHistory}
              onClick={() => setShowRebuildHistory((v) => !v)}
            >
              <span className="chev" aria-hidden>{showRebuildHistory ? "▾" : "▸"}</span>
              {showRebuildHistory ? t("fc_rebuild_history_hide") : t("fc_rebuild_history_show")}
              <span className="count">{rebuildHistory.length}</span>
            </button>
            {showRebuildHistory && (
              /* Own light surface: this sits on the dark page header, and the
                 global `tbody td:first-child` sticky column paints an opaque
                 var(--card) background — on the dark header that rendered as
                 white text on a white block, i.e. an invisible Trigger column. */
              <div className="tablewrap rebuild-history-table" style={{ maxHeight: 220 }}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("fc_rebuild_history_th_trigger")}</th>
                      <th>{t("fc_rebuild_history_th_when")}</th>
                      <th>{t("fc_rebuild_history_th_status")}</th>
                      <th>{t("fc_rebuild_history_th_detail")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rebuildHistory.map((h) => (
                      <tr key={h.id}>
                        <td>
                          <span className={`trigger-chip ${h.trigger === "manual" ? "manual" : "auto"}`}>
                            {h.trigger === "manual"
                              ? t("fc_rebuild_history_manual")
                              : t("fc_rebuild_history_scheduled")}
                          </span>
                        </td>
                        <td className="dim mono" style={{ fontSize: "0.78rem" }}>
                          {new Date(h.started_at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")}
                        </td>
                        <td>
                          {h.status === "running" && <span className="pill">{t("fc_rebuilding")}</span>}
                          {h.status === "done" && <span style={{ color: "var(--green)" }}>✅ {t("fc_rebuild_history_done")}</span>}
                          {h.status === "error" && <span style={{ color: "var(--red)" }}>⚠️ {t("fc_rebuild_history_failed")}</span>}
                        </td>
                        <td className="dim" style={{ fontSize: "0.78rem" }}>
                          {h.status === "error"
                            ? h.error
                            : h.report?.sap_shipments
                              ? t("fc_rebuild_history_shipments_detail", {
                                  resolved: h.report.sap_shipments.resolved,
                                  unresolved: h.report.sap_shipments.unresolved,
                                })
                              : "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}
        <div className="stats" style={{ marginTop: 14 }}>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "最終実行" : "Last run"}</div>
            <div className="val" style={{ fontSize: "1.05rem" }}>{lastRunAt}</div>
          </div>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "モデル" : "Model"}</div>
            <div className="val" style={{ fontSize: "1.05rem" }}>
              {run_?.method_version ?? "—"}
            </div>
          </div>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "予測期間" : "Horizon"}</div>
            <div className="val" style={{ fontSize: "1.05rem" }}>
              {run_?.horizon != null
                ? lang === "ja" ? `${run_.horizon}ヶ月` : `${run_.horizon} mo`
                : "—"}
            </div>
          </div>
        </div>
      </div>

      {error && <div className="banner warn">⚠️ {error}</div>}
      {flash && <div className="banner info">✅ {flash}</div>}
      {summary && !summary.run && <div className="banner info">ℹ️ {t("fc_no_run")}</div>}

      {/* ── KPI band ── */}
      <div className="kpis">
        <div className="kpi">
          <div className="lab">{t("fc_kpi_items")}</div>
          <div className="val">{run_?.items_forecasted?.toLocaleString() ?? "—"}</div>
          <div className="sub">
            {t("fc_kpi_items_sub", { n: run_?.items_skipped_stale ?? "—" })}
          </div>
        </div>
        <div className="kpi">
          <div className="lab">{t("fc_kpi_next3")}</div>
          <div className="val">{summary ? next3All.toLocaleString() : "—"}</div>
          <div className="sub">
            {summary?.excluded_no_stock_tracking && summary.excluded_no_stock_tracking.items > 0
              ? t("fc_kpi_next3_excluded_sub", {
                  n: summary.excluded_no_stock_tracking.items,
                  vol: summary.excluded_no_stock_tracking.next3_total.toLocaleString(),
                })
              : t("fc_kpi_next3_sub")}
          </div>
        </div>
        <div
          className="kpi accent-amber"
          onClick={toggleOrders}
          style={{ cursor: "pointer", outline: showOrders ? "2px solid var(--amber)" : undefined }}
          title={t("fc_kpi_orders_sub")}
        >
          <div className="lab">{t("fc_kpi_orders")}</div>
          <div className="val" style={{ color: "var(--amber)" }}>
            {summary?.orders_needed ?? "—"}
          </div>
          <div className="sub">{t("fc_kpi_orders_sub")}</div>
        </div>
        <div className="kpi">
          <div className="lab">{t("fc_kpi_wape")}</div>
          <div className="val">{holdout ? `${holdout.wape}%` : "—"}</div>
          <div className="sub">
            {holdout
              ? t("fc_kpi_wape_sub", { n: holdout.items, naive: holdout.naive_wape })
              : "—"}
          </div>
        </div>
        <div className="kpi accent-green">
          <div className="lab">{t("fc_kpi_wape_near")}</div>
          <div className="val" style={{ color: "var(--green)" }}>
            {nearTerm ? `${nearTerm.wape}%` : "—"}
          </div>
          <div className="sub">
            {nearTerm ? t("fc_kpi_wape_near_sub", { naive: nearTerm.naive_wape }) : "—"}
          </div>
        </div>
        <div
          className="kpi"
          onClick={() => setMethodFilter(methodFilter === "tier2" ? null : "tier2")}
          style={{
            cursor: "pointer",
            outline: methodFilter === "tier2" ? "2px solid var(--brand-500)" : undefined,
          }}
          title={t("fc_kpi_tier2_hint")}
        >
          <div className="lab">{t("fc_kpi_tier2")}</div>
          <div className="val">{tier2 ? tier2Total.toLocaleString() : "—"}</div>
          <div className="sub">
            {tier2
              ? t("fc_kpi_tier2_sub", { cat: tier2.category_fallback.leave_one_out_wape ?? "—" })
              : "—"}
          </div>
        </div>
      </div>

      {/* ── Order suggestions detail — opens when the KPI above is clicked ── */}
      {showOrders && (
        <div className="card stripe-card brand" ref={ordersRef} style={{ marginTop: 18 }}>
          <div className="hd">
            <div>
              <h3>{t("fc_orders_panel_title")}</h3>
              <div className="desc">{t("fc_orders_panel_desc")}</div>
            </div>
            <button className="btn" onClick={() => setShowOrders(false)}>
              {t("fc_orders_close")}
            </button>
          </div>
          {!orders ? (
            <div className="empty">{t("common_loading")}</div>
          ) : (
            <>
              <div
                style={{
                  display: "flex", gap: 32, flexWrap: "wrap", alignItems: "center",
                  padding: "2px 24px 14px",
                }}
              >
                <div className="statline">
                  <span className="n">{orders.total.toLocaleString()}</span>
                  <span className="c">{t("fc_orders_stat_items")}</span>
                </div>
                <div className="statline">
                  <span className="n">{orders.total_qty.toLocaleString()}</span>
                  <span className="c">{t("fc_orders_stat_qty")}</span>
                </div>
                <div className="statline">
                  <span className="n" style={{ color: orders.negative_stock_count > 0 ? "var(--red)" : undefined }}>
                    {orders.negative_stock_count.toLocaleString()}
                  </span>
                  <span className="c">{t("fc_orders_stat_negative")}</span>
                </div>
                <div className="statline">
                  <span className="n" style={{ color: "var(--amber)" }}>
                    {(orders.by_month.find((m) => m.month === orders.run?.start_month)?.items ?? 0).toLocaleString()}
                  </span>
                  <span className="c">{t("fc_orders_stat_this_month")}</span>
                </div>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, padding: "0 24px 16px" }}>
                <div>
                  <div className="eyebrow">{t("fc_orders_by_category_title")}</div>
                  <div className="tablewrap" style={{ maxHeight: 200 }}>
                    <table>
                      <tbody>
                        {orders.by_category.map((c) => (
                          <tr key={c.category}>
                            <td>{c.category}</td>
                            <td className="num mono dim">{c.items}</td>
                            <td className="num mono" style={{ fontWeight: 600 }}>
                              {c.qty.toLocaleString()}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
                <div>
                  <div className="eyebrow">{t("fc_orders_by_month_title")}</div>
                  <div className="tablewrap" style={{ maxHeight: 200 }}>
                    <table>
                      <tbody>
                        {orders.by_month.map((m) => (
                          <tr key={m.month}>
                            <td className="mono">{fmtMonth(m.month, lang)}</td>
                            <td className="num mono" style={{ fontWeight: 600 }}>
                              {m.items}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>

              <div className="toolbar" style={{ alignItems: "center" }}>
                <input
                  value={orderSearch}
                  onChange={(e) => setOrderSearch(e.target.value)}
                  placeholder={t("fc_orders_search_placeholder")}
                  style={{ minWidth: 260 }}
                />
                <label className="dim" style={{ display: "flex", alignItems: "center", gap: 6, fontSize: "0.8rem" }}>
                  <input type="checkbox" checked={showOrderedItems}
                        onChange={(e) => setShowOrderedItems(e.target.checked)} />
                  {t("fc_orders_show_ordered_toggle")}
                </label>
              </div>
              {(() => {
                let filtered = orderSearch
                  ? orders.rows.filter((r) =>
                      r.name.toLowerCase().includes(orderSearch.toLowerCase()),
                    )
                  : orders.rows;
                if (!showOrderedItems) {
                  filtered = filtered.filter(
                    (r) => !r.order_action || r.order_action.awaiting_confirmation,
                  );
                }
                return (
                  <>
                    <div className="tablewrap" style={{ maxHeight: 460 }}>
                      <table>
                        <thead>
                          <tr>
                            <th>{t("fc_orders_th_item")}</th>
                            <th>{t("fc_orders_th_category")}</th>
                            <th className="num">{t("fc_orders_th_stock")}</th>
                            <th className="num">{t("fc_orders_th_safety")}</th>
                            <th className="num">{t("fc_orders_th_qty")}</th>
                            <th>{t("fc_orders_th_order_by")}</th>
                            <th>{t("fc_orders_th_status")}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {filtered.map((r) => {
                            const negative = r.stock !== null && r.stock < 0;
                            const { discontinuing } = parseMethod(r.method);
                            return (
                              <tr key={r.item_id} onClick={() => openDetail(r.item_id)} style={{ cursor: "pointer" }}>
                                <td style={{ fontWeight: 600 }}>
                                  {r.name}
                                  {discontinuing && <DiscontinuingBadge label={t("fc_discontinuing_badge")} />}
                                </td>
                                <td className="dim">{r.category ?? "—"}</td>
                                <td className="num mono" style={{ color: negative ? "var(--red)" : undefined }}>
                                  {fmtQty(r.stock)}
                                  {negative && (
                                    <span
                                      style={{ marginLeft: 6, fontSize: "0.68rem", fontWeight: 700, color: "var(--red)" }}
                                    >
                                      {t("fc_orders_negative_badge")}
                                    </span>
                                  )}
                                  {r.stock_confidence !== "sap_verified" && (
                                    <span
                                      style={{ marginLeft: 6, fontSize: "0.68rem", fontWeight: 700, color: "var(--amber)", whiteSpace: "nowrap" }}
                                      title={t(
                                        r.stock_confidence === "one_only_negative_clamped"
                                          ? "fc_orders_low_confidence_tooltip_clamped"
                                          : "fc_orders_low_confidence_tooltip_unverified",
                                      )}
                                    >
                                      ⚠ {t("fc_orders_low_confidence_badge")}
                                    </span>
                                  )}
                                </td>
                                <td className="num mono dim">{fmtQty(r.safety_stock)}</td>
                                <td className="num mono" style={{ fontWeight: 700, color: "var(--amber)" }}>
                                  {fmtQty(r.order_qty)}
                                  {r.moq_excessive && (
                                    <DiscontinuingBadge
                                      label={t("fc_orders_moq_excessive_badge", {
                                        months: r.moq_months_of_cover ?? 0,
                                      })}
                                    />
                                  )}
                                </td>
                                <td className="mono">{fmtMonth(r.order_by, lang)}</td>
                                <td>
                                  <OrderActionControl itemId={r.item_id} action={r.order_action}
                                                      suggestedQty={r.order_qty} onChanged={loadOrders}
                                                      t={t} lang={lang} />
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    </div>
                    <div className="dim" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
                      {t("fc_orders_showing", { shown: filtered.length, total: orders.total })}
                    </div>
                  </>
                );
              })()}
            </>
          )}
        </div>
      )}

      {/* ── Category breakdown + Tier-2 side by side when both exist ── */}
      <div className={tier2 && tier2Total > 0 ? "grid-13" : undefined}>
        <div className="card">
          <div className="hd">
            <div>
              <h3>{t("fc_cat_title")}</h3>
              <div className="desc">
                {t("fc_cat_desc")}
                {run_?.created_at && (
                  <>
                    {" — "}
                    {t("fc_last_run", { at: lastRunAt })}
                  </>
                )}
              </div>
            </div>
            {category && (
              <button className="chip on" onClick={() => setCategory(null)} title={t("fc_filter_all")}>
                {category} ✕
              </button>
            )}
          </div>
          {summary ? (
            <div className="tablewrap" style={{ maxHeight: 420 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("fc_th_category")}</th>
                    <th className="num">{t("fc_th_items")}</th>
                    <th className="num">{t("fc_th_next3")}</th>
                    <th className="num">{t("fc_th_next6")}</th>
                    <th style={{ width: 96 }} />
                  </tr>
                </thead>
                <tbody>
                  {summary.categories.map((c) => {
                    const selected = category === c.category;
                    return (
                      <tr
                        key={c.category}
                        onClick={() => setCategory(selected ? null : c.category)}
                        style={{
                          cursor: "pointer",
                          background: selected ? "var(--brand-bg, rgba(30,33,136,0.08))" : undefined,
                          boxShadow: selected
                            ? "inset 3px 0 0 var(--brand-500)"
                            : undefined,
                        }}
                      >
                        <td style={{ fontWeight: 600 }}>{c.category}</td>
                        <td className="num mono dim">{c.items}</td>
                        <td className="num mono">{c.next3_total.toLocaleString()}</td>
                        <td className="num mono" style={{ fontWeight: 700 }}>
                          {c.next6_total.toLocaleString()}
                        </td>
                        <td>
                          <span
                            className="minibar"
                            style={{
                              width: `${Math.max(3, Math.round((c.next6_total / maxNext6) * 88))}px`,
                              opacity: selected ? 1 : 0.85,
                            }}
                            title={c.next6_total.toLocaleString()}
                          />
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="empty">{t("common_loading")}</div>
          )}
        </div>

        {tier2 && tier2Total > 0 && (
          <div className="card stripe-card brand">
            <div className="hd">
              <div>
                <div className="eyebrow" style={{ marginBottom: 3 }}>
                  {lang === "ja" ? "TIER-2 / 推定・派生予測" : "TIER-2 / ESTIMATED, NOT COMMITTED"}
                </div>
                <h3>{t("fc_tier2_title")}</h3>
                <div className="desc">{t("fc_tier2_desc")}</div>
              </div>
              <button className="btn" onClick={viewAllTier2}>
                {t("fc_tier2_view_all")}
              </button>
            </div>
            <div
              style={{
                display: "flex", gap: 32, flexWrap: "wrap", alignItems: "center",
                padding: "2px 24px 14px",
              }}
            >
              <div className="statline">
                <span className="n">{tier2.items_forecasted.toLocaleString()}</span>
                <span className="c">{t("fc_tier2_attach")}</span>
              </div>
              <div className="statline">
                <span className="n">
                  {tier2.category_fallback.items_forecasted.toLocaleString()}
                </span>
                <span className="c">{t("fc_tier2_category")}</span>
              </div>
              {tier2.category_fallback.leave_one_out_wape !== null && (
                <div className="statline">
                  <span className="n">{tier2.category_fallback.leave_one_out_wape}%</span>
                  <span className="c">{t("fc_tier2_loo_wape")}</span>
                </div>
              )}
            </div>
            <div className="eyebrow" style={{ padding: "0 24px 6px" }}>
              {t("fc_tier2_top_title")}
            </div>
            {tier2Preview && tier2Preview.rows.length > 0 ? (
              <div className="tablewrap" style={{ maxHeight: 320 }}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("fc_th_item")}</th>
                      <th>{t("fc_th_category")}</th>
                      <th>{t("fc_th_method")}</th>
                      <th className="num">{t("fc_th_wape")}</th>
                      <th className="num">{t("fc_th_total6")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tier2Preview.rows.map((r) => (
                      <tr key={r.item_id} onClick={() => openDetail(r.item_id)} style={{ cursor: "pointer" }}>
                        <td style={{ fontWeight: 600 }}>
                          {r.name}
                          {parseMethod(r.method).discontinuing && (
                            <DiscontinuingBadge label={t("fc_discontinuing_badge")} />
                          )}
                        </td>
                        <td className="dim">{r.category ?? "—"}</td>
                        <td className="dim">{methodLabel(r.method)}</td>
                        <td className="num mono dim"
                            title={r.wape_full_horizon !== null
                                  ? t("fc_wape_full_horizon_tooltip", { wape: r.wape_full_horizon })
                                  : undefined}>
                          <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end" }}>
                            <ConfidenceDot wape={r.wape} />
                            {r.wape !== null ? `${r.wape}%` : "—"}
                          </span>
                        </td>
                        <td className="num mono" style={{ fontWeight: 700 }}>
                          {r.next6_total.toLocaleString()}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <div className="empty">{t("common_loading")}</div>
            )}
          </div>
        )}
      </div>

      {/* ── Detail panel ── */}
      {detail && (
        <div className="card stripe-card brand" style={{ marginTop: 18 }}>
          <div className="hd">
            <div>
              <h3>
                {t("fc_detail_title", { name: detail.item.name })}
                {parseMethod(detail.method).discontinuing && (
                  <DiscontinuingBadge label={t("fc_discontinuing_badge")} />
                )}
              </h3>
              {/* The title above is the 綽名 (nickname), which is often opaque
                  on its own — "RX7600" doesn't say which board, "M02" says
                  nothing. SAP's full product description goes right under it. */}
              {detail.item.product_name && (
                <div
                  style={{
                    fontSize: "0.82rem", fontWeight: 600, color: "var(--ink)",
                    opacity: 0.72, margin: "2px 0 4px",
                  }}
                  title={t("fc_detail_product_name_tooltip")}
                >
                  {detail.item.product_name}
                </div>
              )}
              <div className="desc">
                {detail.item.category} ・ {methodLabel(detail.method)}
                {detail.item.moq ? ` ・ ${t("fc_moq")}: ${fmtQty(detail.item.moq)}` : ""}
                {detail.item.effective_lead_time_days
                  ? ` ・ ${t("fc_lead")}: ${t("fc_lead_days", { n: Math.round(detail.item.effective_lead_time_days) })}` +
                    (detail.item.observed_lead_time_days != null &&
                     (detail.item.observed_lead_time_samples ?? 0) >= 3
                      ? ` (${t("fc_lead_observed", { n: detail.item.observed_lead_time_samples ?? 0 })})`
                      : "")
                  : ""}
                {detail.stock
                  ? ` ・ ${t("fc_stock_now")}: ${fmtQty(detail.stock.sap_qty ?? detail.stock.one_balance)}`
                  : ""}
                {detail.stock?.snapshot_date && (
                  <span style={isStale(detail.stock.snapshot_date) ? { color: "var(--red)" } : undefined}>
                    {" "}({t("fc_stock_asof", { date: fmtDate(detail.stock.snapshot_date, lang) })}
                    {isStale(detail.stock.snapshot_date) ? ` ・ ${t("fc_stock_stale")}` : ""})
                  </span>
                )}
              </div>
              {parseMethod(detail.method).discontinuing && (
                <div className="dim" style={{ fontSize: "0.76rem", marginTop: 4, color: "var(--red)" }}>
                  {t("fc_discontinuing_tooltip")}
                </div>
              )}
            </div>
            <button className="btn" onClick={() => { setDetail(null); setPlanVsActual(null); setTuneMonth(null); }}>
              {t("fc_detail_close")}
            </button>
          </div>
          <div style={{ padding: "6px 24px 22px" }}>
            <SectionLabel>{lang === "ja" ? "チャート" : "Chart"}</SectionLabel>
            <div className="dim" style={{ fontSize: "0.8rem", marginBottom: 8 }}>
              <span style={{ color: "#a6adc4" }}>■</span> {t("fc_legend_history")}{"  "}
              <span style={{ color: "#2c33b8" }}>■</span> {t("fc_legend_forecast")}{"  "}
              <span style={{ color: "#d97706" }}>┅</span> {t("fc_legend_plan")}
            </div>
            <ForecastChart detail={detail} lang={lang} />
            <div className="dim" style={{ fontSize: "0.8rem", margin: "8px 0 4px", display: "flex", alignItems: "center" }}>
              <ConfidenceDot wape={detail.wape} />
              {detail.wape !== null
                ? t("fc_detail_wape_note", { wape: detail.wape })
                : t("fc_detail_no_wape", { method: methodLabel(detail.method) })}
            </div>
            {detail.wape_full_horizon !== null && (
              <div className="dim" style={{ fontSize: "0.76rem", margin: "0 0 4px" }}>
                {t("fc_detail_wape_full_horizon_note", { wape: detail.wape_full_horizon })}
              </div>
            )}

            {/* The standalone "Weekly breakdown (next 3 months)" table was
                removed 2026-07-29: its numbers are the same week-split that
                Planned-vs-Actual below already shows in its Planned column,
                only without the actuals to compare against. One weekly table
                per SKU, not two. `detail.weekly` is still served by the API
                and is what the Planned column is built from. */}

            {planVsActual && planVsActual.weeks.length > 0 && (
              <>
                <SectionLabel>{t("fc_pva_title")}</SectionLabel>
                <div className="card" style={{ margin: "0 0 4px" }}>
                  <div className="dim" style={{ fontSize: "0.78rem", padding: "12px 16px 10px" }}>
                    {t("fc_pva_desc")}
                  </div>
                  <div className="tablewrap" style={{ maxHeight: 320, padding: "0 4px 8px" }}>
                    <table>
                      <thead>
                        <tr>
                          <th>{t("fc_pva_th_week")}</th>
                          <th className="num">{t("fc_pva_th_planned")}</th>
                          <th className="num">{t("fc_pva_th_actual")}</th>
                          <th className="num">{t("fc_pva_th_variance")}</th>
                          <th></th>
                        </tr>
                      </thead>
                      <tbody>
                        {(() => {
                          const seenMonths = new Set<string>();
                          let dividerShown = false;
                          return planVsActual.weeks.flatMap((w) => {
                            const isFirstOfMonth = !seenMonths.has(w.month);
                            seenMonths.add(w.month);
                            const rows = [];
                            if (!w.is_history && !dividerShown) {
                              dividerShown = true;
                              rows.push(
                                <tr key="divider">
                                  <td colSpan={5} className="dim" style={{ fontSize: "0.72rem", padding: "4px 8px", borderTop: "1px dashed var(--border)", borderBottom: "1px dashed var(--border)" }}>
                                    {t("fc_pva_forecast_divider")}
                                  </td>
                                </tr>
                              );
                            }
                            rows.push(
                              <tr key={w.week_start} style={w.is_history ? { opacity: 0.65 } : undefined}>
                                <td className="mono">
                                  {fmtMonth(w.week_start, lang)} ({w.week_start.slice(8, 10)})
                                </td>
                                <td className="num mono">
                                  {w.planned_qty !== null ? fmtQty(w.planned_qty) : "—"}
                                  {w.has_adjustment && (
                                    <span title={t("fc_pva_tuned_badge")} style={{ marginLeft: 4, color: "var(--amber)" }}>
                                      ✎
                                    </span>
                                  )}
                                </td>
                                <td className="num mono">
                                  {w.actual_qty !== null ? fmtQty(w.actual_qty) : "—"}
                                </td>
                                <td
                                  className="num mono"
                                  style={{
                                    color: w.variance_qty === null ? undefined
                                          : w.variance_qty < 0 ? "var(--red)" : "var(--green)",
                                  }}
                                >
                                  {w.variance_qty !== null ? (
                                    <>
                                      {w.variance_qty > 0 ? "+" : ""}{fmtQty(w.variance_qty)}
                                      {w.variance_pct !== null && (
                                        <span className="dim" style={{ marginLeft: 4, fontSize: "0.85em" }}>
                                          ({w.variance_pct > 0 ? "+" : ""}{w.variance_pct}%)
                                        </span>
                                      )}
                                    </>
                                  ) : "—"}
                                </td>
                                <td>
                                  {isFirstOfMonth && !w.is_history && w.planned_qty !== null && (
                                    <button
                                      className="btn"
                                      style={{ padding: "2px 8px", fontSize: "0.72rem" }}
                                      onClick={() => startTune(w.month, w.planned_qty!)}
                                    >
                                      {t("fc_pva_tune_btn")}
                                    </button>
                                  )}
                                </td>
                              </tr>
                            );
                            return rows;
                          });
                        })()}
                      </tbody>
                    </table>
                  </div>
                  {tuneMonth && (
                    <div style={{ padding: "10px 16px 16px", borderTop: "1px solid var(--border)" }}>
                      <div style={{ fontWeight: 700, fontSize: "0.85rem", marginBottom: 8 }}>
                        {t("fc_tune_form_title", { month: fmtMonth(tuneMonth, lang) })}
                      </div>
                      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
                        <div>
                          <label style={{ display: "block", fontSize: "0.72rem" }}>{t("fc_tune_value_label")}</label>
                          <input
                            type="number" min={0} value={tuneValue}
                            onChange={(e) => setTuneValue(e.target.value)}
                            style={{ width: 100 }}
                          />
                        </div>
                        <div style={{ flex: 1, minWidth: 180 }}>
                          <label style={{ display: "block", fontSize: "0.72rem" }}>{t("adj_reason_label")}</label>
                          <input
                            type="text" value={tuneReason}
                            onChange={(e) => setTuneReason(e.target.value)}
                            placeholder={t("adj_reason_placeholder")}
                            style={{ width: "100%" }}
                          />
                        </div>
                        <button className="btn" disabled={tuneSubmitting} onClick={submitTune}>
                          {tuneSubmitting ? t("fc_running") : t("fc_tune_save")}
                        </button>
                        <button className="btn" onClick={() => setTuneMonth(null)}>
                          {t("fc_tune_cancel")}
                        </button>
                      </div>
                      {tuneError && (
                        <div className="banner warn" style={{ marginTop: 8 }}>⚠️ {tuneError}</div>
                      )}
                    </div>
                  )}
                </div>
              </>
            )}

            <SectionLabel>{lang === "ja" ? "発注提案" : "Order suggestion"}</SectionLabel>
            {detail.suggestion === null ? (
              <div className="banner info">ℹ️ {t("fc_sugg_no_stock")}</div>
            ) : detail.suggestion.action === "suggest_order" ? (
              <div className="banner warn" style={{ display: "flex", justifyContent: "space-between",
                                                     alignItems: "center", flexWrap: "wrap", gap: 12 }}>
                <div>
                  📦 <b>{t("fc_sugg_title")}:</b>{" "}
                  {t("fc_sugg_body", {
                    breach: fmtMonth(
                      detail.forecast[detail.suggestion.breach_month_index ?? 0]?.month ?? "",
                      lang,
                    ),
                    safety: fmtQty(detail.suggestion.safety_stock),
                    qty: fmtQty(detail.suggestion.suggested_qty),
                    order_by: fmtMonth(
                      detail.forecast[detail.suggestion.order_by_month_index ?? 0]?.month ?? "",
                      lang,
                    ),
                  })}
                </div>
                <OrderActionControl itemId={detail.item.id} action={detail.suggestion.order_action ?? null}
                                    suggestedQty={detail.suggestion.suggested_qty ?? 0}
                                    onChanged={() => openDetail(detail.item.id)} t={t} lang={lang} />
              </div>
            ) : (
              <div className="banner info">✅ {t("fc_sugg_none")}</div>
            )}

            <SectionLabel>{lang === "ja" ? "月次" : "Monthly"}</SectionLabel>
            <div className="tablewrap" style={{ maxHeight: 260 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("fc_detail_th_month")}</th>
                    <th className="num">{t("fc_detail_th_fcst")}</th>
                    <th className="num">{t("fc_detail_th_plan")}</th>
                    <th className="num">{t("fc_detail_th_stock_proj")}</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.forecast.map((f, i) => {
                    const plan = detail.one_plan.find((p) => p.month === f.month);
                    const proj = detail.suggestion?.projected?.[i];
                    const adj = f.adjustment;
                    return (
                      <tr key={f.month}>
                        <td className="mono">{fmtMonth(f.month, lang)}</td>
                        <td className="num mono">
                          {adj ? (
                            <span
                              title={t("fc_adjusted_tooltip", {
                                month: fmtMonth(f.month, lang), baseline: fmtQty(adj.baseline),
                                adjusted: fmtQty(adj.adjusted), author: adj.author, reason: adj.reason,
                              })}
                            >
                              <span className="dim" style={{ textDecoration: "line-through", marginRight: 6 }}>
                                {fmtQty(adj.baseline)}
                              </span>
                              <span style={{ fontWeight: 700, color: "var(--amber)" }}>
                                {fmtQty(f.qty)}
                              </span>
                            </span>
                          ) : (
                            <span style={{ fontWeight: 600 }}>{fmtQty(f.qty)}</span>
                          )}
                        </td>
                        <td className="num mono dim">{plan ? fmtQty(plan.qty) : "—"}</td>
                        <td
                          className="num mono"
                          style={{ color: proj !== undefined && proj < 0 ? "var(--red)" : undefined }}
                        >
                          {proj !== undefined ? fmtQty(proj) : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ── Main SKU list ── */}
      <div className="card" ref={listRef} style={{ marginTop: 18 }}>
        <div className="hd">
          <div>
            <h3>{t("fc_list_title")}</h3>
            <div className="desc">{t("fc_list_desc")}</div>
          </div>
        </div>
        <div className="toolbar">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("fc_search_placeholder")}
            style={{ minWidth: 260 }}
          />
          {category && (
            <button className="chip on" onClick={() => setCategory(null)} title={t("fc_filter_all")}>
              {category} ✕
            </button>
          )}
          {methodFilter && (
            <button className="chip on" onClick={() => setMethodFilter(null)} title={t("fc_filter_all")}>
              {t("fc_kpi_tier2")} ✕
            </button>
          )}
          <button
            className={"chip" + (tierFilter === 1 ? " on" : "")}
            onClick={() => setTierFilter(tierFilter === 1 ? null : 1)}
            title={t("fc_tier_filter_1_tooltip")}
          >
            {t("fc_tier_filter_1")}
          </button>
          <button
            className={"chip" + (tierFilter === 2 ? " on" : "")}
            onClick={() => setTierFilter(tierFilter === 2 ? null : 2)}
            title={t("fc_tier_filter_2_tooltip")}
          >
            {t("fc_tier_filter_2")}
          </button>
          <span style={{ flex: 1 }} />
          <span className="dim" style={{ fontSize: "0.74rem" }}>
            {t("fc_confidence_legend")}
          </span>
          {(["good", "rough", "poor", "unknown"] as const).map((tier) => (
            <span key={tier} className="chip static">
              <ConfidenceDot wape={tier === "good" ? 0 : tier === "rough" ? 80 : tier === "poor" ? 150 : null} />
              {t(CONFIDENCE_KEY[tier])}
            </span>
          ))}
        </div>
        {!list ? (
          <div className="empty">{t("common_loading")}</div>
        ) : list.rows.length === 0 ? (
          <div className="empty">{t("fc_no_rows")}</div>
        ) : (
          <>
            <div className="tablewrap" style={{ maxHeight: 520 }}>
              <table className="fit-table">
                {/* Fixed column shares so all 14 columns fit the card width —
                    no scrolling right to reach month 6 / total / order, and
                    the six month buckets stay identically sized. Sums to 100. */}
                <colgroup>
                  <col style={{ width: "13%" }} /> {/* item            */}
                  <col style={{ width: "7%" }} />   {/* category        */}
                  <col style={{ width: "7%" }} />   {/* tier            */}
                  <col style={{ width: "9%" }} />   {/* method + active */}
                  <col style={{ width: "6%" }} />  {/* wape            */}
                  <col style={{ width: "6.5%" }} />{/* stock           */}
                  {monthCols.map((m) => (
                    <col key={m} style={{ width: `${36 / Math.max(1, monthCols.length)}%` }} />
                  ))}
                  <col style={{ width: "6.5%" }} />{/* total6          */}
                  <col style={{ width: "9%" }} />  {/* order           */}
                </colgroup>
                <thead>
                  <tr>
                    <th>{t("fc_th_item")}</th>
                    <th>{t("fc_th_category")}</th>
                    <th>{t("fc_th_tier")}</th>
                    <th>
                      {t("fc_th_method")}
                      <span className="sub">{t("fc_th_last_active")}</span>
                    </th>
                    <th className="num">{t("fc_th_wape")}</th>
                    <th className="num">{t("fc_th_stock")}</th>
                    {monthCols.map((m) => (
                      <th key={m} className="num">
                        {fmtMonth(m, lang)}
                      </th>
                    ))}
                    <th className="num">{t("fc_th_total6")}</th>
                    <th>{t("fc_th_order")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.rows.map((r) => {
                    const tier = confidenceTier(r.wape);
                    const lowTrust = tier === "poor";
                    const { discontinuing } = parseMethod(r.method);
                    return (
                    <tr
                      key={r.item_id}
                      onClick={() => openDetail(r.item_id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td style={{ fontWeight: 600 }} title={r.name}>
                        {r.name}
                        {r.has_adjustment && (
                          <span
                            style={{ marginLeft: 6, fontSize: "0.72rem", fontWeight: 600, color: "var(--amber)" }}
                            title={t("fc_adjusted_badge")}
                          >
                            {t("fc_adjusted_badge")}
                          </span>
                        )}
                        {discontinuing && <DiscontinuingBadge label={t("fc_discontinuing_badge")} />}
                      </td>
                      <td className="dim" title={r.category ?? undefined}>{r.category ?? "—"}</td>
                      <td>
                        <span
                          className={"order-action-badge" + (r.tier === 1 ? " awaiting" : "")}
                          title={r.tier === 1 ? t("fc_tier_filter_1") : r.tier === 2 ? t("fc_tier_filter_2") : undefined}
                        >
                          {r.tier === 1 ? t("fc_tier_badge_1") : r.tier === 2 ? t("fc_tier_badge_2") : "—"}
                        </span>
                      </td>
                      {/* method + last-active share one column so the row fits;
                          the month is the dim second line. */}
                      <td className="dim stacked" title={methodLabel(r.method)}>
                        {methodLabel(r.method)}
                        <span className="sub">
                          {r.last_active_month ? fmtMonth(r.last_active_month, lang) : "—"}
                        </span>
                      </td>
                      <td className="num mono dim"
                          title={r.wape_full_horizon !== null
                                ? t("fc_wape_full_horizon_tooltip", { wape: r.wape_full_horizon })
                                : undefined}>
                        <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end" }}>
                          <ConfidenceDot wape={r.wape} />
                          {r.wape !== null ? `${r.wape}%` : "—"}
                        </span>
                      </td>
                      <td className="num mono">{fmtQty(r.stock)}</td>
                      {r.qty.slice(0, 6).map((v, i) => (
                        <td key={i} className="num mono" style={lowTrust ? { opacity: 0.5 } : undefined}>
                          {fmtQty(v)}
                        </td>
                      ))}
                      <td
                        className="num mono"
                        style={{ fontWeight: 700, opacity: lowTrust ? 0.5 : 1 }}
                      >
                        {r.next6_total.toLocaleString()}
                      </td>
                      {/* qty over order-by month rather than one long inline
                          phrase — keeps the column narrow enough to fit. */}
                      <td className="stacked">
                        {r.order ? (
                          <span
                            style={{ display: "block", color: "var(--amber)", fontWeight: 700 }}
                            title={t("fc_order_badge", {
                              qty: fmtQty(r.order.qty),
                              month: fmtMonth(r.order.order_by, lang),
                            })}
                          >
                            {fmtQty(r.order.qty)}
                            <span className="sub">{fmtMonth(r.order.order_by, lang)}</span>
                          </span>
                        ) : (
                          <span className="dim">—</span>
                        )}
                      </td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="dim" style={{ padding: "10px 24px", fontSize: "0.8rem" }}>
              {t("fc_showing", { shown: list.rows.length, total: list.total })}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
