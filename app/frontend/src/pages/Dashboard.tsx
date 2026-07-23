import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { BacktestReport, MismatchGroup, MismatchReport, Notification } from "../api/types";

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
  const attention: MismatchGroup[] =
    mismatch?.groups
      .filter((g) => g.severity === "HIGH" || g.severity === "MEDIUM")
      .sort((a, b) => (a.severity === b.severity ? Math.abs(b.gap) - Math.abs(a.gap) : a.severity === "HIGH" ? -1 : 1)) ?? [];

  const ja = lang === "ja";
  const num = "num mono" as const;

  const quickActions: {
    to: string;
    emoji: string;
    tone?: "green" | "amber" | "red";
    ttl: string;
    meta: string;
  }[] = [
    {
      to: "/forecast",
      emoji: "📈",
      tone: "green",
      ttl: ja ? "需要予測の確認" : "Review demand forecast",
      meta: ja
        ? "SKU別の予測値と発注提案"
        : "Per-SKU forecasts and order suggestions",
    },
    {
      to: "/mismatch",
      emoji: "⚖️",
      tone: highCount ? "red" : mediumCount ? "amber" : undefined,
      ttl: ja ? "CPU/MB 需給ミスマッチ" : "CPU/MB supply-demand mismatch",
      meta: mismatch
        ? ja
          ? `HIGH ${highCount}件・MEDIUM ${mediumCount}件（${mismatch.month}）`
          : `${highCount} HIGH, ${mediumCount} MEDIUM (${mismatch.month})`
        : t("common_loading"),
    },
    {
      to: "/compat",
      emoji: "🧩",
      tone: ruleCounts.draft ? "amber" : undefined,
      ttl: ja ? "互換ルールのレビュー" : "Review compatibility rules",
      meta: ja
        ? `ドラフト ${ruleCounts.draft.toLocaleString()}件が承認待ち（確定済 ${ruleCounts.confirmed.toLocaleString()}件）`
        : `${ruleCounts.draft.toLocaleString()} drafts pending (${ruleCounts.confirmed.toLocaleString()} confirmed)`,
    },
    {
      to: "/adjustments",
      emoji: "✏️",
      ttl: ja ? "予測の手動調整" : "Adjust forecasts manually",
      meta: ja
        ? "市場要因を反映した人手による上書きを登録"
        : "Record human overrides for market factors",
    },
  ];

  return (
    <div>
      {error && (
        <div className="banner warn">
          ⚠️ {t("dash_api_error", { error })}
          <code style={{ marginLeft: 6 }}>uvicorn onefcst.api.main:app --port 8600</code>
        </div>
      )}

      {/* ── hero band ─────────────────────────────────────────────── */}
      <div className="hero">
        <h2>{t("dash_title")}</h2>
        <p>{t("dash_sub")}</p>
        <div className="stats">
          <div className="stat">
            <div className="lab">{t("dash_kpi_accuracy")}</div>
            <div className="val">
              {backtest ? (
                <>
                  {backtest.avg_seasonal_wape}
                  <small>%</small>
                </>
              ) : (
                "—"
              )}
            </div>
          </div>
          <div className="stat">
            <div className="lab">{ja ? "HIGH ミスマッチ" : "HIGH mismatches"}</div>
            <div className="val">{mismatch ? highCount : "—"}</div>
          </div>
          <div className="stat">
            <div className="lab">{ja ? "MEDIUM ミスマッチ" : "MEDIUM mismatches"}</div>
            <div className="val">{mismatch ? mediumCount : "—"}</div>
          </div>
          <div className="stat">
            <div className="lab">{t("dash_kpi_pending_rules")}</div>
            <div className="val">{ruleCounts.draft.toLocaleString()}</div>
          </div>
        </div>
      </div>

      {/* ── two-column cockpit: pair the two SHORT list cards together
          (Quick actions, Needs attention) so grid-13's align-items:start
          doesn't leave a height-mismatch gap under either one. The two
          taller/wider sections (Audit log, Accuracy validation — the
          latter has a 5-column table that needs real width, not a
          squeezed narrow column) get their own full-width rows below,
          same as before this redesign attempt. ───────────────────── */}
      <div className="grid-13" style={{ marginTop: 18 }}>
        {/* LEFT — quick actions */}
        <div className="card">
          <div className="hd">
            <div>
              <div className="eyebrow">{ja ? "ナビゲーション" : "Navigation"}</div>
              <h3>{ja ? "クイックアクション" : "Quick actions"}</h3>
              <div className="desc">
                {ja ? "今日の業務の入口" : "Where to start today"}
              </div>
            </div>
          </div>
          <div>
            {quickActions.map((a) => (
              <Link
                key={a.to}
                to={a.to}
                className="list-row"
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <div className={`icon-tile${a.tone ? ` ${a.tone}` : ""}`}>{a.emoji}</div>
                <div className="grow">
                  <div className="ttl">{a.ttl}</div>
                  <div className="meta">{a.meta}</div>
                </div>
                <span className="dim" aria-hidden style={{ fontSize: "0.8rem" }}>
                  ›
                </span>
              </Link>
            ))}
          </div>
        </div>

        {/* RIGHT — needs attention */}
        <div className="card">
          <div className="hd">
            <div>
              <div className="eyebrow">
                {mismatch
                  ? ja
                    ? `対象月 ${mismatch.month}`
                    : `Target month ${mismatch.month}`
                  : "—"}
              </div>
              <h3>{ja ? "要対応" : "Needs attention"}</h3>
              <div className="desc">
                {ja
                  ? "CPU/MB ソケット別の需給ギャップ（HIGH・MEDIUM のみ）"
                  : "CPU/MB demand gaps by socket (HIGH and MEDIUM only)"}
              </div>
            </div>
            <Link to="/app/mismatch" className="btn">
              {ja ? "すべて表示" : "View all"}
            </Link>
          </div>
          {mismatch ? (
            attention.length ? (
              <div>
                {attention.map((g) => (
                  <div className="list-row" key={g.socket}>
                    <div className={`icon-tile ${g.severity === "HIGH" ? "red" : "amber"}`}>
                      ⚖️
                    </div>
                    <div className="grow">
                      <div className="ttl mono">{g.socket}</div>
                      <div className="meta">
                        {ja
                          ? `CPU ${g.cpu_demand.toLocaleString()} / MB ${g.mb_demand.toLocaleString()} — ギャップ ${g.gap.toLocaleString()}`
                          : `CPU ${g.cpu_demand.toLocaleString()} / MB ${g.mb_demand.toLocaleString()} — gap ${g.gap.toLocaleString()}`}
                        {g.pct != null && (
                          <span className="mono">{` (${g.pct}%)`}</span>
                        )}
                      </div>
                    </div>
                    <span className={`pill ${g.severity === "HIGH" ? "p-high" : "p-medium"}`}>
                      {g.severity}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="empty">
                {ja ? "対応が必要なミスマッチはありません" : "No mismatches need attention"}
              </div>
            )
          ) : (
            <div className="empty">{t("common_loading")}</div>
          )}
        </div>
      </div>

      {/* ── audit log (full width — a 6-row timeline reads better with
          room, not squeezed into a half-width column) ──────────────── */}
      <div className="card" style={{ marginTop: 18 }}>
        <div className="hd">
          <div>
            <div className="eyebrow">{ja ? "監査ログ" : "Audit log"}</div>
            <h3>{t("dash_activity_title")}</h3>
          </div>
          <Link to="/app/notifications" className="btn">
            {ja ? "すべて表示" : "View all"}
          </Link>
        </div>
        {activity.length ? (
          <ul className="timeline">
            {activity.map((a) => (
              <li key={a.id}>
                <div className="when mono">
                  {new Date(a.at).toLocaleString(ja ? "ja-JP" : "en-US")}
                  {" · "}
                  {a.who ?? "system"}
                </div>
                <div className="what">{a.what}</div>
              </li>
            ))}
          </ul>
        ) : (
          <div className="empty">{t("common_none_recorded")}</div>
        )}
      </div>

      {/* ── backtest evidence (full width — 5 columns need real room) ── */}
      <div className="card" style={{ marginTop: 18 }}>
        <div className="hd">
          <div>
            <div className="eyebrow">{ja ? "精度検証" : "Accuracy validation"}</div>
            <h3>{t("dash_backtest_title")}</h3>
            <div className="desc">{t("dash_backtest_desc")}</div>
          </div>
          {backtest && (
            <div className="statline">
              <span className="n mono">{backtest.avg_seasonal_wape}%</span>
              <span className="c">
                {t("dash_kpi_accuracy_sub", { n: backtest.n_periods })}
              </span>
            </div>
          )}
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
                    <td>{ja ? `${r.ki}期` : `FY${r.ki}`}</td>
                    <td className={num}>{r.total.toLocaleString()}</td>
                    <td className={num} style={{ color: "var(--green)", fontWeight: 700 }}>
                      {r.seasonal_wape}%
                    </td>
                    <td className={`${num} dim`}>{r.flat_wape}%</td>
                    <td className={`${num} dim`}>{r.last_year_wape}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">{t("common_loading")}</div>
        )}
      </div>
    </div>
  );
}
