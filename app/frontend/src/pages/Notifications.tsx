import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLang } from "../i18n/LanguageContext";
import type { Notification } from "../api/types";

export default function Notifications() {
  const { t, lang } = useLang();
  const [list, setList] = useState<Notification[]>([]);
  const [summary, setSummary] = useState<{
    total_events: number;
    by_author: Record<string, number>;
  } | null>(null);

  useEffect(() => {
    api.notifications(200).then(setList);
    api.notificationSummary().then(setSummary);
  }, []);

  return (
    <div>
      <div className="page-head">
        <h2>{t("notif_title")}</h2>
        <p>{t("notif_sub")}</p>
      </div>

      {summary && (
        <div className="kpis">
          <div className="kpi">
            <div className="lab">{t("notif_total_events")}</div>
            <div className="val">{summary.total_events.toLocaleString()}</div>
          </div>
          {Object.entries(summary.by_author)
            .sort((a, b) => b[1] - a[1])
            .slice(0, 3)
            .map(([who, n]) => (
              <div className="kpi" key={who}>
                <div className="lab">{who}</div>
                <div className="val">{n}</div>
                <div className="sub">{t("notif_action_count")}</div>
              </div>
            ))}
        </div>
      )}

      <div className="card">
        <div className="hd">
          <h3>{t("notif_all_activity")}</h3>
        </div>
        <div className="tablewrap">
          {list.length === 0 ? (
            <div className="empty">{t("notif_none")}</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>{t("notif_th_when")}</th>
                  <th>{t("notif_th_who")}</th>
                  <th>{t("notif_th_what")}</th>
                </tr>
              </thead>
              <tbody>
                {list.map((n) => (
                  <tr key={n.id}>
                    <td className="mono dim" style={{ width: 170 }}>
                      {new Date(n.at).toLocaleString(lang === "ja" ? "ja-JP" : "en-US")}
                    </td>
                    <td style={{ fontWeight: 600 }}>{n.who ?? "system"}</td>
                    <td>{n.what}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}
