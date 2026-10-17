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
  is_active: boolean;
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

/** 注册模式。open = 自助；invite = 要邀请码；closed = 关闭。 */
export type RegistrationMode = "open" | "invite" | "closed";

/** 服务端运行信息，「关于」面板用。 */
export type Meta = {
  app_name: string;
  version: string;
  registration_mode: RegistrationMode;
  /** none / rembg */
  matting_provider: string;
  /** none / clip / vlm */
  tagger_provider: string;
  tagger_available: boolean;
  /** 当前用户有没有配好自己的大模型 */
  llm_configured: boolean;
};

/** 我自己的大模型配置（API Key 只回脱敏串）。 */
export type LlmSettings = {
  base_url: string | null;
  model: string | null;
  api_key_masked: string;
  /** 这份配置是不是管理员代设的 */
  from_admin: boolean;
  configured: boolean;
};

export type QuotaItem = { used: number; limit: number | null };
export type Quota = { garments: QuotaItem; outfits: QuotaItem; tags: QuotaItem };

export type RegisterPayload = {
  username: string;
  password: string;
  display_name?: string | null;
  email?: string | null;
  invite_code?: string | null;
};

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// ---------- 管理端类型 ----------

export type UserStats = {
  garments: number;
  outfits: number;
  tags: number;
  sessions: number;
};

export type AdminUser = {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  is_admin: boolean;
  is_active: boolean;
  created_at: string;
  last_seen_at: string | null;
  stats: UserStats;
  quotas: Quota;
  llm: LlmSettings;
};

export type AdminUserDetail = AdminUser & { sessions: SessionInfo[] };

/** 建号 / 改号时提交的字段。改号只传想改的。 */
export type AdminUserPayload = {
  username?: string;
  password?: string;
  display_name?: string | null;
  email?: string | null;
  is_admin?: boolean;
  is_active?: boolean;
  garment_quota?: number | null;
  outfit_quota?: number | null;
  tag_quota?: number | null;
  clear_garment_quota?: boolean;
  clear_outfit_quota?: boolean;
  clear_tag_quota?: boolean;
  llm_base_url?: string | null;
  llm_api_key?: string | null;
  llm_model?: string | null;
};

export type RegistrationSettings = {
  mode: RegistrationMode;
  invite_codes_usable: number;
};

export type InviteCode = {
  id: number;
  code: string;
  note: string | null;
  max_uses: number | null;
  used_count: number;
  remaining: number | null;
  expires_at: string | null;
  revoked_at: string | null;
  created_at: string;
  usable: boolean;
};

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
  // FormData 必须让浏览器自己带 boundary，手写 Content-Type 会把上传打坏。
  const isForm = typeof FormData !== "undefined" && init.body instanceof FormData;
  if (init.body !== undefined && !isForm && !headers.has("Content-Type")) {
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
    // 413 往往是网关（nginx）直接回的，body 可能是它自己的 HTML 错误页，
    // 解析不出 detail。给一句人话，别让用户对着「请求失败（413）」猜。
    const fallback =
      res.status === 413
        ? "上传的内容太大，被网关挡下了：请减少单次上传的张数（或换小一点的图）再试。"
        : `请求失败（${res.status}）`;
    throw new ApiError(res.status, extractDetail(payload, fallback));
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/**
 * 通用请求入口（供其它 api 模块复用）。
 *
 * 带 401 自动刷新重试；`allowRetry` 为 false 时不再递归。
 */
export function request<T>(path: string, init: RequestInit = {}, allowRetry = true): Promise<T> {
  return doRequest<T>(path, init, allowRetry);
}

async function doRequest<T>(path: string, init: RequestInit, allowRetry: boolean): Promise<T> {
  try {
    return await raw<T>(path, init, true);
  } catch (err) {
    if (err instanceof ApiError && err.status === 401 && allowRetry) {
      if (await tryRefresh()) return doRequest<T>(path, init, false);
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
  /** 公开的注册策略（不需要登录）—— 登录页决定要不要显示注册入口。 */
  registrationPolicy: () =>
    raw<{ mode: RegistrationMode; invite_required: boolean }>(
      "/auth/registration",
      { method: "GET" },
      false,
    ),

  login: (username: string, password: string, deviceLabel?: string) =>
    raw<TokenResponse>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password, device_label: deviceLabel ?? null }) },
      false,
    ),

  /** 自助注册。要不要邀请码由服务端的注册模式决定，前端只管把码带上。 */
  register: (payload: RegisterPayload) =>
    raw<TokenResponse>(
      "/auth/register",
      { method: "POST", body: JSON.stringify({ ...payload, device_label: null }) },
      false,
    ),

  logout: (refreshToken: string) =>
    raw<{ ok: boolean }>(
      "/auth/logout",
      { method: "POST", body: JSON.stringify({ refresh_token: refreshToken }) },
      false,
    ),

  me: () => request<User>("/auth/me"),

  /** 改自己的资料。目前只有显示名；传 null / 空串表示清空（显示时回落到用户名）。 */
  updateProfile: (payload: { display_name?: string | null }) =>
    request<User>("/auth/me", { method: "PATCH", body: JSON.stringify(payload) }),

  /** 版本号、AI 能力是否就绪 —— 「关于」面板用。 */
  meta: () => request<Meta>("/meta"),

  // ---------- 自己的大模型配置 ----------
  myLlm: () => request<LlmSettings>("/auth/llm"),

  /** apiKey 不传 = 保持不变；传空串 = 清空。 */
  saveMyLlm: (payload: { base_url: string | null; model: string | null; api_key?: string }) =>
    request<LlmSettings>("/auth/llm", { method: "PUT", body: JSON.stringify(payload) }),

  clearMyLlm: () => request<LlmSettings>("/auth/llm", { method: "DELETE" }),

  myQuota: () => request<Quota>("/auth/quota"),

  logoutAll: () => request<{ revoked: number }>("/auth/logout-all", { method: "POST" }),

  sessions: () => request<SessionInfo[]>("/auth/sessions"),

  revokeSession: (id: number) => request<{ ok: boolean }>(`/auth/sessions/${id}`, { method: "DELETE" }),

  changePassword: (currentPassword: string, newPassword: string) =>
    request<{ revoked: number }>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
    }),
};

/** 管理端接口。全部要求管理员身份，非管理员会拿到 403。 */
export const adminApi = {
  users: () => request<AdminUser[]>("/admin/users"),

  user: (id: number) => request<AdminUserDetail>(`/admin/users/${id}`),

  createUser: (payload: AdminUserPayload) =>
    request<AdminUserDetail>("/admin/users", { method: "POST", body: JSON.stringify(payload) }),

  updateUser: (id: number, payload: AdminUserPayload) =>
    request<AdminUserDetail>(`/admin/users/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),

  deleteUser: (id: number) =>
    request<{ ok: boolean; username: string; deleted_files: number }>(`/admin/users/${id}`, {
      method: "DELETE",
    }),

  resetPassword: (id: number, newPassword: string) =>
    request<{ revoked: number }>(`/admin/users/${id}/password`, {
      method: "POST",
      body: JSON.stringify({ new_password: newPassword }),
    }),

  forceLogout: (id: number) =>
    request<{ revoked: number }>(`/admin/users/${id}/logout`, { method: "POST" }),

  setUserLlm: (id: number, payload: { base_url: string | null; model: string | null; api_key?: string }) =>
    request<AdminUserDetail>(`/admin/users/${id}/llm`, {
      method: "PUT",
      body: JSON.stringify(payload),
    }),

  clearUserLlm: (id: number) =>
    request<AdminUserDetail>(`/admin/users/${id}/llm`, { method: "DELETE" }),

  settings: () => request<RegistrationSettings>("/admin/settings"),

  updateSettings: (mode: RegistrationMode) =>
    request<RegistrationSettings>("/admin/settings", {
      method: "PATCH",
      body: JSON.stringify({ mode }),
    }),

  inviteCodes: () => request<InviteCode[]>("/admin/invite-codes"),

  createInviteCodes: (payload: {
    count?: number;
    note?: string | null;
    max_uses?: number | null;
    expires_in_days?: number | null;
  }) =>
    request<InviteCode[]>("/admin/invite-codes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  revokeInviteCode: (id: number) =>
    request<InviteCode>(`/admin/invite-codes/${id}`, { method: "DELETE" }),
};
