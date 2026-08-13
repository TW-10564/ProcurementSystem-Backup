/**
 * "My profile" — the signed-in employee, as the company directory describes them.
 *
 * Deliberately read-only, and with no refresh control. Every field comes from
 * Microsoft Entra / the company directory, so the only correct way to change
 * any of it is to have IT change it in Azure; an editable copy here would
 * drift from the directory within a week and there would be no way to tell
 * which was right. A refresh button would imply the page can go stale in a way
 * the user is responsible for fixing — it cannot: IdentityContext re-reads on
 * every load, so a correction made in Azure is already there next time.
 *
 * Rows with no value render as — rather than disappearing: "no job title set
 * in Azure" is information, and a row that vanishes just looks like a bug.
 */
import { useLang } from "../i18n/LanguageContext";
import { useIdentity, useMyName } from "../auth/IdentityContext";

function Row({ label, value }: { label: string; value?: string | null }) {
  return (
    <div className="profile-row">
      <div className="profile-label">{label}</div>
      <div className="profile-value">
        {value ? value : <span className="profile-empty">—</span>}
      </div>
    </div>
  );
}

export default function Profile() {
  const { t } = useLang();
  const { me, loading } = useIdentity();
  const name = useMyName();

  if (loading && !me) return <div className="page-head"><p>{t("common_loading")}</p></div>;

  const p = me?.profile;

  return (
    <>
      <div className="page-head">
        <h2>{t("profile_title")}</h2>
      </div>

      <div className="profile-grid">
        <section className="card">
          <div className="hd">
            <h3>{t("profile_identity")}</h3>
          </div>
          <div className="profile-body">
            <div className="profile-hero">
              <div className="profile-avatar" aria-hidden="true">
                {(name || "?").trim().charAt(0).toUpperCase()}
              </div>
              <div>
                <div className="profile-hero-name">{name || t("profile_unknown")}</div>
                <div className="profile-hero-sub">{me?.email || ""}</div>
              </div>
            </div>

            <Row label={t("profile_display_name")} value={p?.display_name || me?.name} />
            <Row label={t("profile_given_name")} value={p?.given_name} />
            <Row label={t("profile_surname")} value={p?.surname} />
            <Row label={t("profile_email")} value={me?.email} />
            <Row label={t("profile_job_title")} value={p?.job_title} />
            <Row label={t("profile_department")} value={p?.department} />
            <Row label={t("profile_office")} value={p?.office_location} />
          </div>
        </section>
      </div>
    </>
  );
}
