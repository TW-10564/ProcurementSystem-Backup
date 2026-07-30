/**
 * Who a write will be attributed to.
 *
 * With SSO on, the SERVER decides: it takes the author from the verified token
 * and discards whatever we send (see api/auth.py:effective_author). So what
 * this returns is only for DISPLAY — telling the user whose name is going on
 * the record — and as the value to send when SSO is off, where the server has
 * nothing better to go on.
 *
 * Why that split matters: sending a name the server ignores is harmless, but
 * showing a name that differs from what gets recorded is not. These office PCs
 * are shared, and "I confirmed fifty rules as somebody else" is the exact
 * confusion worth spending a line of UI on.
 */
import { activeAccount, ssoEnabled } from "./msal";

const LEGACY_KEY = "onefcst_author";

/**
 * The author to send. Under SSO this is the signed-in address, which the
 * server will independently arrive at anyway — so the two agree, and the UI is
 * telling the truth about the audit trail.
 */
export function currentAuthor(): string {
  if (ssoEnabled) {
    const account = activeAccount();
    // username is the UPN/work address — the same claim the backend uses.
    if (account?.username) return account.username;
  }
  // Pre-SSO: the name the user typed, kept per-browser. Deliberately no
  // hardcoded fallback — Forecast.tsx used to default to the literal "bala",
  // which quietly attributed one person's adjustments to a name they never
  // chose. An empty value becomes "unknown" server-side, which is at least
  // honest about knowing nothing.
  return localStorage.getItem(LEGACY_KEY) ?? "";
}

/** True when the user still has to type a name — i.e. SSO is not in play. */
export function authorIsSelfAsserted(): boolean {
  return !ssoEnabled || !activeAccount();
}

export function setLegacyAuthor(value: string): void {
  localStorage.setItem(LEGACY_KEY, value);
}

export function legacyAuthor(): string {
  return localStorage.getItem(LEGACY_KEY) ?? "";
}
