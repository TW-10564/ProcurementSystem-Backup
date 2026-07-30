import axios from "axios";
import { acquireApiToken } from "../auth/msal";
import type {
  Adjustment,
  BacktestReport,
  CompatRule,
  DiscontinuationReport,
  DiscontinuationSuggestion,
  ForecastDetail,
  ForecastList,
  ForecastRunInfo,
  ForecastSummary,
  LlmDraftResult,
  MismatchReport,
  Notification,
  OrderActionInfo,
  OrderSuggestions,
  PlannerAccuracy,
  PlanVsActual,
  RebuildHistoryEntry,
  RebuildStatus,
  SalesReconciliationReport,
  StockReconciliationReport,
} from "./types";

// Read-only against every NAS source, by construction: this client only ever
// talks to the onefcst API (app/backend), which itself never writes to
// ONE/SAP/planning files — see app/backend/onefcst/api/main.py's docstring.
const BASE_URL = import.meta.env.VITE_API_BASE ?? "http://localhost:8600";
// Only sent when set — matches the backend's default-disabled auth gate
// (onefcst/api/auth.py). Empty on both sides = no auth, the local-dev state.
const API_KEY = import.meta.env.VITE_API_KEY as string | undefined;

const http = axios.create({
  baseURL: BASE_URL,
  headers: API_KEY ? { "X-API-Key": API_KEY } : undefined,
});

/**
 * Attach the Microsoft token to every request.
 *
 * Per-request rather than once at creation because tokens expire: MSAL hands
 * back a cached one until it is close to expiry and then silently renews. A
 * token captured once at startup would work for an hour and then 401 for the
 * rest of the session — the kind of bug that only shows up after lunch.
 *
 * A no-op when SSO is off (acquireApiToken returns null), so the pre-SSO
 * shared-key path is byte-for-byte unchanged.
 */
http.interceptors.request.use(async (cfg) => {
  const token = await acquireApiToken();
  if (token) cfg.headers.Authorization = `Bearer ${token}`;
  return cfg;
});

/**
 * Fired whenever a request fails in a way a rebuild would explain: any 5xx,
 * or no response at all (backend restarting).
 *
 * Why: a rebuild drops every table, so requests already in flight come back
 * `no such table: forecasts` -> 500. MaintenanceGate polls on a 20s timer, so
 * users saw a raw error screen for up to 20s before the 「再構築中」 notice
 * appeared — reported as a bug on 2026-07-30, and it looks exactly like the
 * system being broken. The gate listens for this and re-polls at once.
 *
 * Deliberately just a signal, not a verdict: a 500 is NOT evidence of
 * maintenance. Only /maintenance decides, so a genuine unrelated 500 makes
 * one extra cheap request and changes nothing on screen.
 */
export const SERVER_ERROR_EVENT = "onefcst:server-error";

http.interceptors.response.use(
  (r) => r,
  (err) => {
    const status = err?.response?.status as number | undefined;
    // Never fire for /maintenance itself: the listener answers by calling it,
    // so a failing probe would re-trigger its own listener indefinitely.
    const isProbe = (err?.config?.url ?? "").includes("/maintenance");
    if (!isProbe && (status === undefined || status >= 500)) {
      window.dispatchEvent(new Event(SERVER_ERROR_EVENT));
    }
    return Promise.reject(err);
  },
);

export interface MaintenanceState {
  enabled: boolean;
  message: string;
  since: string | null;
  by: string | null;
}

export const api = {
  // Public endpoint (auth-exempt server-side): it must answer even when the
  // DB is locked mid-rebuild or the shared key has rotated, since that is
  // exactly when users need to be told what is going on.
  maintenance: () => http.get<MaintenanceState>("/maintenance").then((r) => r.data),

  setMaintenance: (enabled: boolean, message: string) =>
    http.post<MaintenanceState>("/maintenance", { enabled, message }).then((r) => r.data),

  compatRules: (params?: { status?: string; min_confidence?: number }) =>
    http.get<CompatRule[]>("/compat-rules", { params }).then((r) => r.data),

  confirmRule: (id: number, author: string) =>
    http
      .post<CompatRule>(`/compat-rules/${id}/confirm`, { author })
      .then((r) => r.data),

  rejectRule: (id: number, author: string, reason: string) =>
    http
      .post<CompatRule>(`/compat-rules/${id}/reject`, { author, reason })
      .then((r) => r.data),

  editRule: (
    id: number,
    author: string,
    changes: { relation?: string; confidence?: number; note?: string },
  ) =>
    http
      .patch<CompatRule>(`/compat-rules/${id}`, { author, ...changes })
      .then((r) => r.data),

  discontinuationSuggestions: (params?: { status?: string }) =>
    http
      .get<DiscontinuationSuggestion[]>("/discontinuation-suggestions", { params })
      .then((r) => r.data),

  confirmDiscontinuation: (id: number, author: string) =>
    http
      .post<DiscontinuationSuggestion>(`/discontinuation-suggestions/${id}/confirm`, { author })
      .then((r) => r.data),

  rejectDiscontinuation: (id: number, author: string, reason: string) =>
    http
      .post<DiscontinuationSuggestion>(`/discontinuation-suggestions/${id}/reject`, { author, reason })
      .then((r) => r.data),

  markOrdered: (
    itemId: number,
    body: { author: string; qty_ordered?: number; expected_arrival_date?: string; note?: string },
  ) =>
    http.post<OrderActionInfo>(`/order-actions/${itemId}/mark-ordered`, body).then((r) => r.data),

  extendOrderAction: (orderActionId: number, author: string, expected_arrival_date: string) =>
    http
      .post<OrderActionInfo>(`/order-actions/${orderActionId}/extend`, { author, expected_arrival_date })
      .then((r) => r.data),

  confirmOrderReceived: (orderActionId: number, author: string) =>
    http
      .post<OrderActionInfo>(`/order-actions/${orderActionId}/confirm-received`, { author })
      .then((r) => r.data),

  dismissOrderAction: (orderActionId: number, author: string, reason: string = "") =>
    http
      .post<OrderActionInfo>(`/order-actions/${orderActionId}/dismiss`, { author, reason })
      .then((r) => r.data),

  bulkConfirm: (author: string, threshold: number) =>
    http
      .post("/compat-rules/bulk-confirm", null, { params: { author, threshold } })
      .then((r) => r.data as { confirmed: number; threshold: number }),

  draftLlmRules: () =>
    http
      .post<LlmDraftResult>("/compat-rules/draft-llm", null, { timeout: 300_000 })
      .then((r) => r.data),

  adjustments: (item_id?: number) =>
    http
      .get<Adjustment[]>("/adjustments", { params: { item_id } })
      .then((r) => r.data),

  createAdjustment: (body: {
    item_id: number;
    month: string;
    old_value?: number;
    new_value: number;
    scope: "global" | "category" | "sku";
    reason: string;
    author: string;
    factor?: number;
  }) => http.post<Adjustment>("/adjustments", body).then((r) => r.data),

  notifications: (limit = 50) =>
    http
      .get<Notification[]>("/notifications", { params: { limit } })
      .then((r) => r.data),

  notificationSummary: () =>
    http
      .get<{ total_events: number; by_author: Record<string, number> }>(
        "/notifications/summary",
      )
      .then((r) => r.data),

  searchItems: (q: string) =>
    http
      .get<{ id: number; canonical_name: string; category: string | null; tier: number | null }[]>(
        "/items",
        { params: { q, limit: 15 } },
      )
      .then((r) => r.data),

  forecastSummary: () =>
    http.get<ForecastSummary>("/forecasts/summary").then((r) => r.data),

  forecasts: (
    params?: { q?: string; category?: string; method?: string; tier?: number; limit?: number; offset?: number },
  ) => http.get<ForecastList>("/forecasts", { params }).then((r) => r.data),

  forecastDetail: (itemId: number) =>
    http.get<ForecastDetail>(`/forecasts/${itemId}`).then((r) => r.data),

  planVsActual: (itemId: number) =>
    http.get<PlanVsActual>(`/forecasts/${itemId}/plan-vs-actual`).then((r) => r.data),

  runForecast: () =>
    http.post<ForecastRunInfo>("/forecasts/run").then((r) => r.data),

  triggerRebuild: () =>
    http.post<{ status: string }>("/build/rebuild").then((r) => r.data),

  rebuildStatus: () =>
    http.get<RebuildStatus>("/build/rebuild/status").then((r) => r.data),

  rebuildHistory: (limit = 20) =>
    http.get<RebuildHistoryEntry[]>("/build/rebuild/history", { params: { limit } })
      .then((r) => r.data),

  orderSuggestions: () =>
    http.get<OrderSuggestions>("/forecasts/order-suggestions").then((r) => r.data),

  mismatchReport: () => http.get<MismatchReport>("/reports/mismatch").then((r) => r.data),

  backtestReport: () =>
    http.get<BacktestReport>("/reports/backtest").then((r) => r.data),

  plannerAccuracy: () =>
    http.get<PlannerAccuracy>("/reports/planner-accuracy").then((r) => r.data),

  fvaReport: () => http.get("/reports/fva").then((r) => r.data),

  adjustmentRate: () => http.get("/reports/adjustment-rate").then((r) => r.data),

  discontinuationReport: () =>
    http.get<DiscontinuationReport>("/reports/discontinuation-risk").then((r) => r.data),

  stockReconciliation: () =>
    http.get<StockReconciliationReport>("/reports/stock-reconciliation").then((r) => r.data),

  salesReconciliation: () =>
    http.get<SalesReconciliationReport>("/reports/sales-reconciliation").then((r) => r.data),
};

// Downloads must go through `http`, not a plain <a href> — the backend's
// X-API-Key gate (onefcst/api/auth.py) rejects a request with no header,
// and browser navigation never sends one. A raw <a href={downloadUrl(...)}>
// silently 401s the instant ONEFCST_API_KEY is set, while the rest of the
// app (which does go through `http`) keeps working — found 2026-07-09.
export async function downloadReport(path: string, fallbackName: string): Promise<void> {
  const res = await http.get<Blob>(path, { responseType: "blob" });
  const disposition = res.headers["content-disposition"] as string | undefined;
  // Starlette sends non-ASCII names as RFC 5987 `filename*=utf-8''%E9%9C...`
  // (percent-encoded) — check that form first, else the exports' Japanese
  // filenames (需要予測_企画4部材_….xlsx) silently degrade to fallbackName.
  const star = disposition?.match(/filename\*=utf-8''([^;]+)/i)?.[1];
  const plain = disposition?.match(/filename="?([^";]+)"?/)?.[1];
  let filename = fallbackName;
  if (star) {
    try {
      filename = decodeURIComponent(star);
    } catch {
      filename = plain ?? fallbackName;
    }
  } else if (plain) {
    filename = plain;
  }
  const url = URL.createObjectURL(res.data);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export { BASE_URL };
