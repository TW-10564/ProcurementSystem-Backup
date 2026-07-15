import { useEffect, useState } from "react";
import { api, downloadReport } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { BacktestReport, PlannerAccuracy, StockReconciliationReport } from "../api/types";

interface FvaReport {
  total_adjustments_recorded: number;
  scoreable_count: number;
  helped: number;
  hurt: number;
  note: string | null;
}

interface AdjustmentRate {
  total_forecast_cells: number;
  adjusted_cells: number;
  rate_pct: number | null;
}

function Bar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = Math.min(100, (value / max) * 100);
  return (
    <div className="bar-track">
      <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
    </div>
  );
}

/* in-table magnitude bar: fixed track, semantic fill */
function MiniBar({ value, max, color, width = 90 }: { value: number; max: number; color?: string; width?: number }) {
  const pct = Math.max(2, Math.min(100, (value / max) * 100));
  return (
    <span className="minibar dim-track" style={{ width }}>
      <span
        className="minibar"
        style={{ width: `${pct}%`, display: "block", ...(color ? { background: color } : {}) }}
      />
    </span>
  );
}

export default function Accuracy() {
  const { t, lang } = useLang();
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [fva, setFva] = useState<FvaReport | null>(null);
  const [rate, setRate] = useState<AdjustmentRate | null>(null);
  const [planner, setPlanner] = useState<PlannerAccuracy | null>(null);
  const [stockRecon, setStockRecon] = useState<StockReconciliationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onError = (e: unknown) => setError(String((e as Error).message ?? e));
    api.backtestReport().then(setReport).catch(onError);
    api.fvaReport().then(setFva).catch(onError);
    api.adjustmentRate().then(setRate).catch(onError);
    api.plannerAccuracy().then(setPlanner).catch(onError);
    api.stockReconciliation().then(setStockRecon).catch(onError);
  }, []);

  const maxWape = report
    ? Math.max(report.avg_flat_wape, report.avg_last_year_wape, report.avg_seasonal_wape) * 1.15
    : 1;

  // head-to-head verdict: one-off matched month (same target, same conditions)
  const matched = planner?.historical_reference.one_off_matched_month ?? null;
  const perSku = planner?.historical_reference.per_sku_level ?? null;
  const catLevel = planner?.historical_reference.category_level ?? null;
  const systemWins = matched ? matched.system.wape < matched.planner.wape : false;
  const gapPts = matched ? Math.abs(matched.planner.wape - matched.system.wape) : 0;
  const gapStr = gapPts.toFixed(1);
  const h2hMax = matched ? Math.max(matched.system.wape, matched.planner.wape) * 1.15 : 1;
  const catMax = catLevel
    ? Math.max(...catLevel.series.map((r) => r.wape), catLevel.our_equivalent.wape) * 1.1
    : 1;

  return (
    <div>
      {error && <div className="banner warn">⚠️ {error}</div>}

      {/* ── hero band ─────────────────────────────────────────── */}
      <div className="hero">
        <h2>{t("accuracy_title")}</h2>
        <p>{t("accuracy_sub")}</p>
        <div className="stats">
          <div className="stat">
            <div className="lab">{lang === "ja" ? "システム誤差（同一月比較）" : "System error (matched month)"}</div>
            <div className="val">
              {matched ? matched.system.wape : "—"}
              <small>%</small>
            </div>
          </div>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "プランナー誤差（同一月比較）" : "Planner error (matched month)"}</div>
            <div className="val">
              {matched ? matched.planner.wape : "—"}
              <small>%</small>
            </div>
          </div>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "採点対象の調整" : "Scoreable adjustments"}</div>
            <div className="val">{fva ? fva.scoreable_count : "—"}</div>
          </div>
          <div className="stat">
            <div className="lab">{lang === "ja" ? "バックテスト期間数" : "Backtest periods"}</div>
            <div className="val">{report ? report.n_periods : "—"}</div>
          </div>
        </div>
      </div>

      {!planner ? (
        <div className="card">
          <div className="empty">{error ? t("common_load_failed") : t("common_loading")}</div>
        </div>
      ) : (
        <>
          {/* ── verdict ───────────────────────────────────────── */}
          {matched && (
            <div className={`card stripe-card ${systemWins ? "green" : "amber"}`} style={{ marginBottom: 18 }}>
              <div style={{ padding: "18px 24px", display: "flex", alignItems: "center", gap: 18, flexWrap: "wrap" }}>
                <div className={`icon-tile ${systemWins ? "green" : "amber"}`}>{systemWins ? "🏆" : "⚖️"}</div>
                <div style={{ flex: 1, minWidth: 260 }}>
                  <div className="eyebrow">
                    {lang === "ja" ? "判定 — 同一月・直接比較" : "Verdict — matched-month head-to-head"}
                  </div>
                  <div className="statline" style={{ marginTop: 4 }}>
                    <span className="n">
                      {systemWins
                        ? lang === "ja" ? "システム予測が優勢" : "System forecast leads"
                        : lang === "ja" ? "プランナーが優勢" : "Planner leads"}
                    </span>
                    <span className={`delta ${systemWins ? "good" : "bad"}`}>
                      {systemWins ? "▲" : "▼"} {gapStr}pt
                    </span>
                    <span className="c">
                      {lang === "ja"
                        ? `誤差の差 ・ 対象 ${matched.target_month} ・ ${matched.system.n_skus} SKU`
                        : `Error gap · target ${matched.target_month} · ${matched.system.n_skus} SKUs`}
                    </span>
                  </div>
                </div>
                <div className="pill">{t("planner_month_title")}</div>
              </div>
              <div className="banner info" style={{ margin: "0 24px 18px" }}>
                ℹ️ {t("planner_month_conclusion")}
              </div>
            </div>
          )}

          {/* ── head-to-head cards ────────────────────────────── */}
          <div className="grid2" style={{ marginBottom: 18 }}>
            {/* system */}
            <div className="card stripe-card brand" style={{ margin: 0 }}>
              <div className="hd">
                <div>
                  <div className="eyebrow">{lang === "ja" ? "AI ベースライン" : "AI baseline"}</div>
                  <h3>{t("planner_sku_our_label")}</h3>
                </div>
                {matched && systemWins && (
                  <span className="delta good">▲ {lang === "ja" ? "優勢" : "ahead"}</span>
                )}
              </div>
              <div style={{ padding: "16px 24px", display: "grid", gap: 14 }}>
                {matched && (
                  <div>
                    <div className="statline">
                      <span className="n" style={{ color: "var(--green)" }}>{matched.system.wape}%</span>
                      <span className="c">
                        {lang === "ja" ? "誤差" : "Error"} ・ {t("planner_month_lead", { lead: matched.system.lead_time })}
                      </span>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <MiniBar value={matched.system.wape} max={h2hMax} color="var(--green)" width={220} />
                    </div>
                  </div>
                )}
                {perSku && (
                  <div style={{ display: "flex", gap: 28, flexWrap: "wrap", borderTop: "1px solid var(--line2)", paddingTop: 12 }}>
                    <div>
                      <div className="eyebrow">{t("planner_sku_overall")}</div>
                      <div className="statline">
                        <span className="n" style={{ fontSize: "1.15rem" }}>{perSku.our_equivalent.overall_6mo_wape}%</span>
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow">{t("planner_sku_nearterm")}</div>
                      <div className="statline">
                        <span className="n" style={{ fontSize: "1.15rem", color: "var(--green)" }}>
                          {perSku.our_equivalent.near_term_m1_wape}%
                        </span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>

            {/* planner */}
            <div className="card stripe-card amber" style={{ margin: 0 }}>
              <div className="hd">
                <div>
                  <div className="eyebrow">{lang === "ja" ? "人手による計画" : "Human planning"}</div>
                  <h3>{t("planner_sku_planner_label")}</h3>
                </div>
                {matched && !systemWins && (
                  <span className="delta good">▲ {lang === "ja" ? "優勢" : "ahead"}</span>
                )}
                {matched && systemWins && (
                  <span className="delta bad">▼ {gapStr}pt</span>
                )}
              </div>
              <div style={{ padding: "16px 24px", display: "grid", gap: 14 }}>
                {matched && (
                  <div>
                    <div className="statline">
                      <span className="n" style={{ color: "var(--amber)" }}>{matched.planner.wape}%</span>
                      <span className="c">
                        {lang === "ja" ? "誤差" : "Error"} ・ {t("planner_month_lead", { lead: matched.planner.lead_time })}
                      </span>
                    </div>
                    <div style={{ marginTop: 8 }}>
                      <MiniBar value={matched.planner.wape} max={h2hMax} color="var(--amber)" width={220} />
                    </div>
                  </div>
                )}
                {perSku && (
                  <div style={{ display: "flex", gap: 28, flexWrap: "wrap", borderTop: "1px solid var(--line2)", paddingTop: 12 }}>
                    <div>
                      <div className="eyebrow">{t("planner_sku_per_sku")}</div>
                      <div className="statline">
                        <span className="n" style={{ fontSize: "1.15rem" }}>{perSku.wape_per_sku}%</span>
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow">{t("planner_sku_total")}</div>
                      <div className="statline">
                        <span className="n" style={{ fontSize: "1.15rem" }}>{perSku.wape_channel_total}%</span>
                      </div>
                    </div>
                    <div>
                      <div className="eyebrow">{lang === "ja" ? "対象" : "Scope"}</div>
                      <div className="statline">
                        <span className="n" style={{ fontSize: "1.15rem" }}>{perSku.models}</span>
                        <span className="c">{lang === "ja" ? `機種 ・ ${perSku.months}ヶ月` : `models · ${perSku.months} mo`}</span>
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── deep dive: planner comparison detail ──────────── */}
          <div className="card">
            <div className="hd">
              <div>
                <h3>{t("planner_title")}</h3>
                <div className="desc">{t("planner_sub")}</div>
              </div>
            </div>
            <div style={{ padding: "18px 24px", display: "grid", gap: 22 }}>
              <div className="banner info" style={{ margin: 0 }}>
                ℹ️ {t("planner_wape_explainer")}
              </div>
              {/* live tracking status */}
              <div>
                <div className="eyebrow" style={{ marginBottom: 8 }}>{t("planner_live_title")}</div>
                {planner.gradeable > 0 ? (
                  <div className="kpis" style={{ marginBottom: 0 }}>
                    <div className="kpi">
                      <div className="lab">{t("planner_live_gradeable")}</div>
                      <div className="val">{planner.gradeable}</div>
                    </div>
                    <div className="kpi accent-amber">
                      <div className="lab">{t("planner_live_wape")}</div>
                      <div className="val" style={{ color: "var(--amber)" }}>
                        {planner.wape}%
                      </div>
                    </div>
                  </div>
                ) : (
                  <div className="banner info">
                    ℹ️ {planner.note} {t("planner_live_pending")}
                  </div>
                )}
              </div>

              {/* ① category level */}
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{t("planner_cat_title")}</div>
                <div className="desc" style={{ marginBottom: 10 }}>
                  {t("planner_cat_desc")}
                </div>
                <div className="tablewrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("planner_th_category")}</th>
                        <th>{t("planner_th_period")}</th>
                        <th className="num">{t("planner_th_months")}</th>
                        <th className="num">{t("planner_th_wape")}</th>
                        <th></th>
                      </tr>
                    </thead>
                    <tbody>
                      {planner.historical_reference.category_level.series.map((r, i) => (
                        <tr key={i}>
                          <td>{r.category}</td>
                          <td className="dim mono">{r.range}</td>
                          <td className="num mono dim">{r.months}</td>
                          <td
                            className="num mono"
                            style={{ fontWeight: 700, color: r.wape > 60 ? "var(--red)" : undefined }}
                          >
                            {r.wape}%
                          </td>
                          <td>
                            <MiniBar
                              value={r.wape}
                              max={catMax}
                              color={r.wape > 60 ? "var(--red)" : "var(--amber)"}
                            />
                          </td>
                        </tr>
                      ))}
                      <tr style={{ borderTop: "2px solid var(--line)" }}>
                        <td style={{ fontWeight: 700 }} colSpan={2}>
                          {t("planner_our_row")}
                        </td>
                        <td></td>
                        <td className="num mono" style={{ fontWeight: 700, color: "var(--green)" }}>
                          {planner.historical_reference.category_level.our_equivalent.wape}%
                        </td>
                        <td>
                          <MiniBar
                            value={planner.historical_reference.category_level.our_equivalent.wape}
                            max={catMax}
                            color="var(--green)"
                          />
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
                <div className="banner info" style={{ marginTop: 10 }}>
                  ✅ <b>{t("planner_cat_conclusion_label")}:</b>{" "}
                  {t("planner_cat_conclusion")}
                </div>
              </div>

              {/* ② per-SKU level — caveat */}
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{t("planner_sku_title")}</div>
                <div className="desc" style={{ marginBottom: 10 }}>
                  {t("planner_sku_desc")}
                </div>
                <div className="banner warn">
                  ⚠️ {t("planner_sku_conclusion")}
                </div>
              </div>

              {/* ③ one-off matched month — detail rows */}
              <div>
                <div className="eyebrow" style={{ marginBottom: 4 }}>{t("planner_month_title")}</div>
                <div className="desc" style={{ marginBottom: 10 }}>
                  {t("planner_month_desc")}
                </div>
                {matched && (
                  <div style={{ border: "1px solid var(--line2)", borderRadius: 10, overflow: "hidden" }}>
                    <div className="list-row">
                      <div className={`icon-tile ${systemWins ? "amber" : "green"}`} style={{ width: 30, height: 30 }}>
                        👤
                      </div>
                      <div className="grow">
                        <div className="ttl">{t("planner_sku_planner_label")}</div>
                        <div className="meta">{t("planner_month_lead", { lead: matched.planner.lead_time })}</div>
                      </div>
                      <MiniBar value={matched.planner.wape} max={h2hMax} color="var(--amber)" />
                      <span className="mono" style={{ fontWeight: 700, minWidth: 58, textAlign: "right" }}>
                        {matched.planner.wape}%
                      </span>
                    </div>
                    <div className="list-row">
                      <div className={`icon-tile ${systemWins ? "green" : "amber"}`} style={{ width: 30, height: 30 }}>
                        🤖
                      </div>
                      <div className="grow">
                        <div className="ttl">{t("planner_sku_our_label")}</div>
                        <div className="meta">{t("planner_month_lead", { lead: matched.system.lead_time })}</div>
                      </div>
                      <MiniBar value={matched.system.wape} max={h2hMax} color="var(--green)" />
                      <span className="mono" style={{ fontWeight: 700, minWidth: 58, textAlign: "right", color: systemWins ? "var(--green)" : undefined }}>
                        {matched.system.wape}%
                      </span>
                    </div>
                  </div>
                )}
              </div>

              <div className="dim" style={{ fontSize: "0.82rem", borderTop: "1px solid var(--line)", paddingTop: 14 }}>
                <b>{t("planner_settle_title")}:</b> {t("planner_settle_body")}
              </div>
            </div>
          </div>
        </>
      )}

      {/* ── FVA: did human adjustments help? ──────────────────── */}
      <div className="card">
        <div className="hd">
          <div>
            <div className="eyebrow">FVA</div>
            <h3>{t("fva_title")}</h3>
            <div className="desc">{t("fva_desc")}</div>
          </div>
          <button className="btn" onClick={() => downloadReport("/export/adjustments", "adjustments.xlsx")}>
            {t("fva_download")}
          </button>
        </div>
        <div style={{ padding: "18px 24px" }}>
          {!fva ? (
            <div className="empty">{error ? t("common_load_failed") : t("common_loading")}</div>
          ) : fva.scoreable_count === 0 ? (
            <div className="banner info">
              ℹ️ {fva.note ?? t("fva_no_scoreable")}
              {fva.total_adjustments_recorded > 0 && (
                <>{t("fva_recorded_note", { n: fva.total_adjustments_recorded })}</>
              )}
            </div>
          ) : (
            <>
              <div className="kpis" style={{ marginBottom: 0 }}>
                <div className="kpi">
                  <div className="lab">{t("fva_scoreable_count")}</div>
                  <div className="val">{fva.scoreable_count}</div>
                </div>
                <div className="kpi accent-green">
                  <div className="lab">{t("fva_helped")}</div>
                  <div className="val" style={{ color: "var(--green)" }}>
                    {fva.helped}
                  </div>
                </div>
                <div className="kpi accent-red">
                  <div className="lab">{t("fva_hurt")}</div>
                  <div className="val" style={{ color: "var(--red)" }}>
                    {fva.hurt}
                  </div>
                </div>
              </div>
              {fva.helped + fva.hurt > 0 && (
                <div style={{ marginTop: 14 }}>
                  <div className="stack-bar">
                    <span style={{ width: `${(fva.helped / (fva.helped + fva.hurt)) * 100}%`, background: "var(--green)" }} />
                    <span style={{ width: `${(fva.hurt / (fva.helped + fva.hurt)) * 100}%`, background: "var(--red)" }} />
                  </div>
                  <div className="dim" style={{ fontSize: "0.76rem", marginTop: 6 }}>
                    {lang === "ja" ? "改善・悪化の内訳" : "Helped vs hurt breakdown"}
                  </div>
                </div>
              )}
            </>
          )}
          {rate && rate.rate_pct !== null && (
            <div className="dim" style={{ marginTop: 14, fontSize: "0.82rem" }}>
              {t("fva_adjustment_rate_note", {
                pct: rate.rate_pct,
                adj: rate.adjusted_cells,
                total: rate.total_forecast_cells,
              })}
            </div>
          )}
        </div>
      </div>

      {/* ── backtest: method benchmark ────────────────────────── */}
      {!report ? (
        <div className="empty">{error ? t("common_load_failed") : t("common_loading")}</div>
      ) : (
        <>
          <div className="card">
            <div className="hd">
              <div>
                <div className="eyebrow">{lang === "ja" ? "手法ベンチマーク" : "Method benchmark"}</div>
                <h3>{t("accuracy_wape_title")}</h3>
                <div className="desc">{t("accuracy_wape_desc", { n: report.n_periods })}</div>
              </div>
              <button className="btn" onClick={() => downloadReport("/export/backtest", "backtest.xlsx")}>
                {t("common_download_excel")}
              </button>
            </div>
            <div style={{ padding: "20px 24px", display: "grid", gap: 18 }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontWeight: 700 }}>
                    {t("accuracy_method_seasonal")}{" "}
                    <span className="pill" style={{ marginLeft: 6 }}>
                      {lang === "ja" ? "採用中" : "In use"}
                    </span>
                  </span>
                  <span className="mono" style={{ fontWeight: 700 }}>{report.avg_seasonal_wape}%</span>
                </div>
                <Bar value={report.avg_seasonal_wape} max={maxWape} color="var(--green)" />
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span className="dim">{t("accuracy_method_lastyear")}</span>
                  <span className="mono dim">{report.avg_last_year_wape}%</span>
                </div>
                <Bar value={report.avg_last_year_wape} max={maxWape} color="var(--amber)" />
              </div>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span className="dim">{t("accuracy_method_flat")}</span>
                  <span className="mono dim">{report.avg_flat_wape}%</span>
                </div>
                <Bar value={report.avg_flat_wape} max={maxWape} color="var(--faint)" />
              </div>
            </div>
          </div>

          <div className="card">
            <div className="hd">
              <h3>{t("accuracy_detail_title")}</h3>
            </div>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("dash_th_period")}</th>
                    <th className="num">{t("accuracy_th_units")}</th>
                    <th className="num">{t("accuracy_th_seasonal")}</th>
                    <th></th>
                    <th className="num">{t("accuracy_th_lastyear")}</th>
                    <th className="num">{t("accuracy_th_flat")}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.rows.map((r) => (
                    <tr key={r.ki}>
                      <td>{lang === "ja" ? `${r.ki}期` : `FY${r.ki}`}</td>
                      <td className="num mono">{r.total.toLocaleString()}</td>
                      <td className="num mono" style={{ color: "var(--green)", fontWeight: 700 }}>
                        {r.seasonal_wape}%
                      </td>
                      <td>
                        <MiniBar value={r.seasonal_wape} max={maxWape} color="var(--green)" width={72} />
                      </td>
                      <td className="num mono dim">{r.last_year_wape}%</td>
                      <td className="num mono dim">{r.flat_wape}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* ── SAP vs ONE stock reconciliation ──────────────────────── */}
      {stockRecon && (
        <div className="card">
          <div className="hd">
            <div>
              <div className="eyebrow">{t("stockrecon_eyebrow")}</div>
              <h3>{t("stockrecon_title")}</h3>
              <div className="desc">{t("stockrecon_desc")}</div>
            </div>
          </div>
          <div style={{ padding: "20px 24px", display: "flex", gap: 32, flexWrap: "wrap" }}>
            <div>
              <div className="lab">{t("stockrecon_sap_covered")}</div>
              <div className="val">
                {stockRecon.sap_covered_items}/{stockRecon.generated_for_items}
              </div>
            </div>
            <div>
              <div className="lab">{t("stockrecon_divergences")}</div>
              <div className="val" style={{ color: stockRecon.divergences.length ? "var(--amber)" : undefined }}>
                {stockRecon.divergences.length}
              </div>
            </div>
            <div>
              <div className="lab">{t("stockrecon_no_coverage")}</div>
              <div className="val" style={{ color: stockRecon.no_sap_coverage.length ? "var(--amber)" : undefined }}>
                {stockRecon.no_sap_coverage.length}
              </div>
            </div>
          </div>

          {stockRecon.divergences.length > 0 && (
            <div className="tablewrap" style={{ maxHeight: 320 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("stockrecon_th_item")}</th>
                    <th>{t("stockrecon_th_category")}</th>
                    <th className="num">{t("stockrecon_th_sap")}</th>
                    <th className="num">{t("stockrecon_th_one")}</th>
                    <th className="num">{t("stockrecon_th_diff")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stockRecon.divergences.map((d) => (
                    <tr key={d.item_id}>
                      <td style={{ fontWeight: 600 }}>{d.name}</td>
                      <td className="dim">{d.category ?? "—"}</td>
                      <td className="num mono">{d.sap_qty.toLocaleString()}</td>
                      <td className="num mono dim">{Math.round(d.one_balance).toLocaleString()}</td>
                      <td className="num mono" style={{ color: "var(--amber)" }}>
                        {d.pct_diff}%
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {stockRecon.no_sap_coverage.length > 0 && (
            <div className="tablewrap" style={{ maxHeight: 320 }}>
              <table>
                <thead>
                  <tr>
                    <th>{t("stockrecon_th_item")}</th>
                    <th>{t("stockrecon_th_category")}</th>
                    <th className="num">{t("stockrecon_th_trusted")}</th>
                    <th>{t("stockrecon_th_confidence")}</th>
                  </tr>
                </thead>
                <tbody>
                  {stockRecon.no_sap_coverage.map((r) => (
                    <tr key={r.item_id}>
                      <td style={{ fontWeight: r.tier === 1 ? 700 : 400 }}>{r.name}</td>
                      <td className="dim">{r.category ?? "—"}</td>
                      <td className="num mono">{(r.trusted_value ?? 0).toLocaleString()}</td>
                      <td className="dim">
                        {r.confidence === "one_only_negative_clamped"
                          ? t("stockrecon_confidence_clamped")
                          : t("stockrecon_confidence_unverified")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
