# 衣橱管理系统

自托管的衣橱管理：衣物标签化归档、AI 抠图与自动打标、套装拖拽搭配。

以 Docker Compose 形式部署，全部数据留在自己机器上。

---

## 功能规划

| 批次 | 内容 | 状态 |
|---|---|---|
| 1 | 账号体系、多设备会话、用户隔离骨架 | ✅ 已完成 |
| 2 | 衣物 CRUD、双标签体系（单品 / 搭配独立）、缩略图筛选墙 | ✅ 已完成 |
| 3 | 上传、AI 抠图 Worker、打标 Provider、复核界面 | ⏳ |
| 4 | 套装搭配编辑器（拖拽画布 / 图层 / 素材库 / 保存回看） | ⏳ |
| 5 | 删除级联提示、UI 打磨、测试、发版 | ⏳ |

### 批次 2 已实现

- **衣物**：新建 / 编辑 / 删除，多图上传（自动生成 240 / 720 px WebP 缩略图）、
  指定封面、单图删除。
- **标签**：初始化只建「大类」并预置 8 个服装品类取值；小类 / 颜色 / 材质由用户
  按需自己在「管理标签」里加。搭配侧（季节 / 风格 / 色系）是完全独立的另一套。
- **筛选墙**：以缩略图为主，支持多标签**交集**筛选 + 名称模糊搜索 + 分页。
- **图片访问**：签名 URL（`<img>` 带不了 Authorization 头），默认 7 天有效；
  文件丢失时前端显示占位块而不是破图图标。
- **隔离**：所有业务数据按 `owner_id` 隔离，跨用户访问一律 404。

---

## 快速开始

### 1. 准备配置

```bash
cp .env.example .env
```

编辑 `.env`，**至少**改这三项：

```ini
POSTGRES_PASSWORD=<强密码>
WARDROBE_JWT_SECRET=<至少 32 字节随机串>
WARDROBE_BOOTSTRAP_PASSWORD=<管理员密码>
```

生成密钥：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. 启动

```bash
docker compose up -d --build
```

首次启动会自动建表并创建 `.env` 中指定的管理员账号（幂等）。

访问 **http://<宿主机IP>:43470**，用 `WARDROBE_BOOTSTRAP_USERNAME` / `WARDROBE_BOOTSTRAP_PASSWORD` 登录。

### 3. 常用操作

```bash
docker compose ps                     # 查看状态
docker compose logs -f api            # 跟随后端日志
docker compose down                   # 停止（保留数据卷）
docker compose down -v                # 停止并删除数据卷（⚠️ 会清空数据）

# 运维 CLI
docker compose exec api ./entrypoint.sh cli create-user --username xxx --password 'yyy'
docker compose exec api ./entrypoint.sh cli list-users
docker compose exec api ./entrypoint.sh cli reset-password --username xxx --password 'zzz'
```

---

## 容器拓扑

```
web (nginx)  ──/api/──▶  api (FastAPI)
 :43470                      │
                             ├─▶ db (PostgreSQL 17)
                             └─▶ redis (队列，批次 3 起使用)
```

`api` 容器启动时自动执行 `alembic upgrade head`，无需手工迁移。

数据卷：`db-data`（数据库）、`redis-data`、`media-data`（图片，批次 2 起使用）。

---

## 资源需求

只跑当前功能（不含本地 AI 模型）约 **2 核 / 4GB 内存 / 32GB 磁盘**。

批次 3 引入抠图 Worker 后内存需求上升约 1.2GB（ONNX 模型常驻），详见 `docs/开发说明.md`。

---

## 配置项

全部环境变量以 `WARDROBE_` 为前缀，见 `.env.example` 内的注释。

几个关键项：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WARDROBE_PORT` | 43470 | 宿主机对外端口 |
| `WARDROBE_ALLOW_REGISTRATION` | false | 是否开放自助注册 |
| `WARDROBE_ACCESS_TOKEN_MINUTES` | 30 | 访问令牌有效期 |
| `WARDROBE_REFRESH_TOKEN_DAYS` | 30 | 刷新令牌有效期 |
| `WARDROBE_BOOTSTRAP_USERNAME` | — | 首启自动创建的管理员 |
| `WARDROBE_CORS_ORIGINS` | localhost:5173 | 开发期跨域白名单 |

---

## 本地开发

见 [`docs/开发说明.md`](docs/开发说明.md)。

---

## 文档

- [`评估报告.md`](评估报告.md) — 工作量、算力、技术选型与设计决议
- [`docs/开发说明.md`](docs/开发说明.md) — 本地开发、测试、构建与发布
