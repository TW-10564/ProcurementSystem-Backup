import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { MaintenanceState } from "../api/client";
import { useLang } from "../i18n/LanguageContext";

/**
 * Site-wide "we're working on this" screen.
 *
 * The portal is now reachable by everyone on the office network, so work in
 * progress — a half-deployed frontend, a rebuild holding the DB lock, a
 * backend restart — is visible to colleagues who reasonably read it as "the
 * system is broken". This shows one clear explanation instead.
 *
 * Design notes:
 *  - polls, so a switch flipped from the terminal reaches open tabs without
 *    anyone needing to reload
 *  - a poll FAILURE never turns the gate on: the backend being unreachable
 *    is not evidence of maintenance, and locking people out on a transient
 *    network blip would be worse than the problem being solved
 *  - dismissible. This is an honest notice, not a security control — anyone
 *    who needs to keep working (including whoever is doing the maintenance)
 *    can continue, and keeps a persistent banner as a reminder.
 */
const POLL_MS = 20000;

export default function MaintenanceGate({ children }: { children: React.ReactNode }) {
  const { t, lang } = useLang();
  const [state, setState] = useState<MaintenanceState | null>(null);
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.maintenance();
        if (!alive) return;
        setState(s);
        // a NEW maintenance window re-gates someone who dismissed the last one
        if (!s.enabled) setDismissed(false);
      } catch {
        /* unreachable backend is not maintenance — leave the app usable */
      }
    };
    tick();
    const id = window.setInterval(tick, POLL_MS);
    return () => {
      alive = false;
      window.clearInterval(id);
    };
  }, []);

  const on = !!state?.enabled;

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
      {children}
    </>
  );
}
