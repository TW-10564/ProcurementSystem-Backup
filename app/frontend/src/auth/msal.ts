/**
 * Microsoft Entra ID sign-in for the SPA — public client, PKCE, no secret.
 *
 * MSAL runs the authorization-code + PKCE flow in the browser and hands us a
 * token we attach to every API call. There is no client secret anywhere in
 * this app, which is the whole reason for this design: nothing to store on a
 * server, nothing to rotate, nothing to expire silently in eighteen months.
 *
 * SSO is OPT-IN by configuration. With VITE_ENTRA_* unset the app behaves
 * exactly as it did before — no login, no redirect — so local development and
 * the pre-SSO deployment keep working unchanged. `ssoEnabled` is the single
 * switch every other module reads.
 */
import {
  PublicClientApplication,
  type AccountInfo,
  type Configuration,
} from "@azure/msal-browser";

const tenantId = (import.meta.env.VITE_ENTRA_TENANT_ID ?? "").trim();
const clientId = (import.meta.env.VITE_ENTRA_CLIENT_ID ?? "").trim();

/** True only when BOTH ids are present — a half-filled config must not half-enable auth. */
export const ssoEnabled = Boolean(tenantId && clientId);

/**
 * The scope we ask for when calling our own API.
 *
 * Two supported shapes, matching the backend's two accepted audiences:
 *   * unset (default) — we request no custom scope and send the ID TOKEN.
 *     Works with a bare app registration, needing no further Entra changes.
 *   * `api://<client-id>/access_as_user` — set this once the registration
 *     exposes an API, and MSAL returns a proper ACCESS token for it. This is
 *     the better end state: an access token is the credential actually
 *     intended for calling an API.
 */
const apiScope = (import.meta.env.VITE_ENTRA_API_SCOPE ?? "").trim();

/** Scopes for the interactive sign-in. `openid`/`profile` give us name+email. */
export const loginRequest = {
  scopes: apiScope ? [apiScope] : ["openid", "profile", "email"],
};

/**
 * What we ask consent for at the sign-in prompt, as opposed to what we request
 * per-token afterwards.
 *
 * `User.Read` is added so the consent screen collects it ONCE, at the only
 * moment the user is already being interrupted — otherwise the first attempt
 * to read their job title from Graph fails with InteractionRequiredAuthError
 * and the profile page shows half a person.
 *
 * Not merged into `loginRequest`: an access-token request may only name scopes
 * for ONE resource, so pairing our own API scope with a Graph scope makes
 * `acquireTokenSilent` throw. Consent may span resources; a token may not.
 */
export const loginScopes = {
  scopes: apiScope
    ? [apiScope, "User.Read"]
    : ["openid", "profile", "email", "User.Read"],
};

const configuration: Configuration = {
  auth: {
    clientId,
    authority: `https://login.microsoftonline.com/${tenantId}`,
    // Whatever origin the page was loaded from — so the same bundle works on
    // localhost and on one-forecast.twave.co.jp. Each origin must still be
    // registered in Entra under the "Single-page application" platform.
    redirectUri: window.location.origin,
    // Return users to where they started rather than leaving them on a blank
    // callback URL.
    postLogoutRedirectUri: window.location.origin,
  },
  cache: {
    // sessionStorage, not localStorage: the token dies with the tab, which is
    // the behaviour you want on the shared PCs in the office. The cost is a
    // silent re-auth per new tab, which is invisible to the user because the
    // Entra session cookie is still there.
    cacheLocation: "sessionStorage",
  },
};

/**
 * Null when SSO is off, so nothing tries to initialise MSAL with an empty
 * clientId (which throws at construction, taking the whole app down).
 */
export const msalInstance = ssoEnabled
  ? new PublicClientApplication(configuration)
  : null;

/**
 * MSAL v3+ requires an explicit initialize() before any other call, and it
 * must finish before React renders anything that might trigger a redirect.
 */
export async function initializeMsal(): Promise<void> {
  if (!msalInstance) return;
  await msalInstance.initialize();
  // Completes a redirect we are returning from; null on a normal page load.
  const result = await msalInstance.handleRedirectPromise();
  const account = result?.account ?? msalInstance.getAllAccounts()[0];
  if (account) msalInstance.setActiveAccount(account);
}

export function activeAccount(): AccountInfo | null {
  if (!msalInstance) return null;
  return msalInstance.getActiveAccount() ?? msalInstance.getAllAccounts()[0] ?? null;
}

/**
 * A token for the API, refreshed silently when possible.
 *
 * Returns null rather than throwing when SSO is off or no one is signed in:
 * the axios interceptor uses that to mean "send no Authorization header",
 * which is exactly the pre-SSO behaviour.
 */
export async function acquireApiToken(): Promise<string | null> {
  if (!msalInstance) return null;
  const account = activeAccount();
  if (!account) return null;

  try {
    const result = await msalInstance.acquireTokenSilent({ ...loginRequest, account });
    // Which credential to send depends on what we asked for, and the choice
    // must NOT be a `||` fallback — with the default scopes MSAL *does* return
    // a non-empty accessToken, but it is a token for MICROSOFT GRAPH. Graph
    // access tokens carry a nonce in the header that makes their signature
    // unverifiable by anyone except Graph, so our backend rejects every call
    // with "Signature verification failed" while sign-in itself looks fine.
    //   * custom API scope set  → accessToken, minted for our own API.
    //   * no custom scope       → idToken, whose `aud` is our client id.
    return apiScope ? result.accessToken || null : result.idToken || null;
  } catch {
    // Silent renewal fails when the refresh token has expired or consent was
    // revoked. A full-page redirect is the only way back, and it is safe here
    // because MSAL restores the current URL afterwards.
    await msalInstance.acquireTokenRedirect({ ...loginRequest, account });
    return null;
  }
}

/**
 * Log out of THIS APP only, with no trip to Microsoft.
 *
 * `clearCache()` is the whole mechanism: it drops MSAL's own tokens without
 * navigating to Entra's logout endpoint, so the user goes straight to the
 * landing page instead of flashing through login.microsoftonline.com.
 * (`logoutRedirect({onRedirectNavigate: () => false})` did this in MSAL 3–4;
 * v5 removed that field from EndSessionRequest, and clearCache is the
 * supported replacement.)
 *
 * KNOWN TRADE-OFF, chosen deliberately (2026-07-31) for the faster exit:
 * the Entra SESSION COOKIE SURVIVES. Pressing "enter the portal" afterwards
 * signs the previous user back in silently, with no prompt — so on a shared
 * office PC the next person can end up working, and having their adjustments
 * logged, under someone else's name. Two ways to tighten it later if that
 * bites: add `prompt: "select_account"` to `loginScopes` so the next sign-in
 * always asks who you are, or restore the full `logoutRedirect` (drop the
 * `onRedirectNavigate` line) to end the Microsoft session as well.
 *
 * Mitigation already in place: MSAL caches in sessionStorage, so closing the
 * browser really does end it — this gap only spans "logged out but same tab
 * still open".
 */
export async function signOut(): Promise<void> {
  if (!msalInstance) return;
  const account = activeAccount();
  await msalInstance.clearCache({ account: account ?? undefined });
  msalInstance.setActiveAccount(null);
  // A full page load, not a router navigate: it drops every piece of in-memory
  // state (IdentityContext's /me, the directory, cached page data) so nothing
  // of the previous user is left on screen.
  window.location.assign("/");
}

