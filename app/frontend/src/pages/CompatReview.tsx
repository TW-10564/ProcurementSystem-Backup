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

const STATUSES = ["draft", "confirmed", "rejected"] as const;
type Status = (typeof STATUSES)[number];

function confTone(c: number): "green" | "amber" | "red" {
  return c >= 0.9 ? "green" : c >= 0.7 ? "amber" : "red";
}

export default function CompatReview() {
  const { t, lang } = useLang();
  const [rules, setRules] = useState<CompatRule[]>([]);
  const [status, setStatus] = useState<string>("draft");
  const [minConfidence, setMinConfidence] = useState(0.9);
  const [loading, setLoading] = useState(false);
  const [author, setAuthor] = useAuthor();
  const [busyId, setBusyId] = useState<number | null>(null);
  const [llmBusy, setLlmBusy] = useState(false);
  const [llmMsg, setLlmMsg] = useState<string | null>(null);
  const [counts, setCounts] = useState<Record<Status, number>>({
    draft: 0,
    confirmed: 0,
    rejected: 0,
  });

  const load = () => {
    setLoading(true);
    api
      .compatRules({ status: status || undefined, min_confidence: undefined })
      .then(setRules)
      .finally(() => setLoading(false));
    // counts across all statuses for the summary strip / segmented control
    api.compatRules({}).then((all) => {
      const c: Record<Status, number> = { draft: 0, confirmed: 0, rejected: 0 };
      for (const r of all) c[r.status] = (c[r.status] ?? 0) + 1;
      setCounts(c);
    });
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

  const draftLlm = async () => {
    setLlmBusy(true);
    setLlmMsg(null);
    try {
      const res = await api.draftLlmRules();
      if (!res.ok) {
        setLlmMsg(
          lang === "ja"
            ? `LLMサーバーに接続できません: ${res.error}`
            : `Cannot reach the LLM server: ${res.error}`,
        );
      } else {
        setLlmMsg(
          lang === "ja"
            ? `未分類 CPU ${res.cpu_unclassified}件・MB ${res.mb_unclassified}件のうち ${res.classified}件を分類し、ルール ${res.rules_drafted}件を下書きしました（要レビュー ${res.needs_review}件、既存スキップ ${res.skipped_existing}件）`
            : `Classified ${res.classified} of ${res.cpu_unclassified} CPU / ${res.mb_unclassified} MB unclassified parts; drafted ${res.rules_drafted} rules (${res.needs_review} still need review, ${res.skipped_existing} skipped as existing)`,
        );
        load();
      }
    } catch (e: unknown) {
      setLlmMsg(String((e as Error).message ?? e));
    } finally {
      setLlmBusy(false);
    }
  };

  const shown = rules.filter((r) => r.confidence >= minConfidence || status !== "draft");

  const statusLabel: Record<Status, string> = {
    draft: t("compat_status_draft"),
    confirmed: t("compat_status_confirmed"),
    rejected: t("compat_status_rejected"),
  };

  const total = counts.draft + counts.confirmed + counts.rejected;

  return (
    <div>
      <div className="page-head">
        <h2>{t("compat_title")}</h2>
        <p>
          {t("compat_sub_pre")} <b>{t("compat_sub_bold")}</b> {t("compat_sub_post")}
        </p>
      </div>

      <div className="banner info">ℹ️ {t("compat_banner")}</div>

      {/* ── Summary strip: queue state at a glance ─────────────────── */}
      <div className="kpis" style={{ margin: "14px 0" }}>
        <div className="kpi accent-amber">
          <div className="lab">{statusLabel.draft}</div>
          <div className="val">{counts.draft}</div>
          <div className="sub">
            {lang === "ja" ? "レビュー待ちルール" : "rules awaiting review"}
          </div>
        </div>
        <div className="kpi accent-green">
          <div className="lab">{statusLabel.confirmed}</div>
          <div className="val">{counts.confirmed}</div>
          <div className="sub">
            {lang === "ja" ? "承認済みルール" : "approved rules"}
          </div>
        </div>
        <div className="kpi accent-red">
          <div className="lab">{statusLabel.rejected}</div>
          <div className="val">{counts.rejected}</div>
          <div className="sub">
            {total > 0
              ? lang === "ja"
                ? `全 ${total} 件のルール候補`
                : `of ${total} candidate rules`
              : lang === "ja"
                ? "却下されたルール"
                : "rejected rules"}
          </div>
        </div>
      </div>

      {/* ── Review queue ───────────────────────────────────────────── */}
      <div className="card">
        <div className="hd" style={{ flexWrap: "wrap", gap: 12 }}>
          <div>
            <div className="eyebrow">
              {lang === "ja" ? "レビューキュー" : "Review queue"}
            </div>
            <h3 style={{ margin: "2px 0 0" }}>
              {statusLabel[(status as Status) in counts ? (status as Status) : "draft"]}
              <span className="dim" style={{ fontWeight: 400, marginLeft: 8, fontSize: "0.8rem" }}>
                {shown.length}
                {lang === "ja" ? " 件表示" : " shown"}
              </span>
            </h3>
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            {status === "draft" && (
              <>
                <button className="btn" onClick={draftLlm} disabled={llmBusy}>
                  {llmBusy
                    ? lang === "ja"
                      ? "AI分類中…"
                      : "AI classifying…"
                    : lang === "ja"
                      ? "AI下書き生成（未分類部材）"
                      : "AI-draft unclassified parts"}
                </button>
                <button className="btn primary" onClick={bulkConfirm}>
                  {t("compat_bulk_confirm")}
                </button>
              </>
            )}
            <a className="btn" href={downloadUrl(`/export/compat-rules?status=${status}`)}>
              {t("common_download_excel")}
            </a>
          </div>
        </div>

        {llmMsg && (
          <div className="banner info" style={{ margin: "12px 24px 0" }}>
            {llmMsg}
          </div>
        )}

        <div className="toolbar" style={{ padding: "10px 24px", borderBottom: "1px solid var(--line2)", display: "flex", flexWrap: "wrap", gap: 16, alignItems: "center" }}>
          <div className="seg">
            {STATUSES.map((s) => (
              <button
                key={s}
                className={status === s ? "on" : ""}
                onClick={() => setStatus(s)}
              >
                {statusLabel[s]}{" "}
                <span className="mono" style={{ opacity: 0.7 }}>
                  {counts[s]}
                </span>
              </button>
            ))}
          </div>

          <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <span className="eyebrow">{t("compat_confidence_label")}</span>
            {(
              [
                [0, t("compat_confidence_all")],
                [0.9, t("compat_confidence_high")],
                [0.7, t("compat_confidence_medium")],
              ] as const
            ).map(([v, label]) => (
              <button
                key={v}
                className={`chip ${minConfidence === v ? "on" : ""}`}
                onClick={() => setMinConfidence(v)}
              >
                {label}
              </button>
            ))}
          </div>

          <div style={{ display: "flex", gap: 8, alignItems: "center", marginLeft: "auto" }}>
            <span className="avatar" title={t("common_author_label")}>
              {author ? author.slice(0, 2).toUpperCase() : "？"}
            </span>
            <input
              value={author}
              onChange={(e) => setAuthor(e.target.value)}
              placeholder={t("compat_author_placeholder")}
              aria-label={t("common_author_label")}
              style={{ width: 180 }}
            />
          </div>
        </div>

        {loading ? (
          <div className="empty">{t("common_loading")}</div>
        ) : shown.length === 0 ? (
          <div className="empty">{t("compat_none")}</div>
        ) : (
          <div>
            {shown.map((r) => {
              const tone = confTone(r.confidence);
              return (
                <div className="list-row" key={r.id}>
                  <div className={`icon-tile ${tone}`}>🔗</div>
                  <div className="grow">
                    <div className="ttl">
                      {r.part_a_name}
                      <span className="dim" style={{ fontWeight: 400, margin: "0 6px" }}>
                        ×
                      </span>
                      {r.part_b_name}
                    </div>
                    <div className="meta">
                      {r.relation}
                      <span style={{ margin: "0 6px" }}>·</span>
                      {t("compat_th_origin")}: {r.origin}
                      {r.confirmed_by && (
                        <>
                          <span style={{ margin: "0 6px" }}>·</span>
                          {t("compat_th_confirmed_by")}: {r.confirmed_by}
                        </>
                      )}
                      {r.note && (
                        <>
                          <span style={{ margin: "0 6px" }}>·</span>
                          <span title={r.note} style={{ fontStyle: "italic" }}>
                            {r.note.length > 90 ? r.note.slice(0, 90) + "…" : r.note}
                          </span>
                        </>
                      )}
                    </div>
                  </div>

                  <div style={{ textAlign: "right", flexShrink: 0, width: 110 }}>
                    <div className="eyebrow">{t("compat_th_confidence")}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 7, justifyContent: "flex-end", marginTop: 3 }}>
                      <span
                        className="minibar dim-track"
                        style={{ width: 56, position: "relative" }}
                      >
                        <span
                          className="minibar"
                          style={{
                            position: "absolute",
                            left: 0,
                            top: 0,
                            width: `${Math.round(r.confidence * 100)}%`,
                          }}
                        />
                      </span>
                      <span className="mono" style={{ fontSize: "0.78rem", fontWeight: 700 }}>
                        {r.confidence.toFixed(2)}
                      </span>
                    </div>
                  </div>

                  <span className={`pill p-${r.status}`} style={{ flexShrink: 0 }}>
                    {r.status}
                  </span>

                  {status === "draft" && (
                    <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                      <button
                        className="btn primary"
                        disabled={busyId === r.id}
                        onClick={() => confirm(r.id)}
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
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
