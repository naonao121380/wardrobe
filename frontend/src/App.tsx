import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthProvider";
import AppShell from "./components/AppShell";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import OutfitsPage from "./pages/OutfitsPage";
import SettingsPage from "./pages/SettingsPage";
import WardrobePage from "./pages/WardrobePage";

export default function App() {
  const { user, ready } = useAuth();

  if (!ready) {
    return (
      <div className="login-wrap">
        <span className="tiny">正在恢复会话…</span>
      </div>
    );
  }

  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<HomePage />} />
        <Route path="wardrobe" element={<WardrobePage />} />
        <Route path="outfits" element={<OutfitsPage />} />
        <Route path="settings" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
