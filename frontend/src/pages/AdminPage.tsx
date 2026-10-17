import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  adminApi,
  type AdminUser,
  type AdminUserDetail,
  type AdminUserPayload,
  type InviteCode,
  type RegistrationMode,
  type RegistrationSettings,
} from "../api/client";
import Dialog from "../components/Dialog";
import { confirmDialog } from "../lib/confirm";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** 用量展示：`12 / 50`，不限时只显示数字。 */
function used(usedCount: number, limit: number | null): string {
  return limit === null ? `${usedCount}` : `${usedCount} / ${limit}`;
}

/** 这一格的用量是不是已经顶到上限了（顶到了标黄，提醒管理员该放配额了）。 */
function atLimit(usedCount: number, limit: number | null): boolean {
  return limit !== null && usedCount >= limit;
}

const MODE_OPTIONS: { value: RegistrationMode; label: string; desc: string }[] = [
  { value: "open", label: "无限制自助注册", desc: "任何人打开登录页都能注册" },
  { value: "invite", label: "邀请码注册", desc: "必须带一枚可用的邀请码" },
  { value: "closed", label: "关闭注册", desc: "只能由管理员创建账号" },
];

export default function AdminPage() {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [settings, setSettings] = useState<RegistrationSettings | null>(null);
  const [codes, setCodes] = useState<InviteCode[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailId, setDetailId] = useState<number | null>(null);
  const [creating, setCreating] = useState(false);
  // 删掉用户之后弹窗会关掉，提示得放在页面上（不能用 window.alert —— 全站不弹原生框）
  const [toast, setToast] = useState<string | null>(null);

  const loadAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [u, s, c] = await Promise.all([
        adminApi.users(),
        adminApi.settings(),
        adminApi.inviteCodes(),
      ]);
      setUsers(u);
      setSettings(s);
      setCodes(c);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "加载管理数据失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadAll();
  }, [loadAll]);

  async function changeMode(mode: RegistrationMode) {
    if (!settings || settings.mode === mode) return;
    setError(null);
    try {
      setSettings(await adminApi.updateSettings(mode));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "切换注册模式失败");
    }
  }

  return (
    <>
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="admin-head">
          <div>
            <h3 style={{ margin: 0 }}>用户与权限</h3>
            <p className="tiny" style={{ margin: "4px 0 0" }}>
              管理员可以查看每个用户的用量、重置密码、调整配额，并在用户没配模型时替他配好。
            </p>
          </div>
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            新建用户
          </button>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {toast && <div className="alert">{toast}</div>}

        <div className="table-wrap">
          <table className="table admin-table">
            <thead>
              <tr>
                <th>用户</th>
                <th>角色</th>
                <th>状态</th>
                <th>衣物</th>
                <th>搭配</th>
                <th>标签</th>
                <th>大模型</th>
                <th>最近活跃</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {loading && (
                <tr>
                  <td colSpan={9} className="empty">
                    加载中…
                  </td>
                </tr>
              )}
              {!loading && users.length === 0 && (
                <tr>
                  <td colSpan={9} className="empty">
                    还没有任何用户
                  </td>
                </tr>
              )}
              {users.map((u) => (
                <tr key={u.id} className={u.is_active ? "" : "row-off"}>
                  <td>
                    <div className="title">
                      {u.display_name || u.username}
                      {u.llm.from_admin && <span className="self-tag">代设</span>}
                    </div>
                    <div className="meta">
                      {u.username}
                      {u.email ? ` · ${u.email}` : ""}
                    </div>
                  </td>
                  <td>{u.is_admin ? <span className="badge badge-accent">管理员</span> : "普通用户"}</td>
                  <td>
                    {u.is_active ? (
                      "正常"
                    ) : (
                      <span className="badge badge-danger">已停用</span>
                    )}
                  </td>
                  <td className={atLimit(u.stats.garments, u.quotas.garments.limit) ? "cell-warn" : ""}>
                    {used(u.stats.garments, u.quotas.garments.limit)}
                  </td>
                  <td className={atLimit(u.stats.outfits, u.quotas.outfits.limit) ? "cell-warn" : ""}>
                    {used(u.stats.outfits, u.quotas.outfits.limit)}
                  </td>
                  <td className={atLimit(u.stats.tags, u.quotas.tags.limit) ? "cell-warn" : ""}>
                    {used(u.stats.tags, u.quotas.tags.limit)}
                  </td>
                  <td>
                    <span className={`about-dot ${u.llm.configured ? "ok" : "off"}`} aria-hidden="true" />
                    {u.llm.configured ? u.llm.model || "已配置" : "未配置"}
                  </td>
                  <td className="tiny">{formatTime(u.last_seen_at)}</td>
                  <td>
                    <button
                      type="button"
                      className="btn btn-sm"
                      onClick={() => setDetailId(u.id)}
                    >
                      管理
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div className="admin-columns">
        <div className="card">
          <h3>注册设置</h3>
          <div className="mode-list">
            {MODE_OPTIONS.map((opt) => (
              <button
                type="button"
                key={opt.value}
                className={`mode-item${settings?.mode === opt.value ? " on" : ""}`}
                aria-pressed={settings?.mode === opt.value}
                onClick={() => void changeMode(opt.value)}
              >
                <span className="mode-radio" aria-hidden="true" />
                <span>
                  <strong>{opt.label}</strong>
                  <span className="tiny">{opt.desc}</span>
                </span>
              </button>
            ))}
          </div>
          {settings?.mode === "invite" && settings.invite_codes_usable === 0 && (
            <div className="alert alert-error">
              当前是邀请码注册，但<strong>没有可用的邀请码</strong> —— 谁都注册不了。先在下面生成几枚。
            </div>
          )}
        </div>

        <InviteCodesCard codes={codes} onChanged={loadAll} />
      </div>

      {creating && (
        <UserFormDialog
          onClose={() => setCreating(false)}
          onDone={() => {
            setCreating(false);
            void loadAll();
          }}
        />
      )}

      {detailId !== null && (
        <UserDetailDialog
          userId={detailId}
          onClose={() => setDetailId(null)}
          onChanged={() => void loadAll()}
          onDeleted={(text) => {
            setDetailId(null);
            setToast(text);
            void loadAll();
          }}
        />
      )}
    </>
  );
}

// ---------- 邀请码 ----------

function InviteCodesCard({ codes, onChanged }: { codes: InviteCode[]; onChanged: () => void }) {
  const [count, setCount] = useState("1");
  const [note, setNote] = useState("");
  const [maxUses, setMaxUses] = useState("");
  const [days, setDays] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [fresh, setFresh] = useState<InviteCode[]>([]);
  const [copied, setCopied] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      const made = await adminApi.createInviteCodes({
        count: Math.max(1, Math.min(50, Number(count) || 1)),
        note: note.trim() || null,
        max_uses: maxUses.trim() ? Math.max(1, Number(maxUses) || 1) : null,
        expires_in_days: days.trim() ? Math.max(1, Number(days) || 1) : null,
      });
      setFresh(made);
      setNote("");
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "生成失败");
    } finally {
      setBusy(false);
    }
  }

  async function revoke(code: InviteCode) {
    const ok = await confirmDialog({
      title: "吊销这枚邀请码？",
      message: `吊销后「${code.code}」立刻失效，已经用它注册过的账号不受影响。`,
      confirmLabel: "吊销",
      danger: true,
    });
    if (!ok) return;
    try {
      await adminApi.revokeInviteCode(code.id);
      setFresh((prev) => prev.filter((c) => c.id !== code.id));
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "吊销失败");
    }
  }

  async function copy(code: string) {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(code);
      window.setTimeout(() => setCopied(null), 1500);
    } catch {
      setErr("浏览器不允许访问剪贴板，请手动选中复制");
    }
  }

  return (
    <div className="card">
      <h3>邀请码</h3>
      <form onSubmit={submit}>
        {err && <div className="alert alert-error">{err}</div>}
        <div className="admin-form-grid">
          <div className="field">
            <label htmlFor="code-count">数量</label>
            <input
              id="code-count"
              type="number"
              min={1}
              max={50}
              value={count}
              onChange={(e) => setCount(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="code-uses">每枚可用次数</label>
            <input
              id="code-uses"
              type="number"
              min={1}
              placeholder="留空 = 不限"
              value={maxUses}
              onChange={(e) => setMaxUses(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="code-days">有效天数</label>
            <input
              id="code-days"
              type="number"
              min={1}
              placeholder="留空 = 永久"
              value={days}
              onChange={(e) => setDays(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="code-note">备注</label>
            <input
              id="code-note"
              maxLength={128}
              placeholder="给小王 / 秋招"
              value={note}
              onChange={(e) => setNote(e.target.value)}
            />
          </div>
        </div>
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "生成中…" : "生成"}
        </button>
      </form>

      {fresh.length > 0 && (
        <div className="code-fresh">
          <p className="tiny" style={{ marginTop: 0 }}>
            刚生成的（点一下复制）：
          </p>
          <div className="code-chips">
            {fresh.map((c) => (
              <button
                type="button"
                key={c.id}
                className="code-chip"
                title="点击复制"
                onClick={() => void copy(c.code)}
              >
                {copied === c.code ? "已复制" : c.code}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="list" style={{ marginTop: 12 }}>
        {codes.length === 0 && <div className="empty">还没有生成过邀请码</div>}
        {codes.map((c) => (
          <div className="list-row" key={c.id}>
            <div>
              <div className="title">
                <span className="mono">{c.code}</span>
                {!c.usable && <span className="self-tag">已失效</span>}
              </div>
              <div className="meta">
                {c.note ? `${c.note} · ` : ""}
                已用 {c.used_count}
                {c.remaining === null ? "（不限次）" : ` / ${c.used_count + c.remaining} 次`}
                {c.expires_at ? ` · 有效至 ${formatTime(c.expires_at)}` : " · 永久有效"}
                {" · "}
                {formatTime(c.created_at)}
              </div>
            </div>
            <div className="row-end">
              <button type="button" className="btn btn-sm" onClick={() => void copy(c.code)}>
                复制
              </button>
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={!c.usable}
                onClick={() => void revoke(c)}
              >
                吊销
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ---------- 新建用户 ----------

function UserFormDialog({ onClose, onDone }: { onClose: () => void; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [isAdmin, setIsAdmin] = useState(false);
  const [garmentQuota, setGarmentQuota] = useState("");
  const [outfitQuota, setOutfitQuota] = useState("");
  const [tagQuota, setTagQuota] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setErr(null);
    setBusy(true);
    try {
      await adminApi.createUser({
        username: username.trim(),
        password,
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        is_admin: isAdmin,
        garment_quota: garmentQuota.trim() ? Number(garmentQuota) : null,
        outfit_quota: outfitQuota.trim() ? Number(outfitQuota) : null,
        tag_quota: tagQuota.trim() ? Number(tagQuota) : null,
        llm_base_url: llmBaseUrl.trim() || null,
        llm_model: llmModel.trim() || null,
        llm_api_key: llmApiKey.trim() || null,
      });
      onDone();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "创建失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog title="新建用户" onClose={onClose} width={620}>
      <form onSubmit={submit}>
        {err && <div className="alert alert-error">{err}</div>}
        <div className="field-inline">
          <div className="field">
            <label htmlFor="nu-username">用户名</label>
            <input
              id="nu-username"
              value={username}
              required
              minLength={3}
              maxLength={64}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="nu-password">初始密码（≥8 位）</label>
            <input
              id="nu-password"
              type="password"
              value={password}
              required
              minLength={8}
              autoComplete="new-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
        </div>
        <div className="field-inline">
          <div className="field">
            <label htmlFor="nu-display">显示名（可选）</label>
            <input
              id="nu-display"
              value={displayName}
              maxLength={64}
              onChange={(e) => setDisplayName(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="nu-email">邮箱（可选）</label>
            <input
              id="nu-email"
              type="email"
              value={email}
              maxLength={255}
              onChange={(e) => setEmail(e.target.value)}
            />
          </div>
        </div>

        <label className="del-check" htmlFor="nu-admin">
          <input
            id="nu-admin"
            type="checkbox"
            checked={isAdmin}
            onChange={(e) => setIsAdmin(e.target.checked)}
          />
          <span className="switch-box" />
          <span>设为管理员（可以管理用户与注册设置）</span>
        </label>

        <h4 className="form-sub">配额（留空 = 不限）</h4>
        <div className="field-inline">
          <div className="field">
            <label htmlFor="nu-garment">衣物</label>
            <input
              id="nu-garment"
              type="number"
              min={0}
              value={garmentQuota}
              onChange={(e) => setGarmentQuota(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="nu-outfit">搭配</label>
            <input
              id="nu-outfit"
              type="number"
              min={0}
              value={outfitQuota}
              onChange={(e) => setOutfitQuota(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="nu-tag">标签取值</label>
            <input
              id="nu-tag"
              type="number"
              min={0}
              value={tagQuota}
              onChange={(e) => setTagQuota(e.target.value)}
            />
          </div>
        </div>

        <h4 className="form-sub">大模型（可选，代用户设好）</h4>
        <p className="tiny" style={{ marginTop: 0, marginBottom: 12 }}>
          留空即不代设，用户可自己填。要求：<strong>视觉（多模态）模型</strong> + OpenAI 兼容接口，
          模型地址填到 <code>/v4</code> 或 <code>/v1</code> 这一层（不要带 <code>/chat/completions</code>）；
          纯文本模型不支持看图，填了也无法打标。
        </p>
        <div className="field">
          <label htmlFor="nu-llm-url">模型地址</label>
          <input
            id="nu-llm-url"
            value={llmBaseUrl}
            placeholder="https://…/v4"
            onChange={(e) => setLlmBaseUrl(e.target.value)}
          />
        </div>
        <div className="field-inline">
          <div className="field">
            <label htmlFor="nu-llm-model">模型名称</label>
            <input
              id="nu-llm-model"
              value={llmModel}
              onChange={(e) => setLlmModel(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="nu-llm-key">API Key</label>
            <input
              id="nu-llm-key"
              type="password"
              autoComplete="new-password"
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
            />
          </div>
        </div>

        <div className="modal-foot">
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? "创建中…" : "创建"}
          </button>
        </div>
      </form>
    </Dialog>
  );
}

// ---------- 用户详情 ----------

function UserDetailDialog({
  userId,
  onClose,
  onChanged,
  onDeleted,
}: {
  userId: number;
  onClose: () => void;
  onChanged: () => void;
  onDeleted: (text: string) => void;
}) {
  const [detail, setDetail] = useState<AdminUserDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [garmentQuota, setGarmentQuota] = useState("");
  const [outfitQuota, setOutfitQuota] = useState("");
  const [tagQuota, setTagQuota] = useState("");

  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");

  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirmName, setConfirmName] = useState("");

  const apply = useCallback((data: AdminUserDetail) => {
    setDetail(data);
    setDisplayName(data.display_name ?? "");
    setEmail(data.email ?? "");
    setGarmentQuota(data.quotas.garments.limit === null ? "" : String(data.quotas.garments.limit));
    setOutfitQuota(data.quotas.outfits.limit === null ? "" : String(data.quotas.outfits.limit));
    setTagQuota(data.quotas.tags.limit === null ? "" : String(data.quotas.tags.limit));
    setLlmBaseUrl(data.llm.base_url ?? "");
    setLlmModel(data.llm.model ?? "");
    setLlmApiKey("");
  }, []);

  useEffect(() => {
    setDetail(null);
    adminApi
      .user(userId)
      .then(apply)
      .catch((e) => setErr(e instanceof ApiError ? e.message : "加载失败"));
  }, [userId, apply]);

  /** 统一的「发请求 → 刷新详情 → 提示」包装。 */
  async function run(label: string, fn: () => Promise<AdminUserDetail>) {
    setMsg(null);
    setErr(null);
    setBusy(true);
    try {
      apply(await fn());
      setMsg({ kind: "ok", text: `${label}完成。` });
      onChanged();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : `${label}失败` });
    } finally {
      setBusy(false);
    }
  }

  /**
   * 配额的三态：留空 = 不限（要显式告诉后端 clear）、填数字 = 设为此值。
   * 光传 `null` 与「不传」在后端无法区分，所以这里带上 clear_* 开关。
   */
  function quotaPayload(): AdminUserPayload {
    const out: AdminUserPayload = {};
    if (garmentQuota.trim() === "") out.clear_garment_quota = true;
    else out.garment_quota = Math.max(0, Number(garmentQuota) || 0);
    if (outfitQuota.trim() === "") out.clear_outfit_quota = true;
    else out.outfit_quota = Math.max(0, Number(outfitQuota) || 0);
    if (tagQuota.trim() === "") out.clear_tag_quota = true;
    else out.tag_quota = Math.max(0, Number(tagQuota) || 0);
    return out;
  }

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await run("保存资料", () =>
      adminApi.updateUser(userId, {
        display_name: displayName.trim() || null,
        email: email.trim() || null,
        ...quotaPayload(),
      }),
    );
  }

  async function toggleRole(next: boolean) {
    const ok = await confirmDialog({
      title: next ? "设为管理员？" : "取消管理员权限？",
      message: next
        ? `${detail?.username} 将可以查看并管理所有用户。`
        : `${detail?.username} 将失去管理入口。`,
      confirmLabel: next ? "设为管理员" : "取消权限",
      danger: !next,
    });
    if (!ok) return;
    await run(next ? "设为管理员" : "取消管理员", () =>
      adminApi.updateUser(userId, { is_admin: next }),
    );
  }

  async function toggleActive(next: boolean) {
    const ok = await confirmDialog({
      title: next ? "启用该账号？" : "停用该账号？",
      message: next
        ? `${detail?.username} 可以重新登录。`
        : `停用后 ${detail?.username} 无法登录，已登录的设备会被立即踢下线（数据保留）。`,
      confirmLabel: next ? "启用" : "停用",
      danger: !next,
    });
    if (!ok) return;
    await run(next ? "启用" : "停用", () => adminApi.updateUser(userId, { is_active: next }));
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMsg(null);
    setErr(null);
    setBusy(true);
    try {
      const res = await adminApi.resetPassword(userId, newPassword);
      setNewPassword("");
      setMsg({
        kind: "ok",
        text: `密码已重置${res.revoked ? `，并踢下线 ${res.revoked} 台设备` : ""}。`,
      });
      apply(await adminApi.user(userId));
      onChanged();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "重置失败" });
    } finally {
      setBusy(false);
    }
  }

  async function forceLogout() {
    const ok = await confirmDialog({
      title: "把所有设备踢下线？",
      message: `${detail?.username} 的登录状态会全部失效，需要重新登录。`,
      confirmLabel: "全部踢下线",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    try {
      const res = await adminApi.forceLogout(userId);
      setMsg({ kind: "ok", text: `已踢下线 ${res.revoked} 台设备。` });
      apply(await adminApi.user(userId));
      onChanged();
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "操作失败" });
    } finally {
      setBusy(false);
    }
  }

  async function removeUser() {
    if (!detail || confirmName.trim() !== detail.username) return;
    setBusy(true);
    try {
      const res = await adminApi.deleteUser(userId);
      onDeleted(`已删除用户 ${res.username}，并清理了 ${res.deleted_files} 个媒体文件。`);
    } catch (e) {
      setMsg({ kind: "err", text: e instanceof ApiError ? e.message : "删除失败" });
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={detail ? `管理用户 · ${detail.username}` : "加载中…"}
      onClose={onClose}
      width={720}
      className="modal-admin"
    >
      {err && <div className="alert alert-error">{err}</div>}
      {!detail && !err && <p className="tiny">正在读取用户信息…</p>}
      {detail && (
        <>
          {msg && (
            <div className={msg.kind === "ok" ? "alert" : "alert alert-error"}>{msg.text}</div>
          )}

          <div className="admin-badges">
            {detail.is_admin ? (
              <span className="badge badge-accent">管理员</span>
            ) : (
              <span className="badge">普通用户</span>
            )}
            {!detail.is_active && <span className="badge badge-danger">已停用</span>}
            <span className="tiny">
              建于 {formatTime(detail.created_at)} · 最近活跃 {formatTime(detail.last_seen_at)}
            </span>
          </div>

          <div className="stat-grid admin-stats">
            <div className="stat-card">
              <span className="stat-num">{detail.stats.garments}</span>
              <span className="stat-label">衣物 / 上限 {detail.quotas.garments.limit ?? "不限"}</span>
            </div>
            <div className="stat-card">
              <span className="stat-num">{detail.stats.outfits}</span>
              <span className="stat-label">搭配 / 上限 {detail.quotas.outfits.limit ?? "不限"}</span>
            </div>
            <div className="stat-card">
              <span className="stat-num">{detail.stats.tags}</span>
              <span className="stat-label">标签 / 上限 {detail.quotas.tags.limit ?? "不限"}</span>
            </div>
          </div>

          <form onSubmit={saveProfile}>
            <h4 className="form-sub">资料与配额</h4>
            <div className="field-inline">
              <div className="field">
                <label htmlFor="du-display">显示名</label>
                <input
                  id="du-display"
                  value={displayName}
                  maxLength={64}
                  placeholder={detail.username}
                  onChange={(e) => setDisplayName(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="du-email">邮箱</label>
                <input
                  id="du-email"
                  type="email"
                  value={email}
                  maxLength={255}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>
            <div className="field-inline">
              <div className="field">
                <label htmlFor="du-garment">衣物上限</label>
                <input
                  id="du-garment"
                  type="number"
                  min={0}
                  placeholder="不限"
                  value={garmentQuota}
                  onChange={(e) => setGarmentQuota(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="du-outfit">搭配上限</label>
                <input
                  id="du-outfit"
                  type="number"
                  min={0}
                  placeholder="不限"
                  value={outfitQuota}
                  onChange={(e) => setOutfitQuota(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="du-tag">标签上限</label>
                <input
                  id="du-tag"
                  type="number"
                  min={0}
                  placeholder="不限"
                  value={tagQuota}
                  onChange={(e) => setTagQuota(e.target.value)}
                />
              </div>
            </div>
            <button type="submit" className="btn btn-primary" disabled={busy}>
              保存资料与配额
            </button>
          </form>

          <h4 className="form-sub">角色与状态</h4>
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => void toggleRole(!detail.is_admin)}
            >
              {detail.is_admin ? "取消管理员" : "设为管理员"}
            </button>
            <button
              type="button"
              className="btn btn-sm"
              disabled={busy}
              onClick={() => void toggleActive(!detail.is_active)}
            >
              {detail.is_active ? "停用账号" : "启用账号"}
            </button>
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void forceLogout()}>
              踢下线（{detail.stats.sessions}）
            </button>
          </div>

          <h4 className="form-sub">大模型（用户没填时由你代设）</h4>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void run("保存模型配置", () =>
                adminApi.setUserLlm(userId, {
                  base_url: llmBaseUrl.trim() || null,
                  model: llmModel.trim() || null,
                  ...(llmApiKey.trim() ? { api_key: llmApiKey.trim() } : {}),
                }),
              );
            }}
          >
            <p className="tiny" style={{ marginTop: 0 }}>
              要求：<strong>视觉（多模态）模型</strong> + OpenAI 兼容接口，地址填到{" "}
              <code>/v4</code> 或 <code>/v1</code> 这一层；纯文本模型不支持看图。
            </p>
            <p className="tiny" style={{ marginTop: 0 }}>
              当前：
              <span className={`about-dot ${detail.llm.configured ? "ok" : "off"}`} aria-hidden="true" />
              {detail.llm.configured
                ? `${detail.llm.base_url} / ${detail.llm.model}${detail.llm.api_key_masked ? ` · ${detail.llm.api_key_masked}` : ""}`
                : "未配置"}
              {detail.llm.from_admin && " · 管理员代设"}
            </p>
            <div className="field">
              <label htmlFor="du-llm-url">模型地址</label>
              <input
                id="du-llm-url"
                value={llmBaseUrl}
                placeholder="https://…/v4"
                onChange={(e) => setLlmBaseUrl(e.target.value)}
              />
            </div>
            <div className="field-inline">
              <div className="field">
                <label htmlFor="du-llm-model">模型名称</label>
                <input
                  id="du-llm-model"
                  value={llmModel}
                  onChange={(e) => setLlmModel(e.target.value)}
                />
              </div>
              <div className="field">
                <label htmlFor="du-llm-key">API Key</label>
                <input
                  id="du-llm-key"
                  type="password"
                  autoComplete="new-password"
                  placeholder={detail.llm.api_key_masked ? "留空 = 不改" : ""}
                  value={llmApiKey}
                  onChange={(e) => setLlmApiKey(e.target.value)}
                />
              </div>
            </div>
            <div className="form-actions">
              <button
                type="button"
                className="btn btn-sm btn-danger"
                disabled={busy || !detail.llm.configured}
                onClick={() => void run("清除模型配置", () => adminApi.clearUserLlm(userId))}
              >
                清除配置
              </button>
              <button type="submit" className="btn btn-primary" disabled={busy}>
                保存模型配置
              </button>
            </div>
          </form>

          <h4 className="form-sub">重置密码</h4>
          <form onSubmit={resetPassword}>
            <div className="field-inline">
              <div className="field">
                <label htmlFor="du-newpw">新密码（≥8 位）</label>
                <input
                  id="du-newpw"
                  type="password"
                  minLength={8}
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  required
                />
              </div>
              <button type="submit" className="btn btn-primary" disabled={busy || newPassword.length < 8}>
                重置密码
              </button>
            </div>
            <p className="tiny" style={{ marginTop: 0 }}>
              无需原密码；重置后会吊销该用户的全部登录设备。
            </p>
          </form>

          <h4 className="form-sub">登录设备</h4>
          <div className="list">
            {detail.sessions.length === 0 && <div className="empty">没有活跃会话</div>}
            {detail.sessions.map((s) => (
              <div className="list-row" key={s.id}>
                <div>
                  <div className="title">{s.device_label || "未知设备"}</div>
                  <div className="meta">
                    {s.ip_address || "未知 IP"} · 登录于 {formatTime(s.created_at)} · 有效至{" "}
                    {formatTime(s.expires_at)}
                  </div>
                </div>
                <span className="tiny">{formatTime(s.last_seen_at)}</span>
              </div>
            ))}
          </div>

          <h4 className="form-sub danger-title">危险操作</h4>
          <div className="danger-zone">
            <p className="tiny" style={{ marginTop: 0 }}>
              删除会<strong>连带清空</strong>该用户的衣物、搭配、标签、登录设备与媒体文件，
              不可恢复。请输入用户名 <span className="mono">{detail.username}</span> 确认：
            </p>
            <div className="field-inline">
              <div className="field">
                <label htmlFor="du-confirm">用户名</label>
                <input
                  id="du-confirm"
                  value={confirmName}
                  autoComplete="off"
                  onChange={(e) => setConfirmName(e.target.value)}
                />
              </div>
              <button
                type="button"
                className="btn btn-danger"
                disabled={busy || confirmName.trim() !== detail.username}
                onClick={() => void removeUser()}
              >
                删除用户
              </button>
            </div>
          </div>
        </>
      )}
    </Dialog>
  );
}
