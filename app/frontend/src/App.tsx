import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import MaintenanceGate from "./components/MaintenanceGate";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";
import Forecast from "./pages/Forecast";
import Accuracy from "./pages/Accuracy";
import Mismatch from "./pages/Mismatch";
import CompatReview from "./pages/CompatReview";
import Adjustments from "./pages/Adjustments";
import Notifications from "./pages/Notifications";
import Discontinuation from "./pages/Discontinuation";
import Help from "./pages/Help";
import Profile from "./pages/Profile";
import AuthGate from "./auth/AuthGate";
import { IdentityProvider } from "./auth/IdentityContext";

function App() {
  return (
    <BrowserRouter>
      {/* wraps every route, landing page included, so the notice is
          site-wide rather than per-page */}
      <MaintenanceGate>
      <Routes>
        <Route path="/" element={<Landing />} />
        {/* Sign-in is required for the app, NOT for the site. Gating "/" too
            meant a signed-out visitor could never see the landing page: Entra
            returned them to the origin and the gate bounced them straight back
            to Microsoft, so "log out" appeared to dump the user on an account
            picker. IdentityProvider sits inside the gate because /me needs a
            token. */}
        <Route
          path="/app"
          element={
            <AuthGate>
              <IdentityProvider>
                <Layout />
              </IdentityProvider>
            </AuthGate>
          }
        >
          <Route index element={<Dashboard />} />
          <Route path="forecast" element={<Forecast />} />
          <Route path="accuracy" element={<Accuracy />} />
          <Route path="mismatch" element={<Mismatch />} />
          <Route path="compat" element={<CompatReview />} />
          <Route path="discontinuation" element={<Discontinuation />} />
          <Route path="adjustments" element={<Adjustments />} />
          <Route path="notifications" element={<Notifications />} />
          <Route path="help" element={<Help />} />
          <Route path="profile" element={<Profile />} />
        </Route>

        {/* pre-2026-07-21 bookmarks pointed at these top-level paths directly
            (before the app moved under /app) — redirect instead of 404ing blank */}
        <Route path="/forecast" element={<Navigate to="/app/forecast" replace />} />
        <Route path="/accuracy" element={<Navigate to="/app/accuracy" replace />} />
        <Route path="/mismatch" element={<Navigate to="/app/mismatch" replace />} />
        <Route path="/compat" element={<Navigate to="/app/compat" replace />} />
        <Route path="/discontinuation" element={<Navigate to="/app/discontinuation" replace />} />
        <Route path="/adjustments" element={<Navigate to="/app/adjustments" replace />} />
        <Route path="/notifications" element={<Navigate to="/app/notifications" replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </MaintenanceGate>
    </BrowserRouter>
  );
}

export default App;
