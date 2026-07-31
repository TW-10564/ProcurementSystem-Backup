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
import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { loginScopes, msalInstance, ssoEnabled } from "./msal";
import { backendSignInEnabled, startBackendSignIn } from "./backendAuth";
import { api } from "../api/client";

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

/**
 * The gate when the BACKEND runs sign-in.
 *
 * There is nothing for MSAL to do in this mode: we ask the server who we are,
 * and if the answer is "nobody" we hand the browser to the server's login
 * endpoint. The check has to happen before children render, or every page
 * fires its own 401 on mount.
 */
function BackendAuthGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<"checking" | "in" | "failed">("checking");

  useEffect(() => {
    let cancelled = false;
    api
      .me()
      .then((me) => {
        if (cancelled) return;
        if (me?.authenticated) {
          setState("in");
        } else {
          // Not signed in — go and do it. No interstitial button, matching
          // the behaviour the team is used to.
          startBackendSignIn(window.location.pathname + window.location.search);
        }
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const status = (err as { response?: { status?: number } })?.response?.status;
        if (status === 401) {
          startBackendSignIn(window.location.pathname + window.location.search);
        } else {
          // A 5xx or a dead backend is NOT a sign-in problem, and bouncing to
          // Microsoft would hide it behind a redirect loop.
          setState("failed");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state === "in") return <>{children}</>;
  if (state === "failed") {
    return (
      <div style={shell}>
        <div style={{ fontSize: "1.05rem", fontWeight: 600, color: "#b91c1c" }}>
          サーバーに接続できません
        </div>
        <div style={{ fontSize: "0.9rem" }}>Could not reach the server.</div>
        <button
          onClick={() => window.location.reload()}
          style={{
            marginTop: "0.5rem", padding: "0.45rem 1.1rem", borderRadius: "6px",
            border: "1px solid #cbd5e1", background: "#fff", cursor: "pointer",
          }}
        >
          再試行 / Retry
        </button>
      </div>
    );
  }
  return <SigningIn />;
}

export default function AuthGate({ children }: { children: ReactNode }) {
  // Which flow is in play is decided by the SERVER, not by the bundle — the
  // whole point of moving sign-in server-side is that the frontend holds no
  // Entra configuration to key off.
  const [mode, setMode] = useState<"unknown" | "backend" | "spa">("unknown");
  useEffect(() => {
    backendSignInEnabled().then((on) => setMode(on ? "backend" : "spa"));
  }, []);

  if (mode === "unknown") return <SigningIn />;
  if (mode === "backend") return <BackendAuthGate>{children}</BackendAuthGate>;

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
