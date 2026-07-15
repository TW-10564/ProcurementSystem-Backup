import axios from "axios";
import type {
  Adjustment,
  BacktestReport,
  CompatRule,
  DiscontinuationReport,
  ForecastDetail,
  ForecastList,
  ForecastRunInfo,
  ForecastSummary,
  LlmDraftResult,
  MismatchReport,
  Notification,
  OrderSuggestions,
  PlannerAccuracy,
  PlanVsActual,
  RebuildHistoryEntry,
  RebuildStatus,
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

export const api = {
  health: () => http.get("/health").then((r) => r.data),

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

  forecasts: (params?: { q?: string; category?: string; method?: string; limit?: number; offset?: number }) =>
    http.get<ForecastList>("/forecasts", { params }).then((r) => r.data),

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
};

// Downloads must go through `http`, not a plain <a href> — the backend's
// X-API-Key gate (onefcst/api/auth.py) rejects a request with no header,
// and browser navigation never sends one. A raw <a href={downloadUrl(...)}>
// silently 401s the instant ONEFCST_API_KEY is set, while the rest of the
// app (which does go through `http`) keeps working — found 2026-07-09.
export async function downloadReport(path: string, fallbackName: string): Promise<void> {
  const res = await http.get<Blob>(path, { responseType: "blob" });
  const disposition = res.headers["content-disposition"] as string | undefined;
  const filename = disposition?.match(/filename="?([^";]+)"?/)?.[1] ?? fallbackName;
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
