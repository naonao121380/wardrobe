import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";

const NAV: { to: string; label: string; end?: boolean }[] = [
  { to: "/", label: "概览", end: true },
  { to: "/wardrobe", label: "衣物" },
  { to: "/outfits", label: "搭配" },
  { to: "/settings", label: "设置" },
];

const TITLES: Record<string, string> = {
  "/": "概览",
  "/wardrobe": "衣物",
  "/outfits": "套装搭配",
  "/settings": "设置",
};

export default function AppShell() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();

  const displayName = user?.display_name || user?.username || "";
  const initial = displayName.trim().charAt(0).toUpperCase() || "?";

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="login-mark">衣</span>
          衣橱
        </div>

        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            className={({ isActive }) => `nav-item${isActive ? " active" : ""}`}
          >
            <span className="nav-dot" />
            {item.label}
          </NavLink>
        ))}

        <div className="sidebar-foot">
          <button type="button" className="btn btn-sm btn-block" onClick={() => void logout()}>
            退出登录
          </button>
        </div>
      </aside>

      <div className="main">
        <header className="topbar">
          <h2>{TITLES[pathname] ?? "衣橱"}</h2>
          <div className="user-chip">
            <span className="muted">{displayName}</span>
            <span className="avatar">{initial}</span>
          </div>
        </header>
        <div className="content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
