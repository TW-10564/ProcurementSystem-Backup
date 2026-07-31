/**
 * The signed-in employee's own record, from Microsoft Graph.
 *
 * The ID token carries a display name and an email and nothing else — no job
 * title, no department. Those live in the directory, so the browser asks Graph
 * for them with a delegated `User.Read` token and forwards the result to our
 * backend (`PUT /me/profile`), which cannot ask Graph itself: it holds no
 * credential of its own by design (public client, no secret).
 *
 * `User.Read` is the least-privileged Graph scope there is — it reads the
 * signed-in user's OWN profile and nothing about anyone else — and is normally
 * user-consentable without an administrator. Normally, not always: a tenant
 * with "users may consent to apps" disabled will fail here, which is why every
 * failure below is non-fatal. The page falls back to the token's name, exactly
 * the behaviour before this file existed.
 */
import { activeAccount, msalInstance, ssoEnabled } from "./msal";

const GRAPH_ME =
  "https://graph.microsoft.com/v1.0/me" +
  "?$select=displayName,givenName,surname,jobTitle,department,officeLocation,mail,userPrincipalName";

/** Snake_case on purpose: this is the shape our own API stores and returns. */
export interface GraphProfile {
  display_name?: string | null;
  given_name?: string | null;
  surname?: string | null;
  job_title?: string | null;
  department?: string | null;
  office_location?: string | null;
}

/**
 * Null whenever Graph is unavailable for any reason — consent refused, the
 * scope not granted, the network blocked by the corporate proxy. A missing job
 * title is not worth breaking a page over.
 */
export async function fetchGraphProfile(): Promise<GraphProfile | null> {
  if (!ssoEnabled || !msalInstance) return null;
  const account = activeAccount();
  if (!account) return null;

  try {
    // A token for GRAPH, deliberately acquired separately from the one we send
    // to our own API. They are different audiences: sending a Graph token to
    // our backend is precisely the bug that made every call 401 with
    // "Signature verification failed" (Graph tokens are not verifiable by
    // anyone but Graph).
    const { accessToken } = await msalInstance.acquireTokenSilent({
      scopes: ["User.Read"],
      account,
    });
    if (!accessToken) return null;

    const res = await fetch(GRAPH_ME, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
    if (!res.ok) return null;
    const g = await res.json();

    return {
      display_name: g.displayName ?? null,
      given_name: g.givenName ?? null,
      surname: g.surname ?? null,
      job_title: g.jobTitle ?? null,
      department: g.department ?? null,
      office_location: g.officeLocation ?? null,
    };
  } catch {
    // Includes InteractionRequiredAuthError: consent for User.Read has not
    // been granted. We do NOT force a redirect to collect it — that would
    // interrupt someone mid-task for a nicety. The next full sign-in asks.
    return null;
  }
}
