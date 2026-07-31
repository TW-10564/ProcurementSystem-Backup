/**
 * The signed-in employee, in the header.
 *
 * Worth the header space for a reason beyond politeness: these are shared
 * office PCs, and every adjustment and compat-rule confirmation is recorded
 * against the signed-in person. Showing whose name that will be, before they
 * click confirm, is how someone notices they are on a colleague's session.
 *
 * Renders nothing when SSO is off, so the header is unchanged pre-SSO.
 *
 * The name shown is the DIRECTORY display name (via IdentityContext), not
 * whatever MSAL happened to cache locally. Those differ more often than you
 * would expect — the local account object can hold a stale name from an
 * earlier sign-in, and when the tenant sets no display name at all MSAL falls
 * back to the raw UPN, which is how a header ends up reading
 * `m_balamurukan@twave.co.jp` where a person's name should be.
 */
import { Link } from "react-router-dom";
import { useLang } from "../i18n/LanguageContext";
import { useIdentity, useMyName } from "./IdentityContext";
import { ssoEnabled } from "./msal";

export default function SignedInUser() {
  const { me, loading } = useIdentity();
  const { t } = useLang();
  const name = useMyName();

  if (!ssoEnabled) return null;
  // Nothing rather than a flash of the raw email that is replaced by the real
  // name a moment later.
  if (loading && !name) return null;
  if (!name && !me?.email) return null;

  const subtitle = me?.profile?.department || me?.profile?.job_title || me?.email || "";

  return (
    <Link
      to="/app/profile"
      className="signed-in-user"
      title={`${name}${me?.email ? ` — ${me.email}` : ""}\n${t("profile_title")}`}
    >
      <span className="signed-in-avatar" aria-hidden="true">
        {(name || me?.email || "?").trim().charAt(0).toUpperCase()}
      </span>
      <span className="signed-in-text">
        <span className="signed-in-name">{name || me?.email}</span>
        {subtitle && <span className="signed-in-sub">{subtitle}</span>}
      </span>
    </Link>
  );
}
