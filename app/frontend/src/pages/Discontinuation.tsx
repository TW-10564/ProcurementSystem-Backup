import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { DiscontinuationReport, DiscontinuationSuggestion } from "../api/types";
import { authorIsSelfAsserted, currentAuthor } from "../auth/author";

const AUTHOR_KEY = "onefcst_author";

function useAuthor() {
  // Under SSO the signed-in address is authoritative and the server enforces
  // it regardless of what we send (api/auth.py:effective_author). The typed
  // name survives only for the no-SSO case.
  const [author, setAuthor] = useState(() => currentAuthor());
  const update = (v: string) => {
    setAuthor(v);
    localStorage.setItem(AUTHOR_KEY, v);
  };
  return [author, update] as const;
}

const REVIEW_STATUSES = ["draft", "confirmed", "rejected"] as const;
type ReviewStatus = (typeof REVIEW_STATUSES)[number];

function ReviewQueue() {
  const { t } = useLang();
  const [status, setStatus] = useState<ReviewStatus>("draft");
  const [rows, setRows] = useState<DiscontinuationSuggestion[]>([]);
  const [counts, setCounts] = useState<Record<ReviewStatus, number>>({
    draft: 0,
    confirmed: 0,
    rejected: 0,
  });
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [author, setAuthor] = useAuthor();
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api
      .discontinuationSuggestions({ status })
      .then((r) => {
        setRows(r);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)))
      .finally(() => setLoading(false));
    api
      .discontinuationSuggestions({})
      .then((all) => {
        const c: Record<ReviewStatus, number> = { draft: 0, confirmed: 0, rejected: 0 };
        for (const r of all) c[r.status] = (c[r.status] ?? 0) + 1;
        setCounts(c);
      })
      .catch(() => {});
  };

  useEffect(load, [status]);

  const confirm = async (id: number) => {
    if (!author) return alert(t("disc_review_confirm_alert"));
    setBusyId(id);
    try {
      await api.confirmDiscontinuation(id, author);
      load();
    } finally {
      setBusyId(null);
    }
  };

  const reject = async (id: number) => {
    if (!author) return alert(t("disc_review_confirm_alert"));
    const reason = window.prompt(t("disc_review_reject_prompt")) ?? "";
    if (!reason.trim()) return;
    setBusyId(id);
    try {
      await api.rejectDiscontinuation(id, author, reason);
      load();
    } finally {
      setBusyId(null);
    }
  };

  const statusLabel: Record<ReviewStatus, string> = {
    draft: t("disc_review_status_draft"),
    confirmed: t("disc_review_status_confirmed"),
    rejected: t("disc_review_status_rejected"),
  };

  return (
    <div className="card">
      <div className="hd" style={{ flexWrap: "wrap", gap: 12 }}>
        <div>
          <div className="eyebrow">{t("disc_review_eyebrow")}</div>
          <h3 style={{ margin: "2px 0 0" }}>{t("disc_review_title")}</h3>
          <div className="desc">{t("disc_review_desc")}</div>
        </div>
      </div>

      <div
        className="toolbar"
        style={{
          padding: "10px 24px",
          borderBottom: "1px solid var(--line2)",
          display: "flex",
          flexWrap: "wrap",
          gap: 16,
          alignItems: "center",
        }}
      >
        <div className="seg">
          {REVIEW_STATUSES.map((s) => (
            <button key={s} className={status === s ? "on" : ""} onClick={() => setStatus(s)}>
              {statusLabel[s]} <span className="mono" style={{ opacity: 0.7 }}>{counts[s]}</span>
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: "auto" }}>
          <span className="avatar" title={t("common_author_label")}>
            {author ? author.slice(0, 2).toUpperCase() : "？"}
          </span>
          {/* Editable only when nobody has signed in — under SSO the server
              takes the author from the verified token and ignores this value. */}
          {authorIsSelfAsserted() ? (
            <input
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder={t("compat_author_placeholder")}
              aria-label={t("common_author_label")}
              style={{ width: 180 }}
            />
          ) : (
            <span
              title={t("common_author_label")}
              style={{ fontSize: "0.8rem", color: "#475569", maxWidth: 200,
                       overflow: "hidden", textOverflow: "ellipsis",
                       whiteSpace: "nowrap" }}
            >
              {author}
            </span>
          )}
        </div>
      </div>

      {status === "draft" && rows.length > 0 && (
        <div className="banner info" style={{ margin: "12px 24px 0" }}>
          ℹ️ {t("disc_review_confirm_note")}
        </div>
      )}

      {error && <div className="banner warn">⚠️ {error}</div>}

      {loading ? (
        <div className="empty">{t("common_loading")}</div>
      ) : rows.length === 0 ? (
        <div className="empty">
          {status === "draft" ? t("disc_review_none_draft") : t("disc_review_none_other")}
        </div>
      ) : (
        <div>
          {rows.map((r) => (
            <div className="list-row" key={r.id}>
              <div className="icon-tile amber">🚫</div>
              <div className="grow">
                <div className="ttl">{r.item_name ?? `#${r.item_id}`}</div>
                <div className="meta">
                  {r.category ?? "—"}
                  {r.source_category_file && (
                    <>
                      <span style={{ margin: "0 6px" }}>·</span>
                      {t("disc_review_th_source")}: {r.source_category_file}
                    </>
                  )}
                  {r.detected_at && (
                    <>
                      <span style={{ margin: "0 6px" }}>·</span>
                      {t("disc_review_th_detected")}: {r.detected_at.slice(0, 10)}
                    </>
                  )}
                  {r.reviewed_by && (
                    <>
                      <span style={{ margin: "0 6px" }}>·</span>
                      {t("disc_review_th_reviewed_by")}: {r.reviewed_by}
                    </>
                  )}
                </div>
              </div>
              <span className={`pill p-${r.status}`} style={{ flexShrink: 0 }}>
                {statusLabel[r.status]}
              </span>
              {status === "draft" && (
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button className="btn primary" disabled={busyId === r.id} onClick={() => confirm(r.id)}>
                    {t("disc_review_btn_confirm")}
                  </button>
                  <button className="btn danger" disabled={busyId === r.id} onClick={() => reject(r.id)}>
                    {t("disc_review_btn_reject")}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

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

      <ReviewQueue />

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
