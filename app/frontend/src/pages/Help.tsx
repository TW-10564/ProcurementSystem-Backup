import { useEffect, useState } from "react";
import { useLang } from "../i18n/LanguageContext";

/* Demo videos are plain static files under public/, not an API resource —
   the portal has to work on a NAS/localhost deployment with no external
   service and no sign-in prompt, so an embedded SharePoint/Stream player was
   deliberately not used. Drop the file in and it plays; no rebuild needed.

   Per-language file so the narration matches the selected UI language, with
   a single shared demo.mp4 as the fallback for when only one recording
   exists (the likely first state — one Japanese video for both audiences). */
const VIDEO_DIR = "/help";
/* Both containers are accepted. MP4 (H.264) is the safest default for a
   hand-recorded walkthrough; WebM is what Playwright's screen capture emits,
   and Edge/Chrome/Firefox all play it, so there's no reason to force a
   conversion step just to rename the container.
   Order matters — per-language before shared, MP4 before WebM. */
const candidatesFor = (lang: string) => [
  `${VIDEO_DIR}/demo-${lang}.mp4`,
  `${VIDEO_DIR}/demo-${lang}.webm`,
  `${VIDEO_DIR}/demo.mp4`,
  `${VIDEO_DIR}/demo.webm`,
];

/** Which of the candidate files actually exists, or null while checking /
 *  if none do. A HEAD request rather than letting <video> fail silently:
 *  a broken player with no explanation is worse than an honest empty state
 *  telling the team exactly which file to add. */
function useAvailableVideo(lang: string): { src: string | null; checking: boolean } {
  const [src, setSrc] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);

  useEffect(() => {
    let cancelled = false;
    const candidates = candidatesFor(lang);
    setChecking(true);
    setSrc(null);

    (async () => {
      for (const url of candidates) {
        try {
          const res = await fetch(url, { method: "HEAD" });
          // Vite's dev server answers unknown paths with the SPA index.html
          // (200 + text/html) rather than a 404, so checking res.ok alone
          // would "find" a video that doesn't exist. Require a video type.
          const type = res.headers.get("content-type") ?? "";
          if (res.ok && type.startsWith("video/")) {
            if (!cancelled) { setSrc(url); setChecking(false); }
            return;
          }
        } catch {
          /* network error — treat as absent, try the next candidate */
        }
      }
      if (!cancelled) { setSrc(null); setChecking(false); }
    })();

    return () => { cancelled = true; };
  }, [lang]);

  return { src, checking };
}

/** The worked example behind the WAPE formula. Deliberately round numbers —
 *  the point is that the arithmetic is checkable by eye, not realistic. */
const EXAMPLE = [
  { month: { ja: "1月", en: "Month 1" }, forecast: 300, actual: 250 },
  { month: { ja: "2月", en: "Month 2" }, forecast: 350, actual: 400 },
  { month: { ja: "3月", en: "Month 3" }, forecast: 300, actual: 350 },
];

export default function Help() {
  const { t, lang } = useLang();
  const { src, checking } = useAvailableVideo(lang);

  const totalActual = EXAMPLE.reduce((s, r) => s + r.actual, 0);
  const totalGap = EXAMPLE.reduce((s, r) => s + Math.abs(r.forecast - r.actual), 0);

  return (
    <div>
      <div className="page-head">
        <h2>{t("help_title")}</h2>
        <p>{t("help_sub")}</p>
      </div>

      {/* ── Demo video ─────────────────────────────────────────────── */}
      <div className="card">
        <div className="hd">
          <div>
            <div className="eyebrow">{t("help_video_section")}</div>
            <h3>{t("help_video_title")}</h3>
            <div className="desc">{t("help_video_desc")}</div>
          </div>
        </div>

        <div style={{ padding: "0 24px 20px" }}>
          {checking ? (
            <div className="empty">{t("common_loading")}</div>
          ) : src ? (
            /* Watch-in-place only: no download link, the browser's own
               download button removed from the control bar, and the
               right-click "save video as" menu suppressed. This is a
               deterrent, NOT protection — the file is still fetched over
               HTTP and anyone who opens devtools can save it. It keeps the
               training video living in the portal rather than circulating
               as a loose copy; it cannot enforce that. */
            <video
              key={src}
              src={src}
              controls
              controlsList="nodownload noplaybackrate"
              disablePictureInPicture
              onContextMenu={(e) => e.preventDefault()}
              preload="metadata"
              style={{
                width: "100%", maxHeight: "70vh", borderRadius: 12,
                background: "#000", display: "block",
              }}
            >
              {t("help_video_unsupported")}
            </video>
          ) : (
            <div className="empty" style={{ textAlign: "left", padding: "22px 20px" }}>
              <div style={{ fontWeight: 700, marginBottom: 6 }}>
                {t("help_video_missing_title")}
              </div>
              <div className="dim" style={{ fontSize: "0.85rem", marginBottom: 10 }}>
                {t("help_video_missing_body")}
              </div>
              <pre
                className="mono"
                style={{
                  fontSize: "0.78rem", background: "var(--brand-bg-soft)",
                  padding: "10px 14px", borderRadius: 8, overflowX: "auto", margin: 0,
                }}
              >
{`app/frontend/public/help/demo-ja.mp4   ← 日本語
app/frontend/public/help/demo-en.mp4   ← English
app/frontend/public/help/demo.mp4      ← ${lang === "ja"
  ? "どちらか一方だけの場合はこちら"
  : "fallback, used when only one recording exists"}

${lang === "ja"
  ? ".webm 形式も同じ名前で利用できます（demo.webm など）"
  : ".webm works too, same names (demo.webm, demo-ja.webm …)"}`}
              </pre>
              <div className="dim" style={{ fontSize: "0.78rem", marginTop: 10 }}>
                {t("help_video_missing_hint")}
              </div>
            </div>
          )}
        </div>
      </div>

      {/* ── WAPE explainer ─────────────────────────────────────────── */}
      <div className="card" style={{ marginTop: 18 }}>
        <div className="hd">
          <div>
            <div className="eyebrow">{t("help_wape_section")}</div>
            <h3>{t("help_wape_title")}</h3>
          </div>
        </div>

        <div style={{ padding: "0 24px 20px" }}>
          <p style={{ margin: "0 0 16px", lineHeight: 1.7 }}>{t("help_wape_plain")}</p>

          {/* Formula shown as words first, symbols second — the audience is
              buyers and planners, not statisticians. */}
          <div className="eyebrow" style={{ marginBottom: 6 }}>
            {t("help_wape_formula_label")}
          </div>
          <div
            style={{
              background: "var(--brand-bg-soft)", borderRadius: 10,
              padding: "16px 18px", marginBottom: 8,
            }}
          >
            {/* A real stacked fraction: numerator and denominator in a
                column with a rule between them, the whole block centred on
                the "=" and "× 100" via align-items:center. Laying this out
                inline (numerator, then a bordered span) reads as two
                separate terms rather than one division. */}
            <div
              className="mono"
              style={{
                display: "flex", alignItems: "center", justifyContent: "center",
                gap: 10, flexWrap: "wrap",
                fontSize: "0.95rem", fontWeight: 700,
              }}
            >
              <span>WAPE&nbsp;=</span>
              <span
                style={{
                  display: "inline-flex", flexDirection: "column",
                  alignItems: "center", lineHeight: 1.5,
                }}
              >
                <span style={{ padding: "0 12px 3px" }}>
                  {lang === "ja" ? "Σ | 予測 − 実績 |" : "Σ | forecast − actual |"}
                </span>
                <span
                  style={{
                    borderTop: "2px solid currentColor",
                    alignSelf: "stretch", padding: "3px 12px 0",
                    textAlign: "center",
                  }}
                >
                  {lang === "ja" ? "Σ 実績" : "Σ actual"}
                </span>
              </span>
              <span>× 100</span>
            </div>
          </div>
          <p className="dim" style={{ margin: "0 0 20px", fontSize: "0.86rem", lineHeight: 1.7 }}>
            {t("help_wape_formula_words")}
          </p>

          <div className="eyebrow" style={{ marginBottom: 6 }}>
            {t("help_wape_example_label")}
          </div>
          <p style={{ margin: "0 0 10px", fontSize: "0.88rem" }}>
            {t("help_wape_example_intro")}
          </p>
          <div className="tablewrap" style={{ maxWidth: 520, marginBottom: 12 }}>
            <table>
              <thead>
                <tr>
                  <th>{t("help_wape_th_month")}</th>
                  <th className="num">{t("help_wape_th_forecast")}</th>
                  <th className="num">{t("help_wape_th_actual")}</th>
                  <th className="num">{t("help_wape_th_gap")}</th>
                </tr>
              </thead>
              <tbody>
                {EXAMPLE.map((r) => (
                  <tr key={r.month.en}>
                    <td>{lang === "ja" ? r.month.ja : r.month.en}</td>
                    <td className="num mono">{r.forecast.toLocaleString()}</td>
                    <td className="num mono">{r.actual.toLocaleString()}</td>
                    <td className="num mono">
                      {Math.abs(r.forecast - r.actual).toLocaleString()}
                    </td>
                  </tr>
                ))}
                <tr style={{ fontWeight: 700 }}>
                  <td>{t("help_wape_th_total")}</td>
                  <td className="num mono dim">—</td>
                  <td className="num mono">{totalActual.toLocaleString()}</td>
                  <td className="num mono">{totalGap.toLocaleString()}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <p style={{ margin: "0 0 8px", fontWeight: 600 }}>{t("help_wape_example_calc")}</p>
          <p className="dim" style={{ margin: "0 0 22px", fontSize: "0.86rem", lineHeight: 1.7 }}>
            {t("help_wape_why_abs")}
          </p>

          {/* Why the detail panel shows two different WAPEs — the single
              most common question this page needs to pre-empt. */}
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            {t("help_wape_two_numbers_title")}
          </div>
          <ul style={{ margin: "0 0 10px", paddingLeft: 20, lineHeight: 1.8 }}>
            <li style={{ marginBottom: 8 }}>{t("help_wape_near_term")}</li>
            <li>{t("help_wape_full_horizon")}</li>
          </ul>
          <p className="dim" style={{ margin: "0 0 22px", fontSize: "0.86rem", lineHeight: 1.7 }}>
            {t("help_wape_gap_note")}
          </p>

          {/* Bands mirror confidenceTier() on the Forecast page — same
              thresholds and the same dot colours the table uses, so the
              legend there and this explanation can't drift apart visually. */}
          <div className="eyebrow" style={{ marginBottom: 8 }}>
            {t("help_wape_bands_title")}
          </div>
          <div style={{ display: "grid", gap: 8 }}>
            {[
              { color: "var(--green)", head: "help_wape_band_good", body: "help_wape_band_good_body" },
              { color: "var(--amber)", head: "help_wape_band_rough", body: "help_wape_band_rough_body" },
              { color: "var(--red)", head: "help_wape_band_poor", body: "help_wape_band_poor_body" },
              { color: "var(--muted)", head: "help_wape_band_unknown", body: "help_wape_band_unknown_body" },
            ].map((b) => (
              <div
                key={b.head}
                style={{
                  display: "flex", gap: 12, alignItems: "flex-start",
                  padding: "10px 14px", borderRadius: 10,
                  background: "var(--brand-bg-soft)",
                }}
              >
                <span
                  style={{
                    width: 10, height: 10, borderRadius: "50%", background: b.color,
                    flex: "0 0 auto", marginTop: 6,
                  }}
                />
                <div>
                  <div style={{ fontWeight: 700, fontSize: "0.88rem" }}>
                    {t(b.head as Parameters<typeof t>[0])}
                  </div>
                  <div className="dim" style={{ fontSize: "0.83rem", lineHeight: 1.6 }}>
                    {t(b.body as Parameters<typeof t>[0])}
                  </div>
                </div>
              </div>
            ))}
          </div>

          <div className="banner" style={{ marginTop: 20 }}>
            {t("help_wape_caveat")}
          </div>
        </div>
      </div>
    </div>
  );
}
