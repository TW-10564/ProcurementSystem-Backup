export interface CompatRule {
  id: number;
  part_a_id: number;
  part_a_name: string;
  part_b_id: number;
  part_b_name: string;
  relation: string;
  confidence: number;
  origin: string;
  status: "draft" | "confirmed" | "rejected";
  confirmed_by: string | null;
  note: string | null;
}

export interface DiscontinuationSuggestion {
  id: number;
  item_id: number | null;
  item_name: string | null;
  category: string | null;
  source_category_file: string | null;
  detected_at: string | null;
  status: "draft" | "confirmed" | "rejected";
  reviewed_by: string | null;
}

export interface LlmDraftResult {
  ok: boolean;
  model: string;
  cpu_unclassified: number;
  mb_unclassified: number;
  classified: number;
  needs_review: number;
  rules_drafted: number;
  skipped_existing: number;
  error: string | null;
}

export interface Adjustment {
  id: number;
  item_id: number | null;
  item_name: string | null;
  month: string;
  old_value: number | null;
  new_value: number;
  scope: "global" | "category" | "sku";
  reason: string;
  author: string;
  created_at: string;
}

export interface Notification {
  id: number;
  who: string | null;
  what: string;
  at: string;
}

export interface MismatchGroup {
  socket: string;
  cpu_demand: number;
  mb_demand: number;
  gap: number;
  pct: number | null;
  exclusive: boolean;
  takeorpay_n1: boolean;
  severity: "LOW" | "MEDIUM" | "HIGH";
}

export interface MismatchReport {
  month: string;
  groups: MismatchGroup[];
  confirmed_rule_classifications: { cpu: string[]; mb: string[] };
}

export interface DiscontinuationCandidate {
  item_id: number;
  name: string;
  category: string | null;
  lifecycle: string | null;
  prior_6mo_actual: number;
  recent_6mo_actual: number;
  decline_pct: number;
  next_6mo_forecast: number;
  method: string | null;
  marked_no_reorder: boolean | null;
}

export interface ZeroStockCandidate {
  item_id: number;
  name: string;
  category: string | null;
  lifecycle: string | null;
  zero_since: string;
  latest_snapshot: string;
  zero_duration_days: number;
  zero_snapshots_observed: number;
  next_6mo_forecast: number;
  marked_no_reorder: boolean | null;
}

export interface DiscontinuationReport {
  run_id: number | null;
  count: number;
  candidates: DiscontinuationCandidate[];
  note: string;
  zero_stock_count: number;
  zero_stock_candidates: ZeroStockCandidate[];
  sap_data_depth_days: number;
  sap_items_with_history: number;
  zero_stock_note: string;
}

export interface StockDivergence {
  item_id: number;
  name: string;
  category: string | null;
  tier: number | null;
  sap_qty: number;
  sap_snapshot_date: string | null;
  one_balance: number;
  one_snapshot_date: string | null;
  pct_diff: number;
  trusted_value: number;
}

export interface NoSapCoverageItem {
  item_id: number;
  name: string;
  category: string | null;
  tier: number | null;
  one_balance: number;
  trusted_value: number | null;
  confidence: "one_only_unverified" | "one_only_negative_clamped";
}

export interface StockReconciliationReport {
  generated_for_items: number;
  sap_covered_items: number;
  divergence_threshold_pct: number;
  divergences: StockDivergence[];
  no_sap_coverage: NoSapCoverageItem[];
  negative_one_balance_clamped: NoSapCoverageItem[];
  note: string;
}

export interface SalesDivergence {
  item_id: number;
  name: string;
  category: string | null;
  month: string;
  one_closed_actual: number;
  sap_actual: number;
  diff: number;
  pct_diff: number;
  higher_source: "ONE" | "SAP";
}

export interface SalesReconciliationReport {
  overlap_months: string[];
  compared_item_months: number;
  divergence_threshold_pct: number;
  divergences: SalesDivergence[];
  note: string;
}

export interface RebuildReport {
  sap_shipments?: { resolved: number; unresolved: number; rows: number };
  history_preserved?: { actuals: number; adjustments: number; compat_rules: number };
  gate_a?: { plan_values: number; resolved: number; rate: number; status: string };
  nas_refresh?: { status: "ok" | "skipped" | "error"; reason?: string;
                  files_in_cache?: number };
}

export interface RebuildStatus {
  status: "idle" | "running" | "done" | "error";
  trigger?: "manual" | "scheduled" | null;
  started_at: string | null;
  finished_at: string | null;
  report: RebuildReport | null;
  error: string | null;
}

export interface RebuildHistoryEntry {
  id: number;
  trigger: "manual" | "scheduled";
  status: "running" | "done" | "error";
  started_at: string;
  finished_at: string | null;
  report: RebuildReport | null;
  error: string | null;
}

export interface PlanVsActualWeek {
  week_start: string;
  month: string;
  planned_qty: number | null;
  actual_qty: number | null;
  variance_qty: number | null;
  variance_pct: number | null;
  has_adjustment: boolean;
  is_history: boolean;
}

export interface PlanVsActual {
  item_id: number;
  name: string;
  weeks: PlanVsActualWeek[];
}

export interface ForecastRunInfo {
  run_id: number;
  method_version: string;
  created_at: string | null;
  start_month?: string;
  horizon?: number;
  items_forecasted?: number;
  items_skipped_stale?: number;
  methods?: Record<string, number>;
  holdout?: {
    overall: { wape: number; naive_wape: number; items: number } | null;
    near_term: { wape: number; naive_wape: number; items: number } | null;
    by_category: Record<string, { wape: number; naive_wape: number; items: number } | null>;
    note?: string;
  };
  tier2?: {
    items_forecasted: number;
    items_skipped_stale: number;
    items_skipped_no_attach_rate: number;
    category_fallback: {
      items_forecasted: number;
      items_skipped_no_category_rate: number;
      categories_with_a_rate: number;
      leave_one_out_wape: number | null;
    };
  };
}

export interface ForecastCategory {
  category: string;
  items: number;
  next3_total: number;
  next6_total: number;
  no_stock_tracking_items?: number;
}

export interface ForecastSummary {
  run: ForecastRunInfo | null;
  orders_needed?: number;
  categories: ForecastCategory[];
  excluded_no_stock_tracking?: { items: number; next3_total: number; next6_total: number };
}

export interface ForecastRow {
  item_id: number;
  name: string;
  category: string | null;
  tier: number | null;
  method: string | null;
  last_active_month: string | null;  // most recent month with real sales; default sort key
  wape: number | null;               // near-term (M+1) WAPE — drives confidence
  wape_full_horizon: number | null;  // 6-month blended WAPE — secondary caveat
  stock: number | null;
  qty: number[];
  next6_total: number;
  has_adjustment: boolean;
  order: { qty: number; order_by: string } | null;
}

export interface ForecastList {
  run: ForecastRunInfo | null;
  total: number;
  months: string[];
  rows: ForecastRow[];
}

export interface OrderActionInfo {
  id: number;
  status: "ordered" | "received" | "dismissed";
  qty_ordered: number | null;
  expected_arrival_date: string | null;
  note: string | null;
  ordered_by: string | null;
  ordered_at: string | null;
  awaiting_confirmation: boolean;
}

export interface OrderSuggestionRow {
  item_id: number;
  name: string;
  category: string | null;
  method: string | null;
  stock: number | null;
  stock_confidence: "sap_verified" | "one_only_unverified" | "one_only_negative_clamped" | "no_data";
  safety_stock: number;
  order_qty: number;
  order_by: string;
  breach_month: string;
  moq_excessive: boolean;
  moq_months_of_cover: number | null;
  order_action: OrderActionInfo | null;
}

export interface OrderSuggestions {
  run: ForecastRunInfo | null;
  total: number;
  total_qty: number;
  rows: OrderSuggestionRow[];
  by_category: { category: string; items: number; qty: number }[];
  by_month: { month: string; items: number }[];
  negative_stock_count: number;
}

export interface MonthQty {
  month: string;
  qty: number | null;
}

export interface AdjustmentDetail {
  baseline: number;
  adjusted: number;
  reason: string;
  author: string;
  created_at: string | null;
}

export interface ForecastMonth {
  month: string;
  qty: number | null;
  baseline_qty: number | null;
  adjustment: AdjustmentDetail | null;
}

export interface ForecastDetail {
  item: {
    id: number;
    name: string;
    category: string | null;
    tier: number | null;
    lifecycle: string | null;
    moq: number | null;
    lead_time_days: number | null;             // 部品マスター's static estimate
    observed_lead_time_days: number | null;    // real median from completed POs
    observed_lead_time_samples: number | null;
    effective_lead_time_days: number | null;   // what order suggestions actually use
    min_stock_days: number | null;
  };
  method: string | null;
  wape: number | null;               // near-term (M+1) WAPE — drives confidence
  wape_full_horizon: number | null;  // 6-month blended WAPE — secondary caveat
  history: MonthQty[];
  forecast: ForecastMonth[];
  one_plan: MonthQty[];
  weekly: { week_start: string; qty: number }[];
  stock: { one_balance: number | null; sap_qty: number | null;
           confidence: "sap_verified" | "one_only_unverified" | "one_only_negative_clamped" | "no_data";
           snapshot_date: string | null } | null;
  suggestion: {
    action: string;
    suggested_qty?: number;
    order_by_month_index?: number;
    breach_month_index?: number;
    safety_stock?: number;
    current_balance?: number;
    projected?: number[];
    reason?: string;
    order_action?: OrderActionInfo | null;
  } | null;
}

export interface PlannerCategorySeriesRow {
  source: string;
  category: string;
  months: number;
  range: string;
  wape: number;
  by_fiscal_year?: Record<string, number>;
}

export interface PlannerAccuracy {
  gradeable: number;
  wape: number | null;
  note?: string;
  archived_total?: number;
  historical_reference: {
    as_of: string;
    category_level: {
      note: string;
      series: PlannerCategorySeriesRow[];
      our_equivalent: { metric: string; wape: number; source: string };
      conclusion: string;
    };
    per_sku_level: {
      note: string;
      source: string;
      months: number;
      range: string;
      models: number;
      wape_per_sku: number;
      wape_channel_total: number;
      our_equivalent: { overall_6mo_wape: number; near_term_m1_wape: number; source: string };
      conclusion: string;
    };
    one_off_matched_month: {
      note: string;
      target_month: string;
      planner: { wape: number; lead_time: string; n_skus: number };
      system: { wape: number; lead_time: string; n_skus: number };
      conclusion: string;
    };
    what_would_settle_it: string;
  };
}

export interface BacktestRow {
  ki: number;
  total: number;
  seasonal_wape: number;
  flat_wape: number;
  last_year_wape: number;
}

export interface BacktestReport {
  n_periods: number;
  avg_seasonal_wape: number;
  avg_flat_wape: number;
  avg_last_year_wape: number;
  rows: BacktestRow[];
  winner: string;
}
