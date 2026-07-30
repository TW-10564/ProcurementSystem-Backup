/// <reference types="vite/client" />

/**
 * Build-time configuration. Every one of these is compiled INTO the bundle and
 * is therefore public to anyone who loads the page — that is a property of the
 * platform, not an oversight, so nothing confidential may be added here.
 *
 * The Entra tenant and client ids are safe on those terms: both appear in the
 * browser's address bar during sign-in anyway. There is deliberately no client
 * secret, because the SPA is a public client using PKCE (src/auth/msal.ts).
 *
 * VITE_API_KEY is the exception that proves the rule: it IS a shared secret,
 * and shipping it to browsers is a known finding. Once SSO is enforced, build
 * without it — the Bearer token replaces it as the browser's credential.
 */
interface ImportMetaEnv {
  /** Backend origin, or a same-origin path like `/api` (the default here). */
  readonly VITE_API_BASE?: string;
  /** Shared API key. Omit once SSO is on — see above. */
  readonly VITE_API_KEY?: string;
  /** Entra directory (tenant) ID. Set with VITE_ENTRA_CLIENT_ID to enable SSO. */
  readonly VITE_ENTRA_TENANT_ID?: string;
  /** Entra application (client) ID of the SPA registration. */
  readonly VITE_ENTRA_CLIENT_ID?: string;
  /**
   * Optional. `api://<client-id>/access_as_user` once the registration exposes
   * an API scope; unset means the ID token is used instead. Both are accepted
   * by the backend (onefcst/api/entra.py).
   */
  readonly VITE_ENTRA_API_SCOPE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
