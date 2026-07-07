import { useEffect, useState } from "react";
import { api, downloadUrl } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { MismatchReport } from "../api/types";

function severityPill(sev: string) {
  const cls = sev === "HIGH" ? "p-high" : sev === "MEDIUM" ? "p-medium" : "p-low";
  return <span className={`pill ${cls}`}>{sev}</span>;
}

export default function Mismatch() {
  const { t } = useLang();
  const [report, setReport] = useState<MismatchReport | null>(null);

  useEffect(() => {
    api.mismatchReport().then(setReport);
  }, []);

  return (
    <div>
      <div className="page-head">
        <h2>{t("mismatch_title")}</h2>
        <p>{t("mismatch_sub")}</p>
      </div>

      <div className="banner info">ℹ️ {t("mismatch_banner")}</div>

      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("mismatch_table_title")}</h3>
            <div className="desc">{t("mismatch_table_month", { month: report?.month ?? "—" })}</div>
          </div>
          <a className="btn" href={downloadUrl("/export/mismatch")}>
            {t("common_download_excel")}
          </a>
        </div>
        <div className="tablewrap">
          {!report ? (
            <div className="empty">{t("common_loading")}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("mismatch_th_socket")}</th>
                  <th className="num">{t("mismatch_th_cpu")}</th>
                  <th className="num">{t("mismatch_th_mb")}</th>
                  <th className="num">{t("mismatch_th_gap")}</th>
                  <th className="num">{t("mismatch_th_pct")}</th>
                  <th>{t("mismatch_th_exclusive")}</th>
                  <th>{t("mismatch_th_takeorpay")}</th>
                  <th>{t("mismatch_th_severity")}</th>
                </tr>
              </thead>
              <tbody>
                {report.groups.map((g) => (
                  <tr key={g.socket}>
                    <td style={{ fontWeight: 700 }}>{g.socket}</td>
                    <td className="num mono">{g.cpu_demand.toLocaleString()}</td>
                    <td className="num mono">{g.mb_demand.toLocaleString()}</td>
                    <td
                      className="num mono"
                      style={{ color: g.gap < 0 ? "var(--red)" : "var(--green)" }}
                    >
                      {g.gap > 0 ? "+" : ""}
                      {g.gap.toLocaleString()}
                    </td>
                    <td className="num mono">{g.pct !== null ? `${g.pct}%` : "—"}</td>
                    <td>{g.exclusive ? t("mismatch_yes") : <span className="dim">—</span>}</td>
                    <td>
                      {g.takeorpay_n1 ? (
                        <span className="pill p-high">{t("mismatch_attention")}</span>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </td>
                    <td>{severityPill(g.severity)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card">
        <div className="hd">
          <h3>{t("mismatch_history_title")}</h3>
        </div>
        <div style={{ padding: "16px 20px", fontSize: "0.85rem", color: "var(--muted)" }}>
          {t("mismatch_history_body")}
        </div>
      </div>
    </div>
  );
}
