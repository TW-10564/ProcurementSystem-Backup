import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, downloadUrl } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type {
  ForecastDetail,
  ForecastList,
  ForecastSummary,
} from "../api/types";
import type { TranslationKey } from "../i18n/translations";

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

function fmtMonth(iso: string, lang: string): string {
  const d = new Date(iso);
  return lang === "ja"
    ? `${d.getFullYear()}/${d.getMonth() + 1}`
    : d.toLocaleDateString("en-US", { year: "2-digit", month: "short" });
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
          fill={a.kind === "h" ? "#94a3b8" : "#2563eb"}
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

export default function Forecast() {
  const { t, lang } = useLang();
  const [summary, setSummary] = useState<ForecastSummary | null>(null);
  const [list, setList] = useState<ForecastList | null>(null);
  const [detail, setDetail] = useState<ForecastDetail | null>(null);
  const [q, setQ] = useState("");
  const [category, setCategory] = useState<string | null>(null);
  const [methodFilter, setMethodFilter] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = () =>
    api.forecastSummary().then(setSummary).catch((e) => setError(String(e.message ?? e)));

  useEffect(() => {
    loadSummary();
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      api
        .forecasts({ q: q || undefined, category: category ?? undefined,
                   method: methodFilter ?? undefined, limit: 80 })
        .then(setList)
        .catch((e) => setError(String(e.message ?? e)));
    }, 250);
    return () => clearTimeout(timer);
  }, [q, category, methodFilter]);

  const run = async () => {
    setRunning(true);
    setFlash(null);
    try {
      const r = await api.runForecast();
      setFlash(t("fc_run_done", { id: r.run_id }));
      await loadSummary();
      setList(await api.forecasts({ q: q || undefined, category: category ?? undefined,
                                  method: methodFilter ?? undefined, limit: 80 }));
    } catch (e: unknown) {
      setError(String((e as Error).message ?? e));
    } finally {
      setRunning(false);
    }
  };

  const openDetail = (itemId: number) =>
    api.forecastDetail(itemId).then(setDetail).catch((e) => setError(String(e.message ?? e)));

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
  const monthCols = list?.months.slice(0, 6) ?? [];

  const methodLabel = (m: string | null) =>
    m && METHOD_KEY[m] ? t(METHOD_KEY[m]) : m ?? "—";

  return (
    <div>
      <div className="page-head">
        <h2>{t("fc_title")}</h2>
        <p>{t("fc_sub")}</p>
      </div>

      {error && <div className="banner warn">⚠️ {error}</div>}
      {flash && <div className="banner info">✅ {flash}</div>}
      {summary && !summary.run && <div className="banner info">ℹ️ {t("fc_no_run")}</div>}

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
          <div className="sub">{t("fc_kpi_next3_sub")}</div>
        </div>
        <div className="kpi">
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
        <div className="kpi">
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
            outline: methodFilter === "tier2" ? "2px solid var(--indigo, #2563eb)" : undefined,
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

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("fc_cat_title")}</h3>
            <div className="desc">
              {t("fc_cat_desc")}
              {run_?.created_at && (
                <>
                  {" — "}
                  {t("fc_last_run", {
                    at: new Date(run_.created_at + "Z").toLocaleString(
                      lang === "ja" ? "ja-JP" : "en-US",
                    ),
                  })}
                </>
              )}
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <a className="btn" href={downloadUrl("/export/forecasts")}>
              {t("common_download_excel")}
            </a>
            <button className="btn" onClick={run} disabled={running}>
              {running ? t("fc_running") : `🔄 ${t("fc_run")}`}
            </button>
          </div>
        </div>
        {summary ? (
          <div className="tablewrap" style={{ maxHeight: 280 }}>
            <table>
              <thead>
                <tr>
                  <th>{t("fc_th_category")}</th>
                  <th className="num">{t("fc_th_items")}</th>
                  <th className="num">{t("fc_th_next3")}</th>
                  <th className="num">{t("fc_th_next12")}</th>
                </tr>
              </thead>
              <tbody>
                {summary.categories.map((c) => (
                  <tr
                    key={c.category}
                    onClick={() =>
                      setCategory(category === c.category ? null : c.category)
                    }
                    style={{
                      cursor: "pointer",
                      background:
                        category === c.category ? "rgba(37,99,235,0.12)" : undefined,
                    }}
                  >
                    <td style={{ fontWeight: 600 }}>{c.category}</td>
                    <td className="num mono">{c.items}</td>
                    <td className="num mono">{c.next3_total.toLocaleString()}</td>
                    <td className="num mono">{c.next12_total.toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">{t("common_loading")}</div>
        )}
      </div>

      {detail && (
        <div className="card">
          <div className="hd">
            <div>
              <h3>{t("fc_detail_title", { name: detail.item.name })}</h3>
              <div className="desc">
                {detail.item.category} ・ {methodLabel(detail.method)}
                {detail.item.moq ? ` ・ ${t("fc_moq")}: ${fmtQty(detail.item.moq)}` : ""}
                {detail.item.lead_time_days
                  ? ` ・ ${t("fc_lead")}: ${t("fc_lead_days", { n: Math.round(detail.item.lead_time_days) })}`
                  : ""}
                {detail.stock
                  ? ` ・ ${t("fc_stock_now")}: ${fmtQty(detail.stock.sap_qty ?? detail.stock.one_balance)}`
                  : ""}
              </div>
            </div>
            <button className="btn" onClick={() => setDetail(null)}>
              {t("fc_detail_close")}
            </button>
          </div>
          <div style={{ padding: "14px 24px 20px" }}>
            <div className="dim" style={{ fontSize: "0.8rem", marginBottom: 8 }}>
              <span style={{ color: "#94a3b8" }}>■</span> {t("fc_legend_history")}{"  "}
              <span style={{ color: "#2563eb" }}>■</span> {t("fc_legend_forecast")}{"  "}
              <span style={{ color: "#d97706" }}>┅</span> {t("fc_legend_plan")}
            </div>
            <ForecastChart detail={detail} lang={lang} />
            <div className="dim" style={{ fontSize: "0.8rem", margin: "8px 0 14px", display: "flex", alignItems: "center" }}>
              <ConfidenceDot wape={detail.wape} />
              {detail.wape !== null
                ? t("fc_detail_wape_note", { wape: detail.wape })
                : t("fc_detail_no_wape", { method: methodLabel(detail.method) })}
            </div>

            {detail.weekly.length > 0 && (
              <div className="card" style={{ margin: "0 0 16px", background: "var(--indigo-bg, rgba(37,99,235,0.06))" }}>
                <div style={{ padding: "12px 16px 4px", fontWeight: 700, fontSize: "0.9rem" }}>
                  {t("fc_weekly_title")}
                </div>
                <div className="dim" style={{ fontSize: "0.78rem", padding: "0 16px 10px" }}>
                  {t("fc_weekly_desc")}
                </div>
                <div className="tablewrap" style={{ maxHeight: 200, padding: "0 4px 8px" }}>
                  <table>
                    <thead>
                      <tr>
                        <th>{t("fc_weekly_th_week")}</th>
                        <th className="num">{t("fc_weekly_th_qty")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.weekly.map((w) => (
                        <tr key={w.week_start}>
                          <td className="mono">{fmtMonth(w.week_start, lang)} ({w.week_start.slice(8, 10)})</td>
                          <td className="num mono">{fmtQty(w.qty)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            {detail.suggestion === null ? (
              <div className="banner info">ℹ️ {t("fc_sugg_no_stock")}</div>
            ) : detail.suggestion.action === "suggest_order" ? (
              <div className="banner warn">
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
            ) : (
              <div className="banner info">✅ {t("fc_sugg_none")}</div>
            )}

            <div className="tablewrap" style={{ marginTop: 14, maxHeight: 260 }}>
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
                                ✍️ {fmtQty(f.qty)}
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

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("fc_list_title")}</h3>
            <div className="desc">{t("fc_list_desc")}</div>
          </div>
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("fc_search_placeholder")}
            style={{ minWidth: 260 }}
          />
        </div>
        {(category || methodFilter) && (
          <div style={{ padding: "8px 24px 0", display: "flex", gap: 8 }}>
            {category && (
              <button className="btn" onClick={() => setCategory(null)}>
                {category} ✕ {t("fc_filter_all")}
              </button>
            )}
            {methodFilter && (
              <button className="btn" onClick={() => setMethodFilter(null)}>
                {t("fc_kpi_tier2")} ✕ {t("fc_filter_all")}
              </button>
            )}
          </div>
        )}
        <div
          className="dim"
          style={{
            display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap",
            padding: "10px 24px 0", fontSize: "0.78rem",
          }}
        >
          <span>{t("fc_confidence_legend")}</span>
          {(["good", "rough", "poor", "unknown"] as const).map((tier) => (
            <span key={tier} style={{ display: "inline-flex", alignItems: "center" }}>
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
              <table>
                <thead>
                  <tr>
                    <th>{t("fc_th_item")}</th>
                    <th>{t("fc_th_category")}</th>
                    <th>{t("fc_th_method")}</th>
                    <th className="num">{t("fc_th_wape")}</th>
                    <th className="num">{t("fc_th_stock")}</th>
                    {monthCols.map((m) => (
                      <th key={m} className="num">
                        {fmtMonth(m, lang)}
                      </th>
                    ))}
                    <th className="num">{t("fc_th_total12")}</th>
                    <th>{t("fc_th_order")}</th>
                  </tr>
                </thead>
                <tbody>
                  {list.rows.map((r) => {
                    const tier = confidenceTier(r.wape);
                    const lowTrust = tier === "poor";
                    return (
                    <tr
                      key={r.item_id}
                      onClick={() => openDetail(r.item_id)}
                      style={{ cursor: "pointer" }}
                    >
                      <td style={{ fontWeight: 600 }}>
                        {r.name}
                        {r.has_adjustment && (
                          <span
                            style={{ marginLeft: 6, fontSize: "0.72rem", fontWeight: 600, color: "var(--amber)" }}
                            title={t("fc_adjusted_badge")}
                          >
                            {t("fc_adjusted_badge")}
                          </span>
                        )}
                      </td>
                      <td className="dim">{r.category ?? "—"}</td>
                      <td className="dim">{methodLabel(r.method)}</td>
                      <td className="num mono dim">
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
                        {r.next12_total.toLocaleString()}
                      </td>
                      <td>
                        {r.order ? (
                          <span style={{ color: "var(--amber)", fontWeight: 600 }}>
                            📦{" "}
                            {t("fc_order_badge", {
                              qty: fmtQty(r.order.qty),
                              month: fmtMonth(r.order.order_by, lang),
                            })}
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
