import { useRef, useState } from "react";
import type { ReactElement } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useLang } from "../i18n/LanguageContext";
import SignedInUser from "../auth/SignedInUser";
import { signOut, ssoEnabled } from "../auth/msal";
import type { TranslationKey } from "../i18n/translations";
import {
  AccuracyIcon,
  AdjustmentsIcon,
  CompatIcon,
  DashboardIcon,
  DiscontinuationIcon,
  ForecastIcon,
  HelpIcon,
  LogoutIcon,
  MismatchIcon,
  NotificationsIcon,
} from "./icons";

interface NavItem {
  to: string;
  labelKey: TranslationKey;
  // React 19 removed the GLOBAL JSX namespace; it now lives under React,
  // so a bare `JSX.Element` no longer resolves under `tsc -b`.
  Icon: (props: { className?: string }) => ReactElement;
}

const NAV: { sectionKey: TranslationKey; items: NavItem[] }[] = [
  {
    sectionKey: "nav_section_overview",
    items: [{ to: "/app", labelKey: "nav_dashboard", Icon: DashboardIcon }],
  },
  {
    sectionKey: "nav_section_accuracy",
    items: [
      { to: "/app/forecast", labelKey: "nav_forecast", Icon: ForecastIcon },
      { to: "/app/accuracy", labelKey: "nav_accuracy", Icon: AccuracyIcon },
    ],
  },
  {
    sectionKey: "nav_section_compat",
    items: [
      { to: "/app/mismatch", labelKey: "nav_mismatch", Icon: MismatchIcon },
      { to: "/app/compat", labelKey: "nav_compat", Icon: CompatIcon },
      { to: "/app/discontinuation", labelKey: "nav_discontinuation", Icon: DiscontinuationIcon },
    ],
  },
  {
    sectionKey: "nav_section_plan",
    items: [{ to: "/app/adjustments", labelKey: "nav_adjustments", Icon: AdjustmentsIcon }],
  },
  {
    sectionKey: "nav_section_ops",
    items: [{ to: "/app/notifications", labelKey: "nav_notifications", Icon: NotificationsIcon }],
  },
  // Reference material, deliberately last: it's onboarding for the purchasing
  // and planning teams, not a daily task, so it shouldn't sit above the work.
  {
    sectionKey: "nav_section_help",
    items: [{ to: "/app/help", labelKey: "nav_help", Icon: HelpIcon }],
  },
];

export default function Layout() {
  const { lang, setLang, t } = useLang();
  const [expanded, setExpanded] = useState(false);
  const asideRef = useRef<HTMLElement>(null);
  const navigate = useNavigate();

  return (
    <div className="app-shell">
      <header className="top-header">
        <div className="header-left">
          <Link to="/app" className="header-logo-link">
            <img src="/twave-logo.png" alt={t("brand_name")} className="header-logo" />
          </Link>
        </div>
        <div className="header-right">
          <SignedInUser />
          <div className="lang-toggle">
            <button className={lang === "ja" ? "on" : ""} onClick={() => setLang("ja")}>
              日本語
            </button>
            <button className={lang === "en" ? "on" : ""} onClick={() => setLang("en")}>
              EN
            </button>
          </div>
          {/* With SSO on, "logout" must end the MICROSOFT session too.
              Navigating to "/" alone would leave the Entra cookie in place, so
              the next visit signs the same person straight back in without a
              prompt — which on a shared PC is the opposite of logging out. */}
          <button
            className="logout-btn"
            onClick={() => (ssoEnabled ? void signOut() : navigate("/"))}
          >
            <LogoutIcon className="logout-icon" />
            {t("logout")}
          </button>
        </div>
      </header>

      <aside
        ref={asideRef}
        className={"sidebar" + (expanded ? " expanded" : "")}
        tabIndex={-1}
        onMouseEnter={() => setExpanded(true)}
        onMouseLeave={() => setExpanded(false)}
        onFocus={() => setExpanded(true)}
        onBlur={(e) => {
          if (!asideRef.current?.contains(e.relatedTarget as Node)) setExpanded(false);
        }}
      >
        <nav className="nav-scroll">
          {NAV.map((group) => (
            <div className="nav-group" key={group.sectionKey}>
              {expanded && <div className="nav-group-label">{t(group.sectionKey)}</div>}
              {group.items.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/app"}
                  className={({ isActive }) =>
                    "nav-link" + (isActive ? " active" : "")
                  }
                >
                  <item.Icon className="nav-icon" />
                  {expanded ? (
                    <span className="nav-label">{t(item.labelKey)}</span>
                  ) : (
                    <span className="nav-tooltip">{t(item.labelKey)}</span>
                  )}
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
