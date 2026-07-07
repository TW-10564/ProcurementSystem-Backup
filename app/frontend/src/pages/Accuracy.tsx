import { useEffect, useState } from "react";
import { api, downloadUrl } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { BacktestReport, PlannerAccuracy } from "../api/types";

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

export default function Accuracy() {
  const { t, lang } = useLang();
  const [report, setReport] = useState<BacktestReport | null>(null);
  const [fva, setFva] = useState<FvaReport | null>(null);
  const [rate, setRate] = useState<AdjustmentRate | null>(null);
  const [planner, setPlanner] = useState<PlannerAccuracy | null>(null);

  useEffect(() => {
    api.backtestReport().then(setReport);
    fetch(downloadUrl("/reports/fva")).then((r) => r.json()).then(setFva);
    fetch(downloadUrl("/reports/adjustment-rate")).then((r) => r.json()).then(setRate);
    api.plannerAccuracy().then(setPlanner);
  }, []);

  const maxWape = report
    ? Math.max(report.avg_flat_wape, report.avg_last_year_wape, report.avg_seasonal_wape) * 1.15
    : 1;

  return (
    <div>
      <div className="page-head">
        <h2>{t("accuracy_title")}</h2>
        <p>{t("accuracy_sub")}</p>
      </div>

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("planner_title")}</h3>
            <div className="desc">{t("planner_sub")}</div>
          </div>
        </div>
        {!planner ? (
          <div className="empty">{t("common_loading")}</div>
        ) : (
          <div style={{ padding: "18px 24px", display: "grid", gap: 22 }}>
            {/* live tracking status */}
            <div>
              <h4 style={{ margin: "0 0 8px", fontSize: "0.95rem" }}>
                {t("planner_live_title")}
              </h4>
              {planner.gradeable > 0 ? (
                <div className="kpis" style={{ marginBottom: 0 }}>
                  <div className="kpi">
                    <div className="lab">{t("planner_live_gradeable")}</div>
                    <div className="val">{planner.gradeable}</div>
                  </div>
                  <div className="kpi">
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
              <h4 style={{ margin: "0 0 6px", fontSize: "0.95rem" }}>
                {t("planner_cat_title")}
              </h4>
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
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="banner info" style={{ marginTop: 10 }}>
                ✅ <b>{t("planner_cat_conclusion_label")}:</b>{" "}
                {t("planner_cat_conclusion")}
              </div>
            </div>

            {/* ② per-SKU level */}
            <div>
              <h4 style={{ margin: "0 0 6px", fontSize: "0.95rem" }}>
                {t("planner_sku_title")}
              </h4>
              <div className="desc" style={{ marginBottom: 10 }}>
                {t("planner_sku_desc")}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
                <div className="card" style={{ margin: 0 }}>
                  <div style={{ padding: "12px 16px", fontWeight: 700, borderBottom: "1px solid var(--line)" }}>
                    {t("planner_sku_planner_label")}
                  </div>
                  <div className="kpis" style={{ margin: 0, padding: 14 }}>
                    <div className="kpi">
                      <div className="lab">{t("planner_sku_per_sku")}</div>
                      <div className="val">
                        {planner.historical_reference.per_sku_level.wape_per_sku}%
                      </div>
                    </div>
                    <div className="kpi">
                      <div className="lab">{t("planner_sku_total")}</div>
                      <div className="val">
                        {planner.historical_reference.per_sku_level.wape_channel_total}%
                      </div>
                    </div>
                  </div>
                </div>
                <div className="card" style={{ margin: 0 }}>
                  <div style={{ padding: "12px 16px", fontWeight: 700, borderBottom: "1px solid var(--line)" }}>
                    {t("planner_sku_our_label")}
                  </div>
                  <div className="kpis" style={{ margin: 0, padding: 14 }}>
                    <div className="kpi">
                      <div className="lab">{t("planner_sku_overall")}</div>
                      <div className="val">
                        {planner.historical_reference.per_sku_level.our_equivalent.overall_6mo_wape}%
                      </div>
                    </div>
                    <div className="kpi">
                      <div className="lab">{t("planner_sku_nearterm")}</div>
                      <div className="val" style={{ color: "var(--green)" }}>
                        {planner.historical_reference.per_sku_level.our_equivalent.near_term_m1_wape}%
                      </div>
                    </div>
                  </div>
                </div>
              </div>
              <div className="banner warn" style={{ marginTop: 10 }}>
                ⚠️ {t("planner_sku_conclusion")}
              </div>
            </div>

            {/* ③ one-off matched month */}
            <div>
              <h4 style={{ margin: "0 0 6px", fontSize: "0.95rem" }}>
                {t("planner_month_title")}
              </h4>
              <div className="desc" style={{ marginBottom: 10 }}>
                {t("planner_month_desc")}
              </div>
              <div className="tablewrap">
                <table>
                  <thead>
                    <tr>
                      <th></th>
                      <th className="num">{t("planner_th_wape")}</th>
                      <th>{t("dash_th_period")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td style={{ fontWeight: 600 }}>{t("planner_sku_planner_label")}</td>
                      <td className="num mono" style={{ fontWeight: 700 }}>
                        {planner.historical_reference.one_off_matched_month.planner.wape}%
                      </td>
                      <td className="dim">
                        {t("planner_month_lead", {
                          lead: planner.historical_reference.one_off_matched_month.planner.lead_time,
                        })}
                      </td>
                    </tr>
                    <tr>
                      <td style={{ fontWeight: 600 }}>{t("planner_sku_our_label")}</td>
                      <td className="num mono" style={{ fontWeight: 700 }}>
                        {planner.historical_reference.one_off_matched_month.system.wape}%
                      </td>
                      <td className="dim">
                        {t("planner_month_lead", {
                          lead: planner.historical_reference.one_off_matched_month.system.lead_time,
                        })}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div className="banner info" style={{ marginTop: 10 }}>
                ℹ️ {t("planner_month_conclusion")}
              </div>
            </div>

            <div className="dim" style={{ fontSize: "0.82rem", borderTop: "1px solid var(--line)", paddingTop: 14 }}>
              <b>{t("planner_settle_title")}:</b> {t("planner_settle_body")}
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("fva_title")}</h3>
            <div className="desc">{t("fva_desc")}</div>
          </div>
          <a className="btn" href={downloadUrl("/export/adjustments")}>
            {t("fva_download")}
          </a>
        </div>
        <div style={{ padding: "18px 24px" }}>
          {!fva ? (
            <div className="empty">{t("common_loading")}</div>
          ) : fva.scoreable_count === 0 ? (
            <div className="banner info">
              ℹ️ {fva.note ?? t("fva_no_scoreable")}
              {fva.total_adjustments_recorded > 0 && (
                <>{t("fva_recorded_note", { n: fva.total_adjustments_recorded })}</>
              )}
            </div>
          ) : (
            <div className="kpis" style={{ marginBottom: 0 }}>
              <div className="kpi">
                <div className="lab">{t("fva_scoreable_count")}</div>
                <div className="val">{fva.scoreable_count}</div>
              </div>
              <div className="kpi">
                <div className="lab">{t("fva_helped")}</div>
                <div className="val" style={{ color: "var(--green)" }}>
                  {fva.helped}
                </div>
              </div>
              <div className="kpi">
                <div className="lab">{t("fva_hurt")}</div>
                <div className="val" style={{ color: "var(--red)" }}>
                  {fva.hurt}
                </div>
              </div>
            </div>
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

      {!report ? (
        <div className="empty">{t("common_loading")}</div>
      ) : (
        <>
          <div className="card">
            <div className="hd">
              <div>
                <h3>{t("accuracy_wape_title")}</h3>
                <div className="desc">{t("accuracy_wape_desc", { n: report.n_periods })}</div>
              </div>
              <a className="btn" href={downloadUrl("/export/backtest")}>
                {t("common_download_excel")}
              </a>
            </div>
            <div style={{ padding: "20px 24px", display: "grid", gap: 18 }}>
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span style={{ fontWeight: 700 }}>{t("accuracy_method_seasonal")}</span>
                  <span className="mono">{report.avg_seasonal_wape}%</span>
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
    </div>
  );
}
