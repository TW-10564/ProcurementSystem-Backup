import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, downloadUrl } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { Adjustment } from "../api/types";

interface ItemOption {
  id: number;
  canonical_name: string;
  category: string | null;
}

export default function Adjustments() {
  const { t, lang } = useLang();
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
    author: localStorage.getItem("onefcst_author") ?? "bala",
  });
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  const load = () => api.adjustments().then(setList);
  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    if (query.length < 1) return setOptions([]);
    const timer = setTimeout(() => {
      api.searchItems(query).then(setOptions);
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
      localStorage.setItem("onefcst_author", form.author);
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

  return (
    <div>
      <div className="page-head">
        <h2>{t("adj_title")}</h2>
        <p>{t("adj_sub")}</p>
      </div>

      <div className="card">
        <div className="hd">
          <h3>{t("adj_new_title")}</h3>
        </div>
        <form onSubmit={submit}>
          <div className="formgrid">
            <div className="field span2">
              <label>{t("adj_item_label")}</label>
              <input
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("adj_item_placeholder")}
              />
              {options.length > 0 && (
                <div
                  style={{
                    border: "1px solid var(--line)",
                    borderRadius: 8,
                    marginTop: 4,
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
            <div className="field">
              <label>{t("common_author_label")}</label>
              <input
                required
                value={form.author}
                onChange={(e) => setForm({ ...form, author: e.target.value })}
              />
            </div>
          </div>
          <div style={{ padding: "0 20px 18px", display: "flex", gap: 10, alignItems: "center" }}>
            <button className="btn primary" disabled={submitting} type="submit">
              {t("adj_submit")}
            </button>
            {message && <span className="dim">{message}</span>}
          </div>
        </form>
      </div>

      <div className="card">
        <div className="hd">
          <h3>{t("adj_history_title")}</h3>
          <a className="btn" href={downloadUrl("/export/adjustments")}>
            {t("common_download_excel")}
          </a>
        </div>
        <div className="tablewrap">
          {list.length === 0 ? (
            <div className="empty">{t("common_none_recorded")}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("adj_th_item")}</th>
                  <th>{t("adj_th_month")}</th>
                  <th>{t("adj_th_scope")}</th>
                  <th className="num">{t("adj_th_old")}</th>
                  <th className="num">{t("adj_th_new")}</th>
                  <th>{t("adj_th_reason")}</th>
                  <th>{t("adj_th_author")}</th>
                  <th>{t("adj_th_when")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((a) => (
                  <tr key={a.id}>
                    <td style={{ fontWeight: 600 }}>
                      {a.item_id ? (
                        <Link to={`/forecast?item=${a.item_id}`} title={t("adj_view_in_forecast")}>
                          {a.item_name ?? `#${a.item_id}`}
                        </Link>
                      ) : (
                        a.item_name ?? "—"
                      )}
                    </td>
                    <td className="mono">{a.month}</td>
                    <td className="dim">{a.scope}</td>
                    <td className="num mono dim">{a.old_value ?? "—"}</td>
                    <td className="num mono" style={{ fontWeight: 700 }}>
                      {a.new_value}
                    </td>
                    <td>{a.reason}</td>
                    <td className="dim">{a.author}</td>
                    <td className="dim mono">
                      {new Date(a.created_at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")}
                    </td>
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
