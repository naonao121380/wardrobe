import { useEffect, useState, type FormEvent } from "react";

import { ApiError, api, type RegistrationMode } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

type Tab = "login" | "register";

const MODE_HINT: Record<RegistrationMode, string> = {
  open: "当前开放自助注册，直接注册即可使用。",
  invite: "当前为邀请码注册，请向管理员索取邀请码。",
  closed: "当前未开放自助注册，账号由管理员创建。",
};

export default function LoginPage() {
  const { login, register } = useAuth();

  const [tab, setTab] = useState<Tab>("login");
  const [mode, setMode] = useState<RegistrationMode | null>(null);

  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [inviteCode, setInviteCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // 注册开不开是**服务端**说了算（管理员在界面上随时可改），所以先问一次
  useEffect(() => {
    api
      .registrationPolicy()
      .then((p) => setMode(p.mode))
      .catch(() => setMode("closed")); // 拿不到就按最保守的来：不显示注册
  }, []);

  const canRegister = mode === "open" || mode === "invite";
  const needInvite = mode === "invite";

  function switchTab(next: Tab) {
    setTab(next);
    setError(null);
  }

  async function onLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "登录失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  async function onRegister(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await register({
        username: username.trim(),
        password,
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        invite_code: needInvite ? inviteCode.trim() : null,
      });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "注册失败，请稍后重试");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login-wrap">
      <form
        className="login-card"
        onSubmit={tab === "login" ? onLogin : onRegister}
        data-mode={tab}
      >
        <div className="login-brand">
          <span className="login-mark">衣</span>
          <h1>衣橱</h1>
        </div>
        <p className="sub">
          {tab === "login" ? "登录后管理你的衣物与搭配" : "注册一个属于你的衣橱"}
        </p>

        {canRegister && (
          <div className="auth-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={tab === "login"}
              className={`auth-tab${tab === "login" ? " on" : ""}`}
              onClick={() => switchTab("login")}
            >
              登录
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === "register"}
              className={`auth-tab${tab === "register" ? " on" : ""}`}
              onClick={() => switchTab("register")}
            >
              注册
            </button>
          </div>
        )}

        {error && <div className="alert alert-error">{error}</div>}

        <div className="field">
          <label htmlFor="username">用户名</label>
          <input
            id="username"
            autoComplete="username"
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
          {tab === "register" && (
            <span className="tiny">3–64 位，字母 / 数字 / . _ -，注册后不可修改</span>
          )}
        </div>

        <div className="field">
          <label htmlFor="password">密码</label>
          <input
            id="password"
            type="password"
            autoComplete={tab === "login" ? "current-password" : "new-password"}
            minLength={tab === "register" ? 8 : undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
          {tab === "register" && <span className="tiny">至少 8 位</span>}
        </div>

        {tab === "register" && (
          <>
            <div className="field">
              <label htmlFor="display-name-reg">显示名（可选）</label>
              <input
                id="display-name-reg"
                value={displayName}
                maxLength={64}
                placeholder="不填就用用户名"
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="email-reg">邮箱（可选）</label>
              <input
                id="email-reg"
                type="email"
                autoComplete="email"
                value={email}
                maxLength={255}
                onChange={(e) => setEmail(e.target.value)}
              />
            </div>

            {needInvite && (
              <div className="field">
                <label htmlFor="invite-code">邀请码</label>
                <input
                  id="invite-code"
                  value={inviteCode}
                  maxLength={64}
                  placeholder="XXXX-XXXX"
                  autoComplete="off"
                  required
                  onChange={(e) => setInviteCode(e.target.value.toUpperCase())}
                />
                <span className="tiny">大小写与短横不敏感</span>
              </div>
            )}
          </>
        )}

        <button type="submit" className="btn btn-primary btn-block" disabled={busy}>
          {busy ? (tab === "login" ? "登录中…" : "注册中…") : tab === "login" ? "登录" : "注册并进入"}
        </button>

        {mode && (
          <p className="tiny login-hint">
            {tab === "login" && canRegister
              ? "第一次来？切到上面的「注册」。"
              : MODE_HINT[mode]}
          </p>
        )}
      </form>
    </div>
  );
}
