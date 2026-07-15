import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { DiscontinuationReport } from "../api/types";

export default function Discontinuation() {
  const { t } = useLang();
  const [report, setReport] = useState<DiscontinuationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.discontinuationReport().then(setReport).catch((e) => setError(String(e.message ?? e)));
  }, []);

  return (
    <div>
      {error && <div className="banner warn">⚠️ {error}</div>}

      <div className="hero">
        <h2>{t("disc_title")}</h2>
        <p>{t("disc_sub")}</p>
      </div>

      {!report ? (
        <div className="card">
          <div className="empty">{error ? t("common_load_failed") : t("common_loading")}</div>
        </div>
      ) : (
        <>
          <div className="card">
            <div className="hd">
              <div>
                <div className="eyebrow">{t("disc_zero_eyebrow")}</div>
                <h3>{t("disc_zero_title")}</h3>
                <div className="desc">{report.zero_stock_note}</div>
              </div>
            </div>
            {report.zero_stock_candidates.length === 0 ? (
              <div className="empty">
                {report.sap_data_depth_days < 60
                  ? t("disc_zero_not_enough_history", { days: report.sap_data_depth_days })
                  : t("disc_zero_none_found")}
              </div>
            ) : (
              <div className="tablewrap" style={{ maxHeight: 420 }}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("disc_th_item")}</th>
                      <th>{t("disc_th_category")}</th>
                      <th>{t("disc_th_zero_since")}</th>
                      <th className="num">{t("disc_th_zero_days")}</th>
                      <th className="num">{t("disc_th_next6")}</th>
                      <th>{t("disc_th_no_reorder")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.zero_stock_candidates.map((c) => (
                      <tr key={c.item_id}>
                        <td style={{ fontWeight: 600 }}>{c.name}</td>
                        <td className="dim">{c.category ?? "—"}</td>
                        <td className="dim mono">{c.zero_since}</td>
                        <td className="num mono">{c.zero_duration_days}</td>
                        <td
                          className="num mono"
                          style={{ color: c.next_6mo_forecast > 0 ? "var(--amber)" : undefined }}
                        >
                          {c.next_6mo_forecast.toLocaleString()}
                        </td>
                        <td>{c.marked_no_reorder ? "✓" : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="hd">
              <div>
                <div className="eyebrow">{t("disc_decline_eyebrow")}</div>
                <h3>{t("disc_decline_title")}</h3>
                <div className="desc">{report.note}</div>
              </div>
            </div>
            {report.candidates.length === 0 ? (
              <div className="empty">{t("disc_decline_none_found")}</div>
            ) : (
              <div className="tablewrap" style={{ maxHeight: 420 }}>
                <table>
                  <thead>
                    <tr>
                      <th>{t("disc_th_item")}</th>
                      <th>{t("disc_th_category")}</th>
                      <th className="num">{t("disc_th_prior6")}</th>
                      <th className="num">{t("disc_th_recent6")}</th>
                      <th className="num">{t("disc_th_decline_pct")}</th>
                      <th className="num">{t("disc_th_next6")}</th>
                      <th>{t("disc_th_no_reorder")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {report.candidates.map((c) => (
                      <tr key={c.item_id}>
                        <td style={{ fontWeight: 600 }}>{c.name}</td>
                        <td className="dim">{c.category ?? "—"}</td>
                        <td className="num mono dim">{c.prior_6mo_actual.toLocaleString()}</td>
                        <td className="num mono dim">{c.recent_6mo_actual.toLocaleString()}</td>
                        <td className="num mono" style={{ color: "var(--amber)" }}>
                          -{c.decline_pct}%
                        </td>
                        <td className="num mono" style={{ fontWeight: 700 }}>
                          {c.next_6mo_forecast.toLocaleString()}
                        </td>
                        <td>{c.marked_no_reorder ? "✓" : "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
