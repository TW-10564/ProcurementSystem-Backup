import { useEffect, useState } from "react";
import { api, downloadUrl } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { CompatRule } from "../api/types";

const AUTHOR_KEY = "onefcst_author";

function useAuthor() {
  const [author, setAuthor] = useState(
    () => localStorage.getItem(AUTHOR_KEY) ?? "",
  );
  const update = (v: string) => {
    setAuthor(v);
    localStorage.setItem(AUTHOR_KEY, v);
  };
  return [author, update] as const;
}

export default function CompatReview() {
  const { t } = useLang();
  const [rules, setRules] = useState<CompatRule[]>([]);
  const [status, setStatus] = useState<string>("draft");
  const [minConfidence, setMinConfidence] = useState(0.9);
  const [loading, setLoading] = useState(false);
  const [author, setAuthor] = useAuthor();
  const [busyId, setBusyId] = useState<number | null>(null);

  const load = () => {
    setLoading(true);
    api
      .compatRules({ status: status || undefined, min_confidence: undefined })
      .then(setRules)
      .finally(() => setLoading(false));
  };

  useEffect(load, [status]);

  const confirm = async (id: number) => {
    if (!author) return alert(t("compat_bulk_confirm_alert"));
    setBusyId(id);
    try {
      await api.confirmRule(id, author);
      load();
    } finally {
      setBusyId(null);
    }
  };

  const reject = async (id: number) => {
    if (!author) return alert(t("compat_bulk_confirm_alert"));
    const reason = window.prompt(t("compat_reject_prompt")) ?? "";
    if (!reason.trim()) return;
    setBusyId(id);
    try {
      await api.rejectRule(id, author, reason);
      load();
    } finally {
      setBusyId(null);
    }
  };

  const bulkConfirm = async () => {
    if (!author) return alert(t("compat_bulk_confirm_alert"));
    if (!window.confirm(t("compat_bulk_confirm_confirm", { t: minConfidence }))) return;
    const res = await api.bulkConfirm(author, minConfidence);
    alert(t("compat_bulk_confirm_done", { n: res.confirmed }));
    load();
  };

  const shown = rules.filter((r) => r.confidence >= minConfidence || status !== "draft");

  return (
    <div>
      <div className="page-head">
        <h2>{t("compat_title")}</h2>
        <p>
          {t("compat_sub_pre")} <b>{t("compat_sub_bold")}</b> {t("compat_sub_post")}
        </p>
      </div>

      <div className="banner info">ℹ️ {t("compat_banner")}</div>

      <div className="card">
        <div className="hd" style={{ gap: 16 }}>
          <div style={{ display: "flex", gap: 12, alignItems: "flex-end", flexWrap: "wrap" }}>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>{t("common_author_label")}</label>
              <input
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                placeholder={t("compat_author_placeholder")}
                style={{ width: 200 }}
              />
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>{t("compat_status_label")}</label>
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="draft">{t("compat_status_draft")}</option>
                <option value="confirmed">{t("compat_status_confirmed")}</option>
                <option value="rejected">{t("compat_status_rejected")}</option>
              </select>
            </div>
            <div className="field" style={{ marginBottom: 0 }}>
              <label>{t("compat_confidence_label")}</label>
              <select
                value={minConfidence}
                onChange={(e) => setMinConfidence(Number(e.target.value))}
              >
                <option value={0}>{t("compat_confidence_all")}</option>
                <option value={0.9}>{t("compat_confidence_high")}</option>
                <option value={0.7}>{t("compat_confidence_medium")}</option>
              </select>
            </div>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            {status === "draft" && (
              <button className="btn primary" onClick={bulkConfirm}>
                {t("compat_bulk_confirm")}
              </button>
            )}
            <a className="btn" href={downloadUrl(`/export/compat-rules?status=${status}`)}>
              {t("common_download_excel")}
            </a>
          </div>
        </div>
        <div className="tablewrap">
          {loading ? (
            <div className="empty">{t("common_loading")}</div>
          ) : shown.length === 0 ? (
            <div className="empty">{t("compat_none")}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("compat_th_cpu")}</th>
                  <th>{t("compat_th_mb")}</th>
                  <th>{t("compat_th_relation")}</th>
                  <th className="num">{t("compat_th_confidence")}</th>
                  <th>{t("compat_th_origin")}</th>
                  <th>{t("compat_th_status")}</th>
                  <th>{t("compat_th_confirmed_by")}</th>
                  {status === "draft" && <th>{t("compat_th_actions")}</th>}
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => (
                  <tr key={r.id}>
                    <td style={{ fontWeight: 600 }}>{r.part_a_name}</td>
                    <td style={{ fontWeight: 600 }}>{r.part_b_name}</td>
                    <td className="dim">{r.relation}</td>
                    <td className="num mono">{r.confidence.toFixed(2)}</td>
                    <td className="dim">{r.origin}</td>
                    <td>
                      <span className={`pill p-${r.status}`}>{r.status}</span>
                    </td>
                    <td className="dim">{r.confirmed_by ?? "—"}</td>
                    {status === "draft" && (
                      <td>
                        <button
                          className="btn"
                          disabled={busyId === r.id}
                          onClick={() => confirm(r.id)}
                          style={{ marginRight: 6 }}
                        >
                          {t("compat_btn_confirm")}
                        </button>
                        <button
                          className="btn danger"
                          disabled={busyId === r.id}
                          onClick={() => reject(r.id)}
                        >
                          {t("compat_btn_reject")}
                        </button>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
