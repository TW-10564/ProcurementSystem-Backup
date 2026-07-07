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
  next12_total: number;
}

export interface ForecastSummary {
  run: ForecastRunInfo | null;
  orders_needed?: number;
  categories: ForecastCategory[];
}

export interface ForecastRow {
  item_id: number;
  name: string;
  category: string | null;
  tier: number | null;
  method: string | null;
  wape: number | null;
  stock: number | null;
  qty: number[];
  next12_total: number;
  has_adjustment: boolean;
  order: { qty: number; order_by: string } | null;
}

export interface ForecastList {
  run: ForecastRunInfo | null;
  total: number;
  months: string[];
  rows: ForecastRow[];
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
    lead_time_days: number | null;
    min_stock_days: number | null;
  };
  method: string | null;
  wape: number | null;
  history: MonthQty[];
  forecast: ForecastMonth[];
  one_plan: MonthQty[];
  weekly: { week_start: string; qty: number }[];
  stock: { one_balance: number | null; sap_qty: number | null; snapshot_date: string | null } | null;
  suggestion: {
    action: string;
    suggested_qty?: number;
    order_by_month_index?: number;
    breach_month_index?: number;
    safety_stock?: number;
    current_balance?: number;
    projected?: number[];
    reason?: string;
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
