import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { BacktestReport, MismatchReport, Notification } from "../api/types";

export default function Dashboard() {
  const { t, lang } = useLang();
  const [mismatch, setMismatch] = useState<MismatchReport | null>(null);
  const [backtest, setBacktest] = useState<BacktestReport | null>(null);
  const [activity, setActivity] = useState<Notification[]>([]);
  const [ruleCounts, setRuleCounts] = useState<{ draft: number; confirmed: number }>({
    draft: 0,
    confirmed: 0,
  });
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.mismatchReport(),
      api.backtestReport(),
      api.notifications(6),
      api.compatRules({ status: "draft" }),
      api.compatRules({ status: "confirmed" }),
    ])
      .then(([m, b, n, draft, confirmed]) => {
        setMismatch(m);
        setBacktest(b);
        setActivity(n);
        setRuleCounts({ draft: draft.length, confirmed: confirmed.length });
      })
      .catch((e) => setError(String(e.message ?? e)));
  }, []);

  const highCount = mismatch?.groups.filter((g) => g.severity === "HIGH").length ?? 0;
  const mediumCount = mismatch?.groups.filter((g) => g.severity === "MEDIUM").length ?? 0;

  return (
    <div>
      <div className="page-head">
        <h2>{t("dash_title")}</h2>
        <p>{t("dash_sub")}</p>
      </div>

      {error && (
        <div className="banner warn">
          ⚠️ {t("dash_api_error", { error })}
          <code style={{ marginLeft: 6 }}>uvicorn onefcst.api.main:app --port 8600</code>
        </div>
      )}

      <div className="kpis">
        <div className="kpi">
          <div className="lab">{t("dash_kpi_accuracy")}</div>
          <div className="val">
            {backtest ? `${backtest.avg_seasonal_wape}%` : "—"}
          </div>
          <div className="sub">
            {t("dash_kpi_accuracy_sub", { n: backtest?.n_periods ?? "—" })}
          </div>
        </div>
        <div className="kpi">
          <div className="lab">{t("dash_kpi_high_mismatch")}</div>
          <div className="val" style={{ color: highCount ? "var(--red)" : undefined }}>
            {mismatch ? highCount : "—"}
          </div>
          <div className="sub">
            {t("dash_kpi_high_mismatch_sub", { n: mediumCount, month: mismatch?.month ?? "—" })}
          </div>
        </div>
        <div className="kpi">
          <div className="lab">{t("dash_kpi_pending_rules")}</div>
          <div className="val">{ruleCounts.draft.toLocaleString()}</div>
          <div className="sub">{t("dash_kpi_pending_rules_sub", { n: ruleCounts.confirmed.toLocaleString() })}</div>
        </div>
        <div className="kpi">
          <div className="lab">{t("dash_kpi_activity")}</div>
          <div className="val">{activity.length}</div>
          <div className="sub">{t("dash_kpi_activity_sub")}</div>
        </div>
      </div>

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("dash_backtest_title")}</h3>
            <div className="desc">{t("dash_backtest_desc")}</div>
          </div>
        </div>
        {backtest ? (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>{t("dash_th_period")}</th>
                  <th className="num">{t("dash_th_actual_total")}</th>
                  <th className="num">{t("dash_th_seasonal")}</th>
                  <th className="num">{t("dash_th_flat")}</th>
                  <th className="num">{t("dash_th_lastyear")}</th>
                </tr>
              </thead>
              <tbody>
                {backtest.rows.map((r) => (
                  <tr key={r.ki}>
                    <td>{lang === "ja" ? `${r.ki}期` : `FY${r.ki}`}</td>
                    <td className="num mono">{r.total.toLocaleString()}</td>
                    <td className="num mono" style={{ color: "var(--green)", fontWeight: 700 }}>
                      {r.seasonal_wape}%
                    </td>
                    <td className="num mono dim">{r.flat_wape}%</td>
                    <td className="num mono dim">{r.last_year_wape}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">{t("common_loading")}</div>
        )}
      </div>

      <div className="card">
        <div className="hd">
          <h3>{t("dash_activity_title")}</h3>
        </div>
        {activity.length ? (
          <div className="tablewrap" style={{ maxHeight: 260 }}>
            <table>
              <tbody>
                {activity.map((a) => (
                  <tr key={a.id}>
                    <td className="dim mono" style={{ width: 160 }}>
                      {new Date(a.at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")}
                    </td>
                    <td style={{ fontWeight: 600 }}>{a.who ?? "system"}</td>
                    <td className="dim">{a.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">{t("common_none_recorded")}</div>
        )}
      </div>
    </div>
  );
}
