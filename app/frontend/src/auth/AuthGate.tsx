/**
 * Requires a Microsoft sign-in before anything else renders.
 *
 * Landing on any URL with no session sends the browser straight to Microsoft
 * — no "Sign in" button to click first. That is deliberate: an interstitial
 * page is one more thing to explain to twelve people, and it is what made the
 * earlier oauth2-proxy attempt look broken (it answered 403 with its own
 * button page).
 *
 * Wraps only the /app subtree, never the public landing page at "/". That
 * distinction is what makes signing out work: Entra returns the browser to
 * our origin, and if the gate covered "/" it would immediately bounce the
 * signed-out user back to Microsoft — so "log out" appeared to dump them on
 * an account picker rather than logging them out.
 *
 * When VITE_ENTRA_* is unset this component is a pass-through, so local dev
 * and the pre-SSO deployment are untouched.
 */
import { MsalProvider, MsalAuthenticationTemplate } from "@azure/msal-react";
import { InteractionType } from "@azure/msal-browser";
import type { MsalAuthenticationResult } from "@azure/msal-react";
import type { ReactNode } from "react";
import { loginScopes, msalInstance, ssoEnabled } from "./msal";

const shell: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "center",
  justifyContent: "center",
  minHeight: "100vh",
  gap: "0.75rem",
  fontFamily: "system-ui, sans-serif",
  color: "#334155",
  textAlign: "center",
  padding: "2rem",
};

function SigningIn() {
  return (
    <div style={shell}>
      <div style={{ fontSize: "1.05rem", fontWeight: 600 }}>
        Microsoft アカウントで認証しています…
      </div>
      <div style={{ fontSize: "0.85rem", color: "#64748b" }}>
        Redirecting to Microsoft sign-in…
      </div>
    </div>
  );
}

/**
 * Shown when the redirect itself failed — a wrong redirect URI, a user who is
 * not assigned the application, or consent that was declined. The Entra error
 * code is displayed on purpose: without it, every one of those looks the same
 * to the person reporting it, and the code is the only thing that separates
 * "IT must assign you" from "the config is wrong".
 */
function SignInFailed({ error }: MsalAuthenticationResult) {
  return (
    <div style={shell}>
      <div style={{ fontSize: "1.05rem", fontWeight: 600, color: "#b91c1c" }}>
        サインインできませんでした
      </div>
      <div style={{ fontSize: "0.9rem" }}>Sign-in failed.</div>
      {error && (
        <pre
          style={{
            fontSize: "0.75rem",
            color: "#64748b",
            maxWidth: "42rem",
            whiteSpace: "pre-wrap",
            textAlign: "left",
            background: "#f8fafc",
            border: "1px solid #e2e8f0",
            borderRadius: "6px",
            padding: "0.75rem",
          }}
        >
          {error.errorCode}
          {error.errorMessage ? `\n${error.errorMessage}` : ""}
        </pre>
      )}
      <button
        onClick={() => window.location.reload()}
        style={{
          marginTop: "0.5rem",
          padding: "0.45rem 1.1rem",
          borderRadius: "6px",
          border: "1px solid #cbd5e1",
          background: "#fff",
          cursor: "pointer",
        }}
      >
        再試行 / Retry
      </button>
    </div>
  );
}

export default function AuthGate({ children }: { children: ReactNode }) {
  if (!ssoEnabled || !msalInstance) return <>{children}</>;

  return (
    <MsalProvider instance={msalInstance}>
      <MsalAuthenticationTemplate
        interactionType={InteractionType.Redirect}
        authenticationRequest={loginScopes}
        loadingComponent={SigningIn}
        errorComponent={SignInFailed}
      >
        {children}
      </MsalAuthenticationTemplate>
    </MsalProvider>
  );
}
