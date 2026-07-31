import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import AuthorName, { useAuthorInitials } from "../components/AuthorName";
import { useIdentity, useMyName } from "../auth/IdentityContext";
import { ssoEnabled } from "../auth/msal";
import { api, downloadReport } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { Adjustment } from "../api/types";
import { currentAuthor } from "../auth/author";

interface ItemOption {
  id: number;
  canonical_name: string;
  category: string | null;
}

function initials(name: string): string {
  const s = name.trim();
  if (!s) return "?";
  const parts = s.split(/\s+/);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

export default function Adjustments() {
  const { t, lang } = useLang();
  // Initials from the resolved directory name, not the mail alias.
  const authorInitials = useAuthorInitials();
  const myName = useMyName();
  const { me } = useIdentity();
  const myDept = me?.profile?.department ?? "";
  const [list, setList] = useState<Adjustment[]>([]);
  const [query, setQuery] = useState("");
  const [options, setOptions] = useState<ItemOption[]>([]);
  const [form, setForm] = useState({
    item_id: 0,
    month: new Date().toISOString().slice(0, 7) + "-01",
    old_value: "",
    new_value: "",
    scope: "sku" as "global" | "category" | "sku",
    reason: "",
    author: currentAuthor(),
  });
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () =>
    api
      .adjustments()
      .then((rows) => {
        setList(rows);
        setError(null);
      })
      .catch((e) => setError(String(e.message ?? e)));
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (query.length < 1) return setOptions([]);
    const timer = setTimeout(() => {
      api.searchItems(query).then(setOptions).catch((e) => setError(String(e.message ?? e)));
    }, 200);
    return () => clearTimeout(timer);
  }, [query]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.item_id) return setMessage(t("adj_missing_item"));
    if (!form.reason.trim()) return setMessage(t("adj_missing_reason"));
    setSubmitting(true);
    setMessage(null);
    try {
      await api.createAdjustment({
        item_id: form.item_id,
        month: form.month,
        old_value: form.old_value ? Number(form.old_value) : undefined,
        new_value: Number(form.new_value),
        scope: form.scope,
        reason: form.reason,
        author: form.author,
      });
      setMessage(t("adj_saved"));
      if (!ssoEnabled) localStorage.setItem("onefcst_author", form.author);
      setForm({ ...form, reason: "", new_value: "", old_value: "" });
      load();
    } catch (err: unknown) {
      const msg =
        err && typeof err === "object" && "response" in err
          ? JSON.stringify((err as { response?: { data?: unknown } }).response?.data)
          : String(err);
      setMessage(t("adj_error", { msg }));
    } finally {
      setSubmitting(false);
    }
  };

  const authorCount = new Set(list.map((a) => a.author)).size;
  const scopeLabel = (s: Adjustment["scope"]) =>
    s === "sku" ? t("adj_scope_sku") : s === "category" ? t("adj_scope_category") : t("adj_scope_global");

  return (
    <div>
      <div className="page-head">
        <h2>{t("adj_title")}</h2>
        <p>{t("adj_sub")}</p>
      </div>

      {error && <div className="banner warn">⚠️ {error}</div>}

      <div className="stack-full">
        {/* ── new-adjustment form — full width, so long values (item
             search, reason text) are never squeezed into a half column ── */}
        <div className="card stripe-card brand">
          <div className="hd">
            <div>
              <div className="eyebrow">
                {lang === "ja" ? "手動上書き" : "Manual override"}
              </div>
              <h3>{t("adj_new_title")}</h3>
            </div>
          </div>
          <form onSubmit={submit}>
            <div className="formgrid">
              <div className="field span2">
                <label>{t("adj_item_label")}</label>
                <div style={{ position: "relative" }}>
                  <input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    placeholder={t("adj_item_placeholder")}
                    style={{ width: "100%" }}
                  />
                  {options.length > 0 && (
                    <div
                      style={{
                        position: "absolute",
                        top: "calc(100% + 4px)",
                        left: 0,
                        right: 0,
                        zIndex: 20,
                        background: "var(--card)",
                        border: "1px solid var(--line)",
                        borderRadius: 8,
                        boxShadow: "var(--shadow-lift)",
                        maxHeight: 160,
                        overflow: "auto",
                      }}
                    >
                      {options.map((o) => (
                        <div
                          key={o.id}
                          onClick={() => {
                            setForm({ ...form, item_id: o.id });
                            setQuery(o.canonical_name);
                            setOptions([]);
                          }}
                          style={{
                            padding: "6px 10px",
                            cursor: "pointer",
                            fontSize: "0.84rem",
                            background: form.item_id === o.id ? "var(--indigo-bg)" : undefined,
                          }}
                        >
                          {o.canonical_name} <span className="dim">({o.category})</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
                {form.item_id > 0 && (
                  <div className="dim" style={{ fontSize: "0.76rem" }}>
                    {t("adj_selected_item", { id: form.item_id })}
                  </div>
                )}
              </div>
              <div className="field">
                <label>{t("adj_month_label")}</label>
                <input
                  type="month"
                  value={form.month.slice(0, 7)}
                  onChange={(e) => setForm({ ...form, month: e.target.value + "-01" })}
                />
              </div>
              <div className="field">
                <label>{t("adj_scope_label")}</label>
                <select
                  value={form.scope}
                  onChange={(e) => setForm({ ...form, scope: e.target.value as typeof form.scope })}
                >
                  <option value="sku">{t("adj_scope_sku")}</option>
                  <option value="category">{t("adj_scope_category")}</option>
                  <option value="global">{t("adj_scope_global")}</option>
                </select>
              </div>
              <div className="field">
                <label>{t("adj_old_value")}</label>
                <input
                  type="number"
                  value={form.old_value}
                  onChange={(e) => setForm({ ...form, old_value: e.target.value })}
                />
              </div>
              <div className="field">
                <label>{t("adj_new_value")}</label>
                <input
                  type="number"
                  required
                  value={form.new_value}
                  onChange={(e) => setForm({ ...form, new_value: e.target.value })}
                />
              </div>
              <div className="field span2">
                <label>{t("adj_reason_label")}</label>
                <textarea
                  required
                  rows={2}
                  value={form.reason}
                  onChange={(e) => setForm({ ...form, reason: e.target.value })}
                  placeholder={t("adj_reason_placeholder")}
                />
              </div>
              {/* Under SSO the author is NOT a form field. The server takes it
                  from the verified token and discards whatever we send
                  (api/auth.py:effective_author), so an editable box would be
                  actively misleading: you could type a colleague's name and
                  the record would still say you. Shown read-only instead, as
                  the confirmation of whose name is going on the record — the
                  point of the whole control on a shared PC.
                  Still editable pre-SSO, where the server has nothing better
                  to go on than what we send. */}
              <div className="field span2">
                <label>{t("common_author_label")}</label>
                <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
                  <span className="avatar">
                    {ssoEnabled ? authorInitials(form.author) : initials(form.author)}
                  </span>
                  {ssoEnabled ? (
                    <div className="author-fixed">
                      <span className="author-fixed-name">
                        {myName || form.author}
                      </span>
                      {myDept && <span className="author-fixed-sub">{myDept}</span>}
                    </div>
                  ) : (
                    <input
                      required
                      style={{ flex: 1 }}
                      value={form.author}
                      onChange={(e) => setForm({ ...form, author: e.target.value })}
                    />
                  )}
                </div>
              </div>
            </div>
            <div style={{ padding: "0 24px 18px" }}>
              <button
                className="btn primary"
                disabled={submitting}
                type="submit"
                style={{ width: "100%", justifyContent: "center" }}
              >
                {t("adj_submit")}
              </button>
              {message && (
                <div className="banner info" style={{ marginTop: 12, marginBottom: 0 }}>
                  {message}
                </div>
              )}
            </div>
          </form>
        </div>

        {/* ── right: audit trail ────────────────────────────────────── */}
        <div className="card">
          <div className="hd">
            <div>
              <div className="eyebrow">{lang === "ja" ? "監査証跡" : "Audit trail"}</div>
              <h3>{t("adj_history_title")}</h3>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 26, flexWrap: "wrap" }}>
              <div className="statline">
                <span className="n">{list.length}</span>
                <span className="c">{lang === "ja" ? "調整件数" : "adjustments"}</span>
              </div>
              <div className="statline">
                <span className="n">{authorCount}</span>
                <span className="c">{lang === "ja" ? "担当者" : "authors"}</span>
              </div>
              <button className="btn" onClick={() => downloadReport("/export/adjustments", "adjustments.xlsx")}>
                {t("common_download_excel")}
              </button>
            </div>
          </div>
          {list.length === 0 ? (
            <div className="empty">{error ? t("common_load_failed") : t("common_none_recorded")}</div>
          ) : (
            <div>
              {list.map((a) => {
                const dir =
                  a.old_value == null || a.new_value === a.old_value
                    ? "flat"
                    : a.new_value > a.old_value
                      ? "good"
                      : "bad";
                const pct =
                  a.old_value != null && a.old_value !== 0
                    ? Math.round(((a.new_value - a.old_value) / a.old_value) * 100)
                    : null;
                return (
                  <div className="list-row" key={a.id}>
                    <span className="avatar" title={a.author}>
                      {authorInitials(a.author)}
                    </span>
                    <div className="grow">
                      <div className="ttl">
                        {a.item_id ? (
                          <Link to={`/app/forecast?item=${a.item_id}`} title={t("adj_view_in_forecast")}>
                            {a.item_name ?? `#${a.item_id}`}
                          </Link>
                        ) : (
                          (a.item_name ?? "—")
                        )}{" "}
                        <span className="mono dim" style={{ textDecoration: "line-through", fontWeight: 500 }}>
                          {a.old_value ?? "—"}
                        </span>{" "}
                        <span className="dim" style={{ fontWeight: 400 }}>
                          →
                        </span>{" "}
                        <span className="mono" style={{ fontWeight: 800 }}>
                          {a.new_value}
                        </span>{" "}
                        <span className={`delta ${dir}`}>
                          {dir === "good" ? "▲" : dir === "bad" ? "▼" : "—"}
                          {pct != null ? ` ${pct > 0 ? "+" : ""}${pct}%` : ""}
                        </span>
                      </div>
                      <div className="meta">
                        {a.reason}
                        <span className="dim">
                          {" "}
                          · <AuthorName author={a.author} /> · {scopeLabel(a.scope)}
                        </span>
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: 0 }}>
                      <div className="pill p-draft" title={t("adj_th_month")}>
                        {a.month.slice(0, 7)}
                      </div>
                      <div className="dim mono" style={{ fontSize: "0.71rem", marginTop: 4 }}>
                        {new Date(a.created_at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
