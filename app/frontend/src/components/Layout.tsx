import { NavLink, Outlet } from "react-router-dom";
import { useLang } from "../i18n/LanguageContext";
import type { TranslationKey } from "../i18n/translations";

interface NavItem {
  to: string;
  labelKey: TranslationKey;
  icon: string;
}

const NAV: { sectionKey: TranslationKey; items: NavItem[] }[] = [
  {
    sectionKey: "nav_section_overview",
    items: [{ to: "/", labelKey: "nav_dashboard", icon: "📊" }],
  },
  {
    sectionKey: "nav_section_accuracy",
    items: [
      { to: "/forecast", labelKey: "nav_forecast", icon: "🔮" },
      { to: "/accuracy", labelKey: "nav_accuracy", icon: "📈" },
    ],
  },
  {
    sectionKey: "nav_section_compat",
    items: [
      { to: "/mismatch", labelKey: "nav_mismatch", icon: "⚠️" },
      { to: "/compat", labelKey: "nav_compat", icon: "🔗" },
    ],
  },
  {
    sectionKey: "nav_section_plan",
    items: [{ to: "/adjustments", labelKey: "nav_adjustments", icon: "✍️" }],
  },
  {
    sectionKey: "nav_section_ops",
    items: [{ to: "/notifications", labelKey: "nav_notifications", icon: "🔔" }],
  },
];

export default function Layout() {
  const { lang, setLang, t } = useLang();

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <img className="logo-img" src="/twave-logo.png" alt="THIRDWAVE" />
          <div>
            <h1>{t("brand_name")}</h1>
            <p>{t("brand_sub")}</p>
          </div>
        </div>
        <div className="lang-toggle">
          <button className={lang === "ja" ? "on" : ""} onClick={() => setLang("ja")}>
            日本語
          </button>
          <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
            EN
          </button>
        </div>
        {NAV.map((group) => (
          <div key={group.sectionKey}>
            <div className="nav-section">{t(group.sectionKey)}</div>
            {group.items.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === "/"}
                className={({ isActive }) =>
                  "nav-link" + (isActive ? " active" : "")
                }
              >
                <span>{item.icon}</span>
                {t(item.labelKey)}
              </NavLink>
            ))}
          </div>
        ))}
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
