import { useNavigate } from "react-router-dom";
import { useLang } from "../i18n/LanguageContext";
import { ssoEnabled } from "../auth/msal";
import { backendSignInEnabled, startBackendSignIn } from "../auth/backendAuth";

export default function Landing() {
  const { lang, setLang, t } = useLang();
  const navigate = useNavigate();

  return (
    <div className="landing">
      <img src="/twave-logo.png" alt="THIRDWAVE" className="landing-logo" />
      <div className="landing-toggle">
        <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
          English
        </button>
        <button className={lang === "ja" ? "on" : ""} onClick={() => setLang("ja")}>
          日本語
        </button>
      </div>

      <div className="landing-body">
        <div className="landing-copy">
          <div className="landing-eyebrow">{t("brand_sub")}</div>
          <h1>
            {t("landing_headline_1")}
            <span className="accent">{t("landing_headline_2")}</span>
          </h1>
          <p>{t("landing_desc")}</p>
        </div>

        <div className="landing-card">
          <h2>{t("landing_enter_title")}</h2>
          <button className="landing-enter-btn" onClick={async () => {
              if (await backendSignInEnabled()) return startBackendSignIn("/app");
              navigate("/app");
            }}>
            {t("landing_enter_button")}
          </button>
          <div className="landing-enter-note">
            {t(ssoEnabled ? "landing_enter_note_sso" : "landing_enter_note")}
          </div>
        </div>
      </div>

      <div className="landing-wave" aria-hidden="true" />
    </div>
  );
}
