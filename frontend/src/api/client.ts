/**
 * API 客户端。
 *
 * 令牌策略：
 * - access token 只放内存（XSS 拿不到持久副本）；
 * - refresh token 放 localStorage，因为它要跨刷新存活。生产若上 https，
 *   可改为 httpOnly cookie —— 见 docs/开发说明.md 的「后续加固」。
 * - 收到 401 自动尝试刷新一次，失败则触发登出回调。
 */

export type User = {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
};

export type TokenResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_at: string;
  session_id: number;
  user: User;
};

export type SessionInfo = {
  id: number;
  device_label: string | null;
  user_agent: string | null;
  ip_address: string | null;
  created_at: string;
  last_seen_at: string | null;
  expires_at: string;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

const REFRESH_KEY = "wardrobe.refresh_token";
const SESSION_KEY = "wardrobe.session_id";

let accessToken: string | null = null;
let authLostHandler: (() => void) | null = null;

export function setAuthLostHandler(fn: (() => void) | null): void {
  authLostHandler = fn;
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}

export function getSessionId(): number | null {
  const raw = localStorage.getItem(SESSION_KEY);
  if (!raw) return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export function setTokens(t: Pick<TokenResponse, "access_token" | "refresh_token" | "session_id">): void {
  accessToken = t.access_token;
  localStorage.setItem(REFRESH_KEY, t.refresh_token);
  localStorage.setItem(SESSION_KEY, String(t.session_id));
}

export function clearTokens(): void {
  accessToken = null;
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(SESSION_KEY);
}

function extractDetail(body: unknown, fallback: string): string {
  if (typeof body !== "object" || body === null) return fallback;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const first = detail[0] as { msg?: unknown } | undefined;
    if (first && typeof first.msg === "string") return first.msg;
  }
  return fallback;
}

async function raw<T>(path: string, init: RequestInit, withAuth: boolean): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (withAuth && accessToken) {
    headers.set("Authorization", `Bearer ${accessToken}`);
  }

  const res = await fetch(`/api${path}`, { ...init, headers });
  if (!res.ok) {
    let payload: unknown = null;
    try {
      payload = await res.json();
    } catch {
      /* 非 JSON 响应，用默认文案 */
    }
    throw new ApiError(res.status, extractDetail(payload, `请求失败（${res.status}）`));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

async function request<T>(path: string, init: RequestInit = {}, allowRetry = true): Promise<T> {
  try {
    return await raw<T>(path, init, true);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && allowRetry) {
      if (await tryRefresh()) return request<T>(path, init, false);
      authLostHandler?.();
    }
    throw err;
  }
}

let refreshInFlight: Promise<boolean> | null = null;

async function performRefresh(): Promise<boolean> {
  const used = getRefreshToken();
  if (!used) return false;

  try {
    const data = await raw<TokenResponse>(
      "/auth/refresh",
      { method: "POST", body: JSON.stringify({ refresh_token: used }) },
      false,
    );
    setTokens(data);
    return true;
  } catch {
    // 多标签页竞争：另一个标签页可能刚好换过令牌，localStorage 里已是新的。
    // 用新令牌再试一次，避免无谓地把用户踢下线。
    const latest = getRefreshToken();
    if (latest && latest !== used) {
      try {
        const data = await raw<TokenResponse>(
          "/auth/refresh",
          { method: "POST", body: JSON.stringify({ refresh_token: latest }) },
          false,
        );
        setTokens(data);
        return true;
      } catch {
        /* 落到下面统一清理 */
      }
    }
    clearTokens();
    return false;
  }
}

/**
 * 刷新访问令牌。
 *
 * 同一时刻只会真正发出一个请求 —— React StrictMode 在开发模式下会把 effect 跑两遍，
 * 若不去重，两个并发刷新会各自建一个会话（服务端轮换是原子的，落败方会拿到 401）。
 */
export function tryRefresh(): Promise<boolean> {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
}

export const api = {
  login: (username: string, password: string, deviceLabel?: string) =>
    raw<TokenResponse>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password, device_label: deviceLabel ?? null }) },
      false,
    ),

  logout: (refreshToken: string) =>
    raw<{ ok: boolean }>(
      "/auth/logout",
      { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) },
      false,
    ),

  me: () => request<User>("/auth/me"),

  logoutAll: () => request<{ revoked: number }>("/auth/logout-all", { method: "POST" }),

  sessions: () => request<SessionInfo[]>("/auth/sessions"),

  revokeSession: (id: number) => request<{ ok: boolean }>(`/auth/sessions/${id}`, { method: "DELETE" }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ revoked: number }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
};
