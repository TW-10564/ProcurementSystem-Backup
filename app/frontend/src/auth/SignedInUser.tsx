/**
 * The signed-in employee, as Entra reports them.
 *
 * Worth the header space for a reason beyond politeness: these are shared
 * office PCs, and every adjustment and compat-rule confirmation is recorded
 * against the signed-in person. Showing whose name that will be, before they
 * click confirm, is how someone notices they are on a colleague's session.
 *
 * Renders nothing when SSO is off, so the header is unchanged pre-SSO.
 */
import { useEffect, useState } from "react";
import { activeAccount, ssoEnabled } from "./msal";

export default function SignedInUser() {
  const [account, setAccount] = useState(() => activeAccount());

  // The account is not necessarily available on first paint: AuthGate may
  // still be completing the redirect. Re-read once on mount.
  useEffect(() => {
    if (!ssoEnabled) return;
    setAccount(activeAccount());
  }, []);

  if (!ssoEnabled || !account) return null;

  // `name` is the display name; `username` is the UPN/email, which is what the
  // backend records as audit_author — so it is the more useful hover text.
  const label = account.name || account.username;

  return (
    <span
      className="signed-in-user"
      title={account.username}
      style={{
        fontSize: "0.8rem",
        color: "#475569",
        maxWidth: "14rem",
        overflow: "hidden",
        textOverflow: "ellipsis",
        whiteSpace: "nowrap",
      }}
    >
      {label}
    </span>
  );
}
