import { useCallback, useEffect, useState, type FormEvent } from "react";

import { ApiError, api, getSessionId, type SessionInfo } from "../api/client";
import { useAuth } from "../auth/AuthProvider";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

export default function SettingsPage() {
  const { user, logoutAll, refreshUser } = useAuth();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [pwMsg, setPwMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [pwBusy, setPwBusy] = useState(false);

  const currentSessionId = getSessionId();

  const loadSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setSessions(await api.sessions());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载设备列表失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadSessions();
  }, [loadSessions]);

  async function revoke(id: number) {
    try {
      await api.revokeSession(id);
      await loadSessions();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "吊销失败");
    }
  }

  async function onChangePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPwMsg(null);
    setPwBusy(true);
    try {
      await api.changePassword(currentPassword, newPassword);
      setPwMsg({ kind: "ok", text: "密码已更新，所有设备需重新登录。" });
      setCurrentPassword("");
      setNewPassword("");
      await refreshUser();
      await loadSessions();
    } catch (err) {
      setPwMsg({ kind: "err", text: err instanceof ApiError ? err.message : "修改失败" });
    } finally {
      setPwBusy(false);
    }
  }

  return (
    <>
      <div className="card" style={{ marginBottom: 18 }}>
        <h3>账号</h3>
        <p>
          用户名 <strong>{user?.username}</strong>
          {user?.is_admin && <span className="badge badge-accent" style={{ marginLeft: 8 }}>管理员</span>}
        </p>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>登录设备</h3>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="list" style={{ marginBottom: 14 }}>
        {loading && <div className="empty">加载中…</div>}
        {!loading && sessions.length === 0 && <div className="empty">暂无活跃会话</div>}
        {!loading &&
          sessions.map((s) => (
            <div className="list-row" key={s.id}>
              <div>
                <div className="title">
                  {s.device_label || "未知设备"}
                  {s.id === currentSessionId && <span className="self-tag">当前设备</span>}
                </div>
                <div className="meta">
                  {s.ip_address || "未知 IP"} · 登录于 {formatTime(s.created_at)} · 有效期至{" "}
                  {formatTime(s.expires_at)}
                </div>
              </div>
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={s.id === currentSessionId}
                title={s.id === currentSessionId ? "当前设备请用左侧「退出登录」" : "吊销该设备"}
                onClick={() => void revoke(s.id)}
              >
                吊销
              </button>
            </div>
          ))}
      </div>

      <button type="button" className="btn btn-sm btn-danger" onClick={() => void logoutAll()}>
        登出所有设备
      </button>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "28px 0 10px" }}>修改密码</h3>
      <div className="card" style={{ maxWidth: 420 }}>
        <form onSubmit={onChangePassword}>
          {pwMsg && (
            <div className={pwMsg.kind === "ok" ? "alert" : "alert alert-error"}>{pwMsg.text}</div>
          )}
          <div className="field">
            <label htmlFor="cur-pw">当前密码</label>
            <input
              id="cur-pw"
              type="password"
              autoComplete="current-password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
            />
          </div>
          <div className="field">
            <label htmlFor="new-pw">新密码（至少 8 位）</label>
            <input
              id="new-pw"
              type="password"
              autoComplete="new-password"
              minLength={8}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
            />
          </div>
          <button type="submit" className="btn btn-primary" disabled={pwBusy}>
            {pwBusy ? "提交中…" : "更新密码"}
          </button>
          <p className="tiny" style={{ marginBottom: 0, marginTop: 10 }}>
            改密后会吊销全部会话，所有设备都需要重新登录。
          </p>
        </form>
      </div>
    </>
  );
}
