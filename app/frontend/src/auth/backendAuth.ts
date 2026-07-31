/**
 * Sign-in when the BACKEND owns the flow (confidential client).
 *
 * The browser never speaks to Microsoft on its own here: it asks our own
 * server to start the flow, Microsoft sends the user back to our server, and
 * the server sets an HttpOnly session cookie. Consequences worth stating,
 * because they are the reason for the design:
 *
 *   * No `VITE_ENTRA_*` in the bundle — the tenant and client id are not
 *     compiled into the JavaScript. (They are still visible in the address bar
 *     during the redirect to Microsoft; they are identifiers, not secrets.)
 *   * No token in `sessionStorage` — script cannot read an HttpOnly cookie, so
 *     an XSS bug cannot exfiltrate a credential for reuse elsewhere.
 *
 * Navigation is a full page load (`window.location`), not a fetch: an OAuth
 * redirect has to happen in the address bar, and Microsoft's page cannot be
 * loaded in an iframe or via XHR.
 */
import { api } from "../api/client";

let cached: boolean | null = null;

/**
 * Whether the server is running sign-in for us.
 *
 * Cached after the first answer: every guarded page asks, and the value cannot
 * change without a server restart. On a network error we answer `false` so the
 * app falls back to its pre-SSO behaviour rather than trapping the user on a
 * loading screen it can never leave.
 */
export async function backendSignInEnabled(): Promise<boolean> {
  if (cached !== null) return cached;
  try {
    const status = await api.authStatus();
    cached = Boolean(status?.backend_sign_in);
  } catch {
    cached = false;
  }
  return cached;
}

/** Hand the browser to the server, which hands it to Microsoft. */
export function startBackendSignIn(next: string = window.location.pathname): void {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
  window.location.assign(`${base}/auth/login?next=${encodeURIComponent(next)}`);
}

/**
 * End the session.
 *
 * `federated` also ends the Microsoft session. Default false, matching the
 * behaviour chosen for the SPA: the fast exit, at the cost of the next person
 * on a shared PC being signed straight back in. Closing the browser ends it
 * either way, since the Entra session cookie is not persistent by default.
 */
export function backendSignOut(federated = false): void {
  const base = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";
  window.location.assign(`${base}/auth/logout?federated=${federated ? "true" : "false"}`);
}
