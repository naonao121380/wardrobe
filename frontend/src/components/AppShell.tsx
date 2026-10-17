import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { useAuth } from "../auth/AuthProvider";
import { applyTheme, getThemeChoice, watchSystemTheme } from "../lib/theme";

const NAV: { to: string; label: string; end?: boolean; adminOnly?: boolean }[] = [
  { to: "/", label: "概览", end: true },
  { to: "/wardrobe", label: "衣物" },
  { to: "/outfits", label: "搭配" },
  { to: "/settings", label: "设置" },
  { to: "/admin", label: "用户管理", adminOnly: true },
];

const TITLES: Record<string, string> = {
  "/": "概览",
  "/wardrobe": "衣物",
  "/outfits": "套装搭配",
  "/settings": "设置",
  "/admin": "用户管理",
};

export default function AppShell() {
  const { user, logout } = useAuth();
  const { pathname } = useLocation();

  const displayName = user?.display_name || user?.username || "";
  const initial = displayName.trim().charAt(0).toUpperCase() || "?";
  // 管理入口只给管理员看 —— 普通用户看到也进不去（前后端都拦）
  const nav = NAV.filter((item) => !item.adminOnly || user?.is_admin);

  // 「跟随系统」时，系统换深浅要跟着变。首帧的 data-theme 由 index.html 的引导脚本写好，
  // 这里只负责后续的系统切换（以及挂载时补齐一次）。
  useEffect(() => {
    const sync = () => {
      if (getThemeChoice() === "system") applyTheme("system");
    };
    sync();
    return watchSystemTheme(sync);
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="login-mark">衣</span>
          衣橱
        </div>

        {nav.map((item) => (
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
            {/* 手机上侧栏变成底部标签栏，退出按钮放到顶栏来 */}
            <button
              type="button"
              className="btn btn-sm mobile-only"
              onClick={() => void logout()}
            >
              退出
            </button>
          </div>
        </header>
        <div className="content">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
