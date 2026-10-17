import { useCallback, useEffect, useState, type FormEvent } from "react";

import {
  ApiError,
  api,
  getSessionId,
  type LlmSettings,
  type Meta,
  type Quota,
  type RegistrationMode,
  type SessionInfo,
} from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { getThemeChoice, setThemeChoice, type ThemeChoice } from "../lib/theme";

function formatTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", { hour12: false });
}

/** provider 名 → 人话。没配时说明清楚，别让用户以为坏了。 */
const MATTING_LABEL: Record<string, string> = {
  none: "未启用（跳过抠图，只存原图）",
  rembg: "rembg 本地抠图",
};
const TAGGER_LABEL: Record<string, string> = {
  none: "未配置（命名与打标手动完成）",
  clip: "CLIP 零样本",
  vlm: "视觉大模型（VLM）",
};

const MODE_LABEL: Record<RegistrationMode, string> = {
  open: "已开放自助注册",
  invite: "邀请码注册（需管理员发码）",
  closed: "未开放（账号由管理员创建）",
};

/** 配额展示：不限 / 已用 x / 上限 y。 */
function quotaText(item?: { used: number; limit: number | null }): string {
  if (!item) return "—";
  if (item.limit === null) return `${item.used} · 不限`;
  return `${item.used} / ${item.limit}`;
}

const THEME_OPTIONS: { value: ThemeChoice; label: string }[] = [
  { value: "system", label: "跟随系统" },
  { value: "light", label: "浅色" },
  { value: "dark", label: "深色" },
];

export default function SettingsPage() {
  const { user, logoutAll, refreshUser } = useAuth();
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [displayName, setDisplayName] = useState("");
  const [profileMsg, setProfileMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [profileBusy, setProfileBusy] = useState(false);

  const [theme, setTheme] = useState<ThemeChoice>(getThemeChoice);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [quota, setQuota] = useState<Quota | null>(null);

  // 我的大模型
  const [llm, setLlm] = useState<LlmSettings | null>(null);
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmModel, setLlmModel] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");
  const [llmMsg, setLlmMsg] = useState<{ kind: "ok" | "err"; text: string } | null>(null);
  const [llmBusy, setLlmBusy] = useState(false);

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

  // 版本 / AI 能力只在进页面时读一次，拿不到就静默留空
  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null));
    api.myQuota().then(setQuota).catch(() => setQuota(null));
    api
      .myLlm()
      .then((data) => {
        setLlm(data);
        setLlmBaseUrl(data.base_url ?? "");
        setLlmModel(data.model ?? "");
      })
      .catch(() => setLlm(null));
  }, []);

  useEffect(() => {
    setDisplayName(user?.display_name ?? "");
  }, [user?.display_name]);

  async function revoke(id: number) {
    try {
      await api.revokeSession(id);
      await loadSessions();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "吊销失败");
    }
  }

  async function onSaveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setProfileMsg(null);
    setProfileBusy(true);
    try {
      await api.updateProfile({ display_name: displayName.trim() || null });
      await refreshUser();
      setProfileMsg({ kind: "ok", text: "显示名已保存。" });
    } catch (err) {
      setProfileMsg({ kind: "err", text: err instanceof ApiError ? err.message : "保存失败" });
    } finally {
      setProfileBusy(false);
    }
  }

  function pickTheme(next: ThemeChoice) {
    setThemeChoice(next);
    setTheme(next);
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

  async function onSaveLlm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLlmMsg(null);
    setLlmBusy(true);
    try {
      // 密钥那格留空 = 保持原样（只改模型名时不用重新粘一遍）
      const data = await api.saveMyLlm({
        base_url: llmBaseUrl.trim() || null,
        model: llmModel.trim() || null,
        ...(llmApiKey.trim() ? { api_key: llmApiKey.trim() } : {}),
      });
      setLlm(data);
      setLlmBaseUrl(data.base_url ?? "");
      setLlmModel(data.model ?? "");
      setLlmApiKey("");
      setLlmMsg({ kind: "ok", text: data.configured ? "已保存，AI 起名与打标可以使用了。" : "已保存（地址与模型名都要填才算配置完成）。" });
      api.meta().then(setMeta).catch(() => undefined);
    } catch (err) {
      setLlmMsg({ kind: "err", text: err instanceof ApiError ? err.message : "保存失败" });
    } finally {
      setLlmBusy(false);
    }
  }

  async function onClearLlm() {
    setLlmMsg(null);
    setLlmBusy(true);
    try {
      const data = await api.clearMyLlm();
      setLlm(data);
      setLlmBaseUrl("");
      setLlmModel("");
      setLlmApiKey("");
      setLlmMsg({ kind: "ok", text: "已清除本机保存的模型配置。" });
      api.meta().then(setMeta).catch(() => undefined);
    } catch (err) {
      setLlmMsg({ kind: "err", text: err instanceof ApiError ? err.message : "清除失败" });
    } finally {
      setLlmBusy(false);
    }
  }

  const taggerReady = meta?.tagger_available ?? false;

  return (
    <>
      <div className="card" style={{ marginBottom: 18 }}>
        <h3>账号</h3>
        <p style={{ marginBottom: 12 }}>
          用户名 <strong>{user?.username}</strong>
          {user?.is_admin && (
            <span className="badge badge-accent" style={{ marginLeft: 8 }}>
              管理员
            </span>
          )}
        </p>
        <form onSubmit={onSaveProfile}>
          {profileMsg && (
            <div className={profileMsg.kind === "ok" ? "alert" : "alert alert-error"}>
              {profileMsg.text}
            </div>
          )}
          <div className="field-inline">
            <div className="field">
              <label htmlFor="display-name">显示名</label>
              <input
                id="display-name"
                value={displayName}
                maxLength={64}
                placeholder={user?.username ?? ""}
                onChange={(e) => setDisplayName(e.target.value)}
              />
            </div>
            <button
              type="submit"
              className="btn"
              disabled={profileBusy || displayName === (user?.display_name ?? "")}
            >
              {profileBusy ? "保存中…" : "保存"}
            </button>
          </div>
        </form>
        <p className="tiny" style={{ marginBottom: 0, marginTop: 8 }}>
          留空就用用户名显示。用户名是登录凭据，不可修改。
        </p>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>大模型</h3>
      <div className="card" style={{ marginBottom: 18, maxWidth: 620 }}>
        <p className="tiny" style={{ marginTop: 0 }}>
          用于给衣物和搭配自动起名、挑标签。<strong>填你自己的</strong>服务地址与密钥 ——
          每个用户各用各的，互不通用；不填就只做手动命名。
        </p>
        <div className="hint-block">
          <strong>怎么挑模型（必须是视觉模型）</strong>
          <ul>
            <li>
              只能用<strong>能看图的视觉模型</strong>（多模态 / Vision），例如
              <code>glm-4v-flash</code>、<code>qwen-vl-max</code>、<code>gpt-4o-mini</code>。
              纯文本模型（<code>glm-4-flash</code>、<code>deepseek-chat</code> 之类）填了也
              用不了 —— 程序是把图片直接发给它「看」的。
            </li>
            <li>
              接口要 <strong>OpenAI 兼容</strong>：程序会请求
              <code>{"<地址>"}/chat/completions</code>，所以地址填到 <code>/v4</code>、
              <code>/v1</code> 这一层，<strong>不要</strong>带 <code>/chat/completions</code>。
            </li>
            <li>
              图片以 base64 内联发送（<code>image_url</code> +{" "}
              <code>data:image/jpeg;base64,…</code>），需要服务端支持这种传图方式。
            </li>
            <li>
              程序只认模型回的 JSON（名称 / 标签 / 把握度）；解析不出来就当作没建议，
              不会把乱猜的词写进你的标签。
            </li>
          </ul>
          <p className="tiny" style={{ margin: "8px 0 0" }}>
            存好后就能用了：上传衣物时后台会自动起名打标（结果在「待确认」里看，
            不通过会写明原因，也能点「重新处理」重试）；搭建搭配时点名称旁的{" "}
            <strong>AI 推荐</strong>，让它看着画布起名并挑标签。
          </p>
        </div>
        {llm?.from_admin && (
          <div className="alert">
            当前这份配置由<strong>管理员代设</strong>。你自己保存一次之后，就以你自己的为准。
          </div>
        )}
        {llmMsg && (
          <div className={llmMsg.kind === "ok" ? "alert" : "alert alert-error"}>
            {llmMsg.text}
          </div>
        )}
        <form onSubmit={onSaveLlm}>
          <div className="field">
            <label htmlFor="llm-base-url">模型地址</label>
            <input
              id="llm-base-url"
              value={llmBaseUrl}
              maxLength={512}
              autoComplete="off"
              placeholder="https://open.bigmodel.cn/api/paas/v4"
              onChange={(e) => setLlmBaseUrl(e.target.value)}
            />
            <span className="tiny">OpenAI 兼容接口的 base URL（到 /v4 这一层，不带 /chat/completions）</span>
          </div>
          <div className="field">
            <label htmlFor="llm-model">模型名称</label>
            <input
              id="llm-model"
              value={llmModel}
              maxLength={128}
              autoComplete="off"
              placeholder="glm-4v-flash"
              onChange={(e) => setLlmModel(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="llm-api-key">API Key</label>
            <input
              id="llm-api-key"
              type="password"
              value={llmApiKey}
              maxLength={512}
              autoComplete="new-password"
              placeholder={llm?.api_key_masked ? "已保存，留空则不修改" : "sk-…"}
              onChange={(e) => setLlmApiKey(e.target.value)}
            />
            <span className="tiny">
              {llm?.api_key_masked
                ? `已保存：${llm.api_key_masked}（留空表示不改，要清空请点下面的「清除」）`
                : "只存在你自己的账号里，接口只回脱敏串"}
            </span>
          </div>
          <div className="form-actions">
            <button
              type="button"
              className="btn btn-sm btn-danger"
              disabled={llmBusy || (!llm?.configured && !llm?.base_url && !llm?.model)}
              onClick={() => void onClearLlm()}
            >
              清除
            </button>
            <button type="submit" className="btn btn-primary" disabled={llmBusy}>
              {llmBusy ? "保存中…" : "保存"}
            </button>
          </div>
        </form>
        <p className="tiny" style={{ marginBottom: 0, marginTop: 10 }}>
          当前状态：
          <span className={`about-dot ${llm?.configured ? "ok" : "off"}`} aria-hidden="true" />
          {llm?.configured
            ? `已配置（${llm.base_url} / ${llm.model}${llm.api_key_masked ? "" : "，未设密钥"}）`
            : "未配置，AI 起名与打标不可用"}
        </p>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>配额</h3>
      <div className="card" style={{ marginBottom: 18, maxWidth: 620 }}>
        <dl className="about-grid">
          <dt>衣物</dt>
          <dd>{quotaText(quota?.garments)}</dd>
          <dt>搭配</dt>
          <dd>{quotaText(quota?.outfits)}</dd>
          <dt>标签取值</dt>
          <dd>{quotaText(quota?.tags)}</dd>
        </dl>
        <p className="tiny" style={{ marginBottom: 0 }}>
          「不限」表示没有上限；有上限时达到后无法再新增，需要先删除或联系管理员调整。
        </p>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>外观</h3>
      <div className="card" style={{ marginBottom: 18 }}>
        <div className="theme-row">
          <span className="tiny">主题</span>
          <div className="theme-seg" role="group" aria-label="主题">
            {THEME_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                className={`theme-seg-btn${theme === opt.value ? " on" : ""}`}
                aria-pressed={theme === opt.value}
                onClick={() => pickTheme(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <span className="tiny">「跟随系统」会随系统的深浅色设置自动切换。</span>
        </div>
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

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "28px 0 10px" }}>关于</h3>
      <div className="card" style={{ maxWidth: 520 }}>
        {!meta ? (
          <p className="tiny" style={{ margin: 0 }}>
            正在读取服务信息…
          </p>
        ) : (
          <dl className="about-grid">
            <dt>版本</dt>
            <dd>
              v{meta.version}
              <span className="tiny"> · {meta.app_name}</span>
            </dd>

            <dt>AI 抠图</dt>
            <dd>{MATTING_LABEL[meta.matting_provider] ?? meta.matting_provider}</dd>

            <dt>AI 打标</dt>
            <dd>
              <span className={`about-dot ${taggerReady ? "ok" : "off"}`} aria-hidden="true" />
              {TAGGER_LABEL[meta.tagger_provider] ?? meta.tagger_provider}
              {!taggerReady && meta.tagger_provider !== "none" && (
                <span className="tiny">（依赖未就绪）</span>
              )}
            </dd>

            <dt>自助注册</dt>
            <dd>{meta.registration_mode ? MODE_LABEL[meta.registration_mode] : "—"}</dd>
          </dl>
        )}
      </div>
    </>
  );
}
