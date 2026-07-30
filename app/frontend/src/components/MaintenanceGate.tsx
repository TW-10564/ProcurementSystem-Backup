import { useEffect, useRef, useState } from "react";
import { api, SERVER_ERROR_EVENT } from "../api/client";
import type { MaintenanceState } from "../api/client";
import { useLang } from "../i18n/LanguageContext";

/**
 * Two different kinds of "not right now", deliberately NOT the same screen.
 *
 * 1. MAINTENANCE (full-screen lock) — an operator deploying new code or
 *    features. The site really is unusable, a human decides when it starts
 *    and ends, and it is announced via `/maintenance`.
 *
 * 2. REBUILD (non-blocking strip) — the routine NAS re-ingest, weekly plus
 *    any manual click. It drops and repopulates the tables, so data is
 *    briefly unavailable, but the SITE is not down. Locking everyone out for
 *    ~4 minutes every rebuild overstated it, so rebuild_preserving no longer
 *    touches maintenance mode (policy 2026-07-30); this reads
 *    `/build/rebuild/status` instead, which keeps answering while the tables
 *    are gone because that state is in memory, not in the DB.
 *
 * Design notes:
 *  - polls, so a switch flipped from the terminal (or the scheduler's own
 *    weekly rebuild) reaches open tabs without anyone needing to reload
 *  - a poll FAILURE never turns either notice on: the backend being
 *    unreachable is not evidence of maintenance, and locking people out on a
 *    transient network blip would be worse than the problem being solved
 *  - but any 5xx elsewhere in the app makes it re-check IMMEDIATELY
 *    (SERVER_ERROR_EVENT). On the 20s timer alone, the start of a rebuild —
 *    which drops every table, so live requests 500 — showed users a raw
 *    error screen for up to 20s before any explanation appeared. The event
 *    only prompts a re-check; the two endpoints still decide.
 *  - the maintenance screen is dismissible. It is an honest notice, not a
 *    security control — anyone who needs to keep working (including whoever
 *    is doing the maintenance) can continue, and keeps a persistent banner.
 *  - the rebuild finishing does NOT auto-reload: someone may be mid-edit on
 *    Adjustments. It offers a reload button instead.
 */
const POLL_MS = 20000;
// Floor between event-driven re-polls. A failing page fires one event per
// failed request (the dashboard issues several in parallel), and a rebuild
// keeps them failing for minutes — without this, one broken screen would
// hammer the endpoints. 2s is still imperceptible next to the old 20s wait.
const MIN_EVENT_GAP_MS = 2000;

export default function MaintenanceGate({ children }: { children: React.ReactNode }) {
  const { t, lang } = useLang();
  const [state, setState] = useState<MaintenanceState | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [rebuilding, setRebuilding] = useState(false);
  const [justFinished, setJustFinished] = useState(false);
  const wasRebuilding = useRef(false);
  const lastPollAt = useRef(0);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      lastPollAt.current = Date.now();
      try {
        const s = await api.maintenance();
        if (!alive) return;
        setState(s);
        // a NEW maintenance window re-gates someone who dismissed the last one
        if (!s.enabled) setDismissed(false);
      } catch {
        /* unreachable backend is not maintenance — leave the app usable */
      }
      try {
        const r = await api.rebuildStatus();
        if (!alive) return;
        const running = r.status === "running";
        setRebuilding(running);
        // running -> not running is the moment the data changed underneath
        // whatever is on screen. Offer a reload; never force one.
        if (wasRebuilding.current && !running) setJustFinished(true);
        wasRebuilding.current = running;
      } catch {
        /* same reasoning: a failed probe asserts nothing */
      }
    };
    const onServerError = () => {
      if (Date.now() - lastPollAt.current < MIN_EVENT_GAP_MS) return;
      tick();
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    window.addEventListener(SERVER_ERROR_EVENT, onServerError);
    return () => {
      alive = false;
      window.clearInterval(id);
      window.removeEventListener(SERVER_ERROR_EVENT, onServerError);
    };
  }, []);

  const on = !!state?.enabled;

  // Maintenance wins: if an operator is deploying, that is the more important
  // message, and a rebuild running inside that window is incidental.
  if (on && !dismissed) {
    const since = state?.since
      ? new Date(state.since).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")
      : null;
    return (
      <div className="maint-screen">
        <div className="maint-card">
          <div className="maint-icon" aria-hidden>🔧</div>
          <h1>{t("maint_title")}</h1>
          <p>{state?.message || t("maint_default_body")}</p>
          {since && (
            <p className="maint-since">
              {t("maint_since")}: <span className="mono">{since}</span>
            </p>
          )}
          <div className="maint-actions">
            <button className="btn primary" onClick={() => window.location.reload()}>
              {t("maint_retry")}
            </button>
            <button className="maint-link" onClick={() => setDismissed(true)}>
              {t("maint_dismiss")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <>
      {on && dismissed && (
        <div className="maint-strip" role="status">
          🔧 {state?.message || t("maint_banner")}
        </div>
      )}
      {!on && rebuilding && (
        <div className="maint-strip rebuild-strip" role="status">
          ⏳ {t("rebuild_strip")}
        </div>
      )}
      {!on && !rebuilding && justFinished && (
        <div className="maint-strip rebuild-strip done" role="status">
          ✅ {t("rebuild_done_strip")}{" "}
          <button className="maint-link" onClick={() => window.location.reload()}>
            {t("rebuild_done_reload")}
          </button>
        </div>
      )}
      {children}
    </>
  );
}
