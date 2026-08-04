import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import AuthorName from "../components/AuthorName";
import type { Notification } from "../api/types";

/* ---- event classification (keyword heuristic on `what`) ---- */
type Kind = "adjust" | "confirm" | "warn" | "system";

function classify(what: string): Kind {
  const s = what.toLowerCase();
  if (/(reject|delete|fail|error|却下|削除|失敗|エラー|警告)/.test(s)) return "warn";
  if (/(confirm|approve|accept|承認|確定|完了)/.test(s)) return "confirm";
  if (/(adjust|override|edit|update|調整|上書|修正|変更)/.test(s)) return "adjust";
  return "system";
}

const KIND_TILE: Record<Kind, { cls: string; glyph: string }> = {
  adjust: { cls: "icon-tile amber", glyph: "✎" },
  confirm: { cls: "icon-tile green", glyph: "✓" },
  warn: { cls: "icon-tile red", glyph: "!" },
  system: { cls: "icon-tile", glyph: "◆" },
};

export default function Notifications() {
  const { t, lang } = useLang();
  const [list, setList] = useState<Notification[]>([]);
  const [summary, setSummary] = useState<{
    total_events: number;
    by_author: Record<string, number>;
  } | null>(null);
  const [author, setAuthor] = useState<string | null>(null);
  const [kind, setKind] = useState<Kind | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [total, setTotal] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);

  const PAGE = 50;

  /* Refetch whenever a filter changes, from offset 0. The server does the
     filtering AND the counting, so a narrowed view is complete rather than
     "whatever matched inside the newest 200 rows". */
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .notifications({ limit: PAGE, offset: 0, who: author, kind, q: query || null, lang })
      .then((page) => {
        if (cancelled) return;
        const items = Array.isArray(page?.items) ? page.items : [];
        setList(items);
        setTotal(page?.total ?? items.length);
        setHasMore(Boolean(page?.has_more));
      })
      .catch((e: unknown) => setError(String((e as Error).message ?? e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [author, kind, query, lang]);

  useEffect(() => {
    api
      .notificationSummary()
      .then(setSummary)
      .catch((e: unknown) => setError(String((e as Error).message ?? e)));
  }, []);

  const loadMore = () => {
    setLoading(true);
    api
      .notifications({ limit: PAGE, offset: list.length, who: author, kind, q: query || null, lang })
      .then((page) => {
        // Append, and de-duplicate by id: rows written in the same second
        // share a timestamp, and although the server tie-breaks on id, a row
        // inserted between two page fetches would otherwise appear twice.
        const items = Array.isArray(page?.items) ? page.items : [];
        setList((prev) => {
          const seen = new Set(prev.map((n) => n.id));
          return [...prev, ...items.filter((n) => !seen.has(n.id))];
        });
        setTotal(page?.total ?? 0);
        setHasMore(Boolean(page?.has_more));
      })
      .catch((e: unknown) => setError(String((e as Error).message ?? e)))
      .finally(() => setLoading(false));
  };

  const ja = lang === "ja";

  /* ---- day-key helpers ---- */
  const dayKey = (d: Date) =>
    `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

  const now = new Date();
  const todayKey = dayKey(now);
  const yesterdayKey = dayKey(new Date(now.getTime() - 86400000));

  const dayLabel = (key: string, sample: Date) => {
    if (key === todayKey) return ja ? "今日" : "Today";
    if (key === yesterdayKey) return ja ? "昨日" : "Yesterday";
    return sample.toLocaleDateString(ja ? "ja-JP" : "en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
      weekday: "short",
    });
  };

  const timeOf = (d: Date) =>
    d.toLocaleTimeString(ja ? "ja-JP" : "en-US", { hour: "2-digit", minute: "2-digit" });

  /* ---- distinct authors for chip filter ---- */
  const authors = useMemo(() => {
    // From the all-time summary rather than the loaded page: chips built from
    // the page would change every time you filter or load more, and would
    // omit anyone with no activity in the newest rows.
    const m = new Map<string, number>(Object.entries(summary?.by_author ?? {}));
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [summary]);

  /* ---- group by day ----
     No filtering here any more: the server has already applied author, kind
     and search. Re-filtering locally would silently drop rows the server
     deliberately returned, and would disagree with the `total` shown above. */
  const groups = useMemo(() => {
    const sorted = [...(Array.isArray(list) ? list : [])].sort(
      (a, b) => new Date(b.at).getTime() - new Date(a.at).getTime()
    );
    const out: { key: string; label: string; items: Notification[] }[] = [];
    for (const n of sorted) {
      const d = new Date(n.at);
      const key = dayKey(d);
      const last = out[out.length - 1];
      if (last && last.key === key) last.items.push(n);
      else out.push({ key, label: dayLabel(key, d), items: [n] });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [list, lang]);

  const kindLabel: Record<Kind, string> = {
    adjust: ja ? "調整" : "Adjustments",
    confirm: ja ? "承認・確定" : "Approvals & confirmations",
    warn: ja ? "警告・却下" : "Warnings & rejections",
    system: ja ? "システム" : "System",
  };

  const shown = groups.reduce((s, g) => s + g.items.length, 0);
  void shown;   // header now reports the server-side total instead

  return (
    <div>
      <div className="page-head">
        <h2>{t("notif_title")}</h2>
        <p>{t("notif_sub")}</p>
      </div>

      {error && <div className="banner warn">⚠️ {error}</div>}

      {summary && (
        <div className="kpis">
          <div className="kpi accent-brand">
            <div className="lab">{t("notif_total_events")}</div>
            <div className="val">{summary.total_events.toLocaleString()}</div>
            <div className="sub">
              {ja ? `直近 ${list.length} 件を表示中` : `showing latest ${list.length}`}
            </div>
          </div>
          {Object.entries(summary.by_author)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3)
            .map(([who, n]) => (
              <div className="kpi" key={who}>
                <div className="lab">{who}</div>
                <div className="val">{n}</div>
                <div className="sub">{t("notif_action_count")}</div>
              </div>
            ))}
        </div>
      )}

      <div className="card">
        <div className="hd">
          <h3>{t("notif_all_activity")}</h3>
          <span className="dim" style={{ fontSize: "0.78rem" }}>
            {list.length < total
              ? ja
                ? `${total.toLocaleString()}件中 ${list.length}件を表示`
                : `showing ${list.length} of ${total.toLocaleString()}`
              : ja
                ? `${total.toLocaleString()} 件`
                : `${total.toLocaleString()} events`}
          </span>
        </div>

        {/* filters: author + type */}
        <div className="toolbar" style={{ padding: "10px 20px", flexWrap: "wrap" }}>
          <button className={`chip${author === null ? " on" : ""}`} onClick={() => setAuthor(null)}>
            {ja ? "全員" : "All users"}
          </button>
          {authors.slice(0, 6).map(([who, n]) => (
            <button
              key={who}
              className={`chip${author === who ? " on" : ""}`}
              onClick={() => setAuthor(author === who ? null : who)}
            >
              <AuthorName author={who} /> <span className="dim">{n}</span>
            </button>
          ))}
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={ja ? "内容を検索…" : "Search…"}
            aria-label={ja ? "内容を検索" : "Search events"}
            style={{ minWidth: 180 }}
          />
          <span style={{ flex: 1 }} />
          {(["adjust", "confirm", "warn", "system"] as Kind[]).map((k) => (
            <button
              key={k}
              className={`chip${kind === k ? " on" : ""}`}
              onClick={() => setKind(kind === k ? null : k)}
            >
              {kindLabel[k]}
            </button>
          ))}
        </div>

        {groups.length === 0 && !loading ? (
          <div className="empty">{t("notif_none")}</div>
        ) : (
          groups.map((g) => (
            <div key={g.key}>
              <div className="eyebrow" style={{ padding: "14px 20px 4px" }}>
                {g.label}
                <span className="dim" style={{ marginLeft: 8, fontWeight: 500 }}>
                  {ja ? `${g.items.length} 件` : `${g.items.length} events`}
                </span>
              </div>
              {g.items.map((n) => {
                const k = classify(n.what);
                const tile = KIND_TILE[k];
                return (
                  <div className="list-row" key={n.id}>
                    <div className={tile.cls} aria-hidden>
                      {tile.glyph}
                    </div>
                    <div className="grow">
                      <div className="ttl">{n.what}</div>
                      <div className="meta">
                        <span style={{ fontWeight: 600 }}><AuthorName author={n.who} /></span>
                        <span className="dim"> · {kindLabel[k]}</span>
                      </div>
                    </div>
                    <div
                      className="mono dim"
                      style={{ fontVariantNumeric: "tabular-nums", fontSize: "0.76rem", whiteSpace: "nowrap" }}
                      title={new Date(n.at).toLocaleString(ja ? "ja-JP" : "en-US")}
                    >
                      {timeOf(new Date(n.at))}
                    </div>
                  </div>
                );
              })}
            </div>
          ))
        )}

        {/* Paging is a deliberate button, not infinite scroll: an audit list
            people read to answer "who changed this" should not move under
            them while they read. */}
        {hasMore && (
          <div style={{ padding: "14px 20px", textAlign: "center" }}>
            <button className="btn" onClick={loadMore} disabled={loading}>
              {loading
                ? t("common_loading")
                : ja
                  ? `さらに表示（残り ${(total - list.length).toLocaleString()} 件）`
                  : `Load more (${(total - list.length).toLocaleString()} remaining)`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
