import { useAuth } from "../auth/AuthProvider";

export default function HomePage() {
  const { user } = useAuth();

  return (
    <>
      <div className="grid grid-2" style={{ marginBottom: 18 }}>
        <div className="card">
          <h3>欢迎回来，{user?.display_name || user?.username}</h3>
          <p>批次 1 已就位：账号体系、多设备会话管理、用户隔离骨架。</p>
        </div>
        <div className="card">
          <h3>
            当前版本 <span className="badge badge-accent">v0.1.0</span>
          </h3>
          <p>后续批次将依次接入衣物管理、AI 抠图流水线与套装搭配编辑器。</p>
        </div>
      </div>

      <h3 style={{ fontSize: 14, fontWeight: 600, margin: "0 0 10px" }}>开发路线</h3>
      <div className="list">
        <div className="list-row">
          <div>
            <div className="title">
              批次 1 · 骨架与鉴权 <span className="self-tag">已完成</span>
            </div>
            <div className="meta">登录 / 刷新令牌轮换 / 多设备会话 / 用户隔离</div>
          </div>
        </div>
        <div className="list-row">
          <div>
            <div className="title">
              批次 2 · 衣物与标签 <span className="badge">待开发</span>
            </div>
            <div className="meta">衣物 CRUD、双标签体系、缩略图筛选墙</div>
          </div>
        </div>
        <div className="list-row">
          <div>
            <div className="title">
              批次 3 · AI 流水线 <span className="badge">待开发</span>
            </div>
            <div className="meta">上传、抠图 Worker、打标 Provider、复核界面</div>
          </div>
        </div>
        <div className="list-row">
          <div>
            <div className="title">
              批次 4 · 套装搭配编辑器 <span className="badge">待开发</span>
            </div>
            <div className="meta">拖拽画布、图层、素材库、保存回看</div>
          </div>
        </div>
        <div className="list-row">
          <div>
            <div className="title">
              批次 5 · 收尾 <span className="badge">待开发</span>
            </div>
            <div className="meta">删除级联提示、UI 打磨、测试、发版</div>
          </div>
        </div>
      </div>
    </>
  );
}
