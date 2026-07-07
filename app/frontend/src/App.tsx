import { BrowserRouter, Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import Dashboard from "./pages/Dashboard";
import Forecast from "./pages/Forecast";
import Accuracy from "./pages/Accuracy";
import Mismatch from "./pages/Mismatch";
import CompatReview from "./pages/CompatReview";
import Adjustments from "./pages/Adjustments";
import Notifications from "./pages/Notifications";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Dashboard />} />
          <Route path="forecast" element={<Forecast />} />
          <Route path="accuracy" element={<Accuracy />} />
          <Route path="mismatch" element={<Mismatch />} />
          <Route path="compat" element={<CompatReview />} />
          <Route path="adjustments" element={<Adjustments />} />
          <Route path="notifications" element={<Notifications />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
