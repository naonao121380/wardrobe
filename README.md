# 衣橱管理系统

自托管的衣橱管理：衣物标签化归档、AI 抠图与自动打标、套装拖拽搭配。

以 Docker Compose 形式部署，全部数据留在自己机器上。

> **v1.0.0** — 计划中的五个批次全部交付，功能已完整。

---

## 功能规划

| 批次 | 内容 | 状态 |
|---|---|---|
| 1 | 账号体系、多设备会话、用户隔离骨架 | ✅ 已完成 |
| 2 | 衣物 CRUD、双标签体系（单品 / 搭配独立）、缩略图筛选墙 | ✅ 已完成 |
| 3 | 上传、AI 抠图 Worker、打标 Provider、复核界面 | ✅ 已完成 |
| 4 | 套装搭配编辑器（拖拽画布 / 图层 / 素材库 / 保存回看）+ AI 推荐命名与标签 | ✅ 已完成 |
| 5 | 删除级联提示、UI 打磨、测试、发版 | ✅ 已完成 |
| 6 | 多用户与管理员、注册三模式、按人配额、按人配置大模型 | ✅ 已完成 |

### 现在能做什么

**账号与权限**
- 两级角色：管理员 / 普通用户。管理员可查阅每个用户的用量、配额、模型配置与登录设备
- **注册三模式**：无限制自助注册 / 邀请码注册 / 关闭注册（管理员在界面上随时改，立刻生效）
- **邀请码**：批量生成，可设可用次数（`1` = 一人一码）与有效期，随时吊销
- **按人配额**：衣物 / 搭配 / 标签取值各有上限（留空 = 不限），到顶时拦住新增并说清现状
- 管理员可建号、改资料与配额、重置密码（连带踢下线）、停用、删号（连带清数据与图片）
- **大模型按人配置**：每个用户填自己的模型地址 / 模型名 / API Key，互不通用；
  接口只回脱敏串。用户没填时管理员可代设。**必须是能看图的视觉模型**（多模态），
  走 OpenAI 兼容接口（地址填到 `/v4`、`/v1` 这一层，程序自己拼 `/chat/completions`）；
  纯文本模型填了也用不了 —— 设置页里写了这条要求与几个可用模型示例

**衣物**
- 新建 / 编辑 / 删除、多图上传（自动生成 240 / 720 px WebP 缩略图）、指定封面
- **批量**：一次多选上传（一张一件，**按大小自动分批**，张数不限）、批量加 / 删 / 覆盖标签、批量删除、一键抠图打标
- **看大图**：卡片上直接翻原图 / 抠图的大图（透明图垫棋盘格）

**标签**
- 两套完全独立的体系：衣物侧 `大类 / 小类 / 颜色 / 材质`，搭配侧 `季节 / 风格 / 色系`
- 级联：`小类 → 大类`，选子自动带父、撤父连带撤子；颜色带色板
- 筛选墙：多标签**交集**筛选 + 名称模糊搜索 + 分页；有「待复核」快筛入口

**AI 流水线（异步）**
- 上传后进后台 Worker：抠图（rembg）→ 打标 / 命名（VLM 或 CLIP，未配置则降级手动）
- 复核界面：原图 / 抠图 / 候选标签三栏，采纳、改写或重新处理
- **抠图精修**：擦除笔 / 恢复笔（从原图取像素补回）、框选重抠、**缩放平移**（放大后贴着像素修边）

**套装搭配**
- 拖拽画布、图层排序、缩放 / 旋转 / 镜像 / 透明度、素材库（抠图优先、自动裁边）
- 导出合成图，可另传**上身图**（可选、可多张）
- AI 推荐命名与标签（不落库，确认后保存）

**其他**
- 删除衣物时提示「有哪几套搭配用到它」，默认保留搭配、被引用的层变成「素材已删除」，也可勾选连删
- 主题三档（跟随系统 / 浅色 / 深色）、统一的应用内确认弹窗、错误边界（一处渲染出错不再白屏）

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
docker compose exec api ./entrypoint.sh cli reseed-tags --username xxx --yes   # 标签拉回当前默认
```

---

## 容器拓扑

```
web (nginx)  ──/api/──▶  api (FastAPI)
 :43470                      │
                             ├─▶ db (PostgreSQL 17)
                             ├─▶ redis (队列)
                             └─◀ worker (消费者：抠图 + 打标)
```

`api` 容器启动时自动执行 `alembic upgrade head`，无需手工迁移。

**ONNX 模型只装在 worker 镜像里**，API 进程保持轻量（约 250MB）；两者共享
数据库与媒体目录，靠数据库里的状态机对接。

唯一的例外是「框选重抠」：API 镜像不带 `rembg`，无法自己算，于是 worker 在
容器网络内额外开一个只监听 `WARDROBE_WORKER_REFINE_PORT`（默认 8001）的内部接口，
API 把 `storage_key` 丢过去、拿回 PNG。**这个端口不对宿主机 publish**；
设 `WARDROBE_WORKER_REFINE_PORT=0` 可关掉，届时框选重抠会提示不可用。

**数据全部绑定挂载到 compose 同目录下**（不用命名卷，整个部署 = 一个目录）：

| 宿主机 | 容器内 | 内容 | 属主 |
|---|---|---|---|
| `./data/postgres` | `/var/lib/postgresql/data` | 数据库 | `uid 70`（容器自动 chown，别手工改） |
| `./data/redis` | `/data` | Redis AOF | `uid 999`（同上） |
| `./data/media` | `/data/media` | 图片（批次 2 起） | `root`（api/worker 以 root 运行） |
| `./models` | `/root/.u2net` | ONNX 抠图模型 | 你自己的账号 |

⚠️ `./data/postgres` 必须在**本地盘**上，别放 SMB / NFS —— PostgreSQL 的文件锁与
`fsync` 语义在网络文件系统上不可靠。`docker compose down -v` 也**不会**删掉这些数据。

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
| `WARDROBE_ALLOW_REGISTRATION` | false | **首次**读设置时的初始注册模式（开后即以库里的为准，管理员可在界面上改） |
| `WARDROBE_ACCESS_TOKEN_MINUTES` | 30 | 访问令牌有效期 |
| `WARDROBE_REFRESH_TOKEN_DAYS` | 30 | 刷新令牌有效期 |
| `WARDROBE_BOOTSTRAP_USERNAME` | — | 首启自动创建的管理员 |
| `WARDROBE_CORS_ORIGINS` | localhost:5173 | 开发期跨域白名单 |
| `WARDROBE_WORKER_MEM_LIMIT` | 1500m | worker 容器内存硬上限，超了只杀 worker 并重启，不影响宿主机 |
| `WARDROBE_WORKER_REFINE_PORT` | 8001 | worker 内部重抠接口端口（仅容器网络），设 0 关闭 |

---

## 本地开发

见 [`docs/开发说明.md`](docs/开发说明.md)。

---

## 文档

- [`CHANGELOG.md`](CHANGELOG.md) — 版本变更记录
- [`评估报告.md`](评估报告.md) — 工作量、算力、技术选型与设计决议
- [`docs/开发说明.md`](docs/开发说明.md) — 本地开发、测试、构建与发布

---

## 许可证

本项目代码以 **MIT** 许可发布，见 [`LICENSE`](LICENSE)。

⚠️ 注意第三方组件的许可与之独立：抠图用的 ONNX 模型（U²-Net / `u2net.onnx`）不随仓库分发，
构建时由 `rembg` 自行下载，遵循其上游许可；搭配与打标调用的视觉模型（如各家 VLM API）
按各自服务条款使用。商用前请自行确认这几项。
