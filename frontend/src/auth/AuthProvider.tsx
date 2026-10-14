import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  api,
  clearTokens,
  getRefreshToken,
  setAuthLostHandler,
  setTokens,
  tryRefresh,
  type User,
} from "../api/client";

type AuthState = {
  user: User | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  logoutAll: () => Promise<void>;
  refreshUser: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  // 刷新令牌失效时，客户端会回调这里把 UI 打回登录态
  useEffect(() => {
    setAuthLostHandler(() => setUser(null));
    return () => setAuthLostHandler(null);
  }, []);

  // 冷启动：本地有刷新令牌就静默恢复会话
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      if (getRefreshToken() && (await tryRefresh())) {
        try {
          const me = await api.me();
          if (!cancelled) setUser(me);
        } catch {
          clearTokens();
        }
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const data = await api.login(username, password);
    setTokens(data);
    setUser(data.user);
  }, []);

  const logout = useCallback(async () => {
    const rt = getRefreshToken();
    if (rt) {
      try {
        await api.logout(rt);
      } catch {
        /* 本地登出不因网络失败而中断 */
      }
    }
    clearTokens();
    setUser(null);
  }, []);

  const logoutAll = useCallback(async () => {
    try {
      await api.logoutAll();
    } catch {
      /* 同上 */
    }
    clearTokens();
    setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    setUser(await api.me());
  }, []);

  const value = useMemo<AuthState>(
    () => ({ user, ready, login, logout, logoutAll, refreshUser }),
    [user, ready, login, logout, logoutAll, refreshUser],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth 必须在 AuthProvider 内使用");
  return ctx;
}
