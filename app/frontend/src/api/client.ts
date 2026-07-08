import axios from "axios";
import type {
  Adjustment,
  BacktestReport,
  CompatRule,
  ForecastDetail,
  ForecastList,
  ForecastRunInfo,
  ForecastSummary,
  LlmDraftResult,
  MismatchReport,
  Notification,
  PlannerAccuracy,
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

  runForecast: () =>
    http.post<ForecastRunInfo>("/forecasts/run").then((r) => r.data),

  mismatchReport: () => http.get<MismatchReport>("/reports/mismatch").then((r) => r.data),

  backtestReport: () =>
    http.get<BacktestReport>("/reports/backtest").then((r) => r.data),

  plannerAccuracy: () =>
    http.get<PlannerAccuracy>("/reports/planner-accuracy").then((r) => r.data),
};

export function downloadUrl(path: string): string {
  return `${BASE_URL}${path}`;
}

export { BASE_URL };
