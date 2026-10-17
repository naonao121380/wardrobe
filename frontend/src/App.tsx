import { type ReactNode } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { useAuth } from "./auth/AuthProvider";
import AppShell from "./components/AppShell";
import ErrorBoundary from "./components/ErrorBoundary";
import { ConfirmHost } from "./lib/confirm";
import AdminPage from "./pages/AdminPage";
import HomePage from "./pages/HomePage";
import LoginPage from "./pages/LoginPage";
import OutfitsPage from "./pages/OutfitsPage";
import SettingsPage from "./pages/SettingsPage";
import WardrobePage from "./pages/WardrobePage";

/**
 * 管理页只给管理员。非管理员直接打回概览 ——
 * 服务端每个 /admin 端点也会独立校验（403），这里只是别让他看到一个空壳。
 */
function RequireAdmin({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  if (!user?.is_admin) return <Navigate to="/" replace />;
  return <>{children}</>;
}

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
      <ErrorBoundary>
        <Routes>
          <Route path="*" element={<LoginPage />} />
        </Routes>
        <ConfirmHost />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<HomePage />} />
          <Route path="wardrobe" element={<WardrobePage />} />
          <Route path="outfits" element={<OutfitsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route
            path="admin"
            element={
              <RequireAdmin>
                <AdminPage />
              </RequireAdmin>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
        </Route>
      </Routes>
      {/* 必须排在 <Routes> 之后：.overlay 都是 z-index 50，谁在后面谁在上面 */}
      <ConfirmHost />
    </ErrorBoundary>
  );
}
