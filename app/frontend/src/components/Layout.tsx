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
      <div className="lang-toggle lang-toggle-float">
        <button className={lang === "ja" ? "on" : ""} onClick={() => setLang("ja")}>
          日本語
        </button>
        <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
          EN
        </button>
      </div>

      <aside className="sidebar" tabIndex={-1}>
        <div className="brand-mark" title={t("brand_name")}>
          <span className="badge">TW</span>
        </div>
        <nav className="nav-scroll">
          {NAV.map((group) => (
            <div className="nav-group" key={group.sectionKey}>
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={({ isActive }) =>
                    "nav-link" + (isActive ? " active" : "")
                  }
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span className="nav-tooltip">{t(item.labelKey)}</span>
                </NavLink>
              ))}
            </div>
          ))}
        </nav>
      </aside>
      <main className="main">
        <Outlet />
      </main>
    </div>
  );
}
