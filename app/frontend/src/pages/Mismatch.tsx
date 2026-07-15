import { useEffect, useMemo, useState } from "react";
import { api, downloadReport } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { MismatchGroup, MismatchReport } from "../api/types";

type SevFilter = "ALL" | "HIGH" | "MEDIUM" | "LOW";

const SEV_ORDER: Record<MismatchGroup["severity"], number> = { HIGH: 0, MEDIUM: 1, LOW: 2 };

function sevStripe(sev: MismatchGroup["severity"]) {
  return sev === "HIGH" ? "red" : sev === "MEDIUM" ? "amber" : "green";
}

function severityPill(sev: string) {
  const cls = sev === "HIGH" ? "p-high" : sev === "MEDIUM" ? "p-medium" : "p-low";
  return <span className={`pill ${cls}`}>{sev}</span>;
}

export default function Mismatch() {
  const { t, lang } = useLang();
  const [report, setReport] = useState<MismatchReport | null>(null);
  const [sevFilter, setSevFilter] = useState<SevFilter>("ALL");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.mismatchReport().then(setReport).catch((e) => setError(String(e.message ?? e)));
  }, []);

  const groups = report?.groups ?? [];
  const counts = useMemo(
    () => ({
      HIGH: groups.filter((g) => g.severity === "HIGH").length,
      MEDIUM: groups.filter((g) => g.severity === "MEDIUM").length,
      LOW: groups.filter((g) => g.severity === "LOW").length,
    }),
    [groups]
  );
  const total = groups.length;

  const visible = useMemo(() => {
    const list = sevFilter === "ALL" ? groups : groups.filter((g) => g.severity === sevFilter);
    return [...list].sort(
      (a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || a.gap - b.gap
    );
  }, [groups, sevFilter]);

  const segItems: { key: SevFilter; label: string }[] = [
    { key: "ALL", label: lang === "ja" ? "すべて" : "All" },
    { key: "HIGH", label: "HIGH" },
    { key: "MEDIUM", label: "MEDIUM" },
    { key: "LOW", label: "LOW" },
  ];

  return (
    <div>
      <div className="page-head">
        <h2>{t("mismatch_title")}</h2>
        <p>{t("mismatch_sub")}</p>
      </div>

      {error && <div className="banner warn">⚠️ {error}</div>}

      <div className="banner info">ℹ️ {t("mismatch_banner")}</div>

      {/* ── severity KPI triage ─────────────────────────────────────── */}
      <div className="kpis">
        <div className="kpi accent-red">
          <div className="lab">{lang === "ja" ? "重大（HIGH）" : "High severity"}</div>
          <div className="val">{report ? counts.HIGH : "—"}</div>
          <div className="sub">
            {lang === "ja" ? "即時対応が必要なソケット" : "Sockets needing immediate action"}
          </div>
        </div>
        <div className="kpi accent-amber">
          <div className="lab">{lang === "ja" ? "注意（MEDIUM）" : "Medium severity"}</div>
          <div className="val">{report ? counts.MEDIUM : "—"}</div>
          <div className="sub">
            {lang === "ja" ? "計画差異の監視対象ソケット" : "Watch list — sockets with plan divergence"}
          </div>
        </div>
        <div className="kpi accent-green">
          <div className="lab">{lang === "ja" ? "良好（LOW）" : "Low / OK"}</div>
          <div className="val">{report ? counts.LOW : "—"}</div>
          <div className="sub">
            {lang === "ja" ? "整合が取れているソケット" : "Sockets in balance"}
          </div>
        </div>
        <div className="kpi">
          <div className="lab">{lang === "ja" ? "確認対象グループ" : "Groups checked"}</div>
          <div className="val">{report ? total : "—"}</div>
          {total > 0 && (
            <div className="stack-bar" style={{ marginTop: 10 }}>
              {counts.HIGH > 0 && (
                <span
                  style={{ width: `${(counts.HIGH / total) * 100}%`, background: "var(--red)" }}
                />
              )}
              {counts.MEDIUM > 0 && (
                <span
                  style={{ width: `${(counts.MEDIUM / total) * 100}%`, background: "var(--amber)" }}
                />
              )}
              {counts.LOW > 0 && (
                <span
                  style={{ width: `${(counts.LOW / total) * 100}%`, background: "var(--green)" }}
                />
              )}
            </div>
          )}
          <div className="sub">{t("mismatch_table_month", { month: report?.month ?? "—" })}</div>
        </div>
      </div>

      {/* ── triage list ─────────────────────────────────────────────── */}
      <div className="card">
        <div className="hd">
          <div>
            <h3>{t("mismatch_table_title")}</h3>
            <div className="desc">{t("mismatch_table_month", { month: report?.month ?? "—" })}</div>
          </div>
          <button className="btn" onClick={() => downloadReport("/export/mismatch", "mismatch.xlsx")}>
            {t("common_download_excel")}
          </button>
        </div>

        <div className="toolbar">
          <span className="eyebrow" style={{ margin: 0 }}>
            {lang === "ja" ? "深刻度で絞り込み" : "Filter by severity"}
          </span>
          <div className="seg">
            {segItems.map((s) => (
              <button
                key={s.key}
                className={sevFilter === s.key ? "on" : ""}
                onClick={() => setSevFilter(s.key)}
              >
                {s.label}
                {report && s.key !== "ALL" ? ` · ${counts[s.key]}` : ""}
              </button>
            ))}
          </div>
          <span className="dim" style={{ marginLeft: "auto", fontSize: "0.76rem" }}>
            {lang === "ja" ? `${visible.length} 件表示` : `${visible.length} shown`}
          </span>
        </div>

        {!report ? (
          <div className="empty">{error ? t("common_load_failed") : t("common_loading")}</div>
        ) : visible.length === 0 ? (
          <div className="empty">
            {lang === "ja" ? "該当するグループはありません" : "No groups match this filter"}
          </div>
        ) : (
          <div className="grid2" style={{ padding: 18 }}>
            {visible.map((g) => {
              const maxDemand = Math.max(g.cpu_demand, g.mb_demand, 1);
              return (
                <div key={g.socket} className={`card stripe-card ${sevStripe(g.severity)}`}>
                  <div className="hd">
                    <div>
                      <div className="eyebrow">{t("mismatch_th_socket")}</div>
                      <h3 className="mono">{g.socket}</h3>
                    </div>
                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      {g.takeorpay_n1 && (
                        <span className="pill p-high">{t("mismatch_attention")}</span>
                      )}
                      {severityPill(g.severity)}
                    </div>
                  </div>

                  <div style={{ padding: "14px 20px 16px" }}>
                    {/* CPU vs MB demand bars */}
                    {(
                      [
                        { lab: t("mismatch_th_cpu"), val: g.cpu_demand },
                        { lab: t("mismatch_th_mb"), val: g.mb_demand },
                      ] as const
                    ).map((row) => (
                      <div
                        key={row.lab}
                        style={{
                          display: "grid",
                          gridTemplateColumns: "1fr 72px 1.4fr",
                          alignItems: "center",
                          gap: 10,
                          marginBottom: 8,
                        }}
                      >
                        <span style={{ fontSize: "0.78rem", color: "var(--muted)", fontWeight: 600 }}>
                          {row.lab}
                        </span>
                        <span
                          className="mono"
                          style={{
                            fontSize: "0.84rem",
                            fontWeight: 700,
                            textAlign: "right",
                            fontVariantNumeric: "tabular-nums",
                          }}
                        >
                          {row.val.toLocaleString()}
                        </span>
                        <span className="minibar dim-track" style={{ width: "100%" }}>
                          <span
                            className="minibar"
                            style={{
                              display: "block",
                              width: `${(row.val / maxDemand) * 100}%`,
                              height: "100%",
                            }}
                          />
                        </span>
                      </div>
                    ))}

                    {/* gap + coverage */}
                    <div
                      style={{
                        display: "flex",
                        gap: 24,
                        marginTop: 12,
                        paddingTop: 12,
                        borderTop: "1px solid var(--line2)",
                      }}
                    >
                      <div>
                        <div className="eyebrow">{t("mismatch_th_gap")}</div>
                        <div className="statline">
                          <span
                            className="n"
                            style={{ color: g.gap < 0 ? "var(--red)" : "var(--green)" }}
                          >
                            {g.gap > 0 ? "+" : ""}
                            {g.gap.toLocaleString()}
                          </span>
                        </div>
                      </div>
                      <div>
                        <div className="eyebrow">{t("mismatch_th_pct")}</div>
                        <div className="statline">
                          <span className="n">{g.pct !== null ? `${g.pct}%` : "—"}</span>
                        </div>
                      </div>
                      <div style={{ marginLeft: "auto", textAlign: "right" }}>
                        <div className="eyebrow">{t("mismatch_th_exclusive")}</div>
                        <div style={{ fontSize: "0.84rem", fontWeight: 700, marginTop: 2 }}>
                          {g.exclusive ? t("mismatch_yes") : <span className="dim">—</span>}
                        </div>
                      </div>
                    </div>

                    {/* take-or-pay flag row */}
                    <div style={{ marginTop: 10, fontSize: "0.76rem", color: "var(--muted)" }}>
                      {t("mismatch_th_takeorpay")}:{" "}
                      {g.takeorpay_n1 ? (
                        <span style={{ color: "var(--red)", fontWeight: 700 }}>
                          {t("mismatch_attention")}
                        </span>
                      ) : (
                        <span className="dim">—</span>
                      )}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="card">
        <div className="hd">
          <h3>{t("mismatch_confirmed_title")}</h3>
        </div>
        <div style={{ padding: "16px 20px" }}>
          <div style={{ fontSize: "0.85rem", color: "var(--muted)", marginBottom: 10 }}>
            {t("mismatch_confirmed_body")}
          </div>
          {report && (
            (report.confirmed_rule_classifications.cpu.length === 0 &&
              report.confirmed_rule_classifications.mb.length === 0) ? (
              <div className="dim" style={{ fontSize: "0.82rem" }}>
                {t("mismatch_confirmed_none")}
              </div>
            ) : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {report.confirmed_rule_classifications.cpu.map((name) => (
                  <span key={`cpu-${name}`} className="pill p-medium mono">
                    {name} ({t("mismatch_th_cpu")})
                  </span>
                ))}
                {report.confirmed_rule_classifications.mb.map((name) => (
                  <span key={`mb-${name}`} className="pill p-medium mono">
                    {name} ({t("mismatch_th_mb")})
                  </span>
                ))}
              </div>
            )
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
