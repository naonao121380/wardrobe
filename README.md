# 衣橱管理系统

自托管的衣橱管理应用：把衣服一件件拍下来，AI 自动抠图、打标签，再拖成整套搭配。

照片和数据库全在自己机器上，不依赖任何第三方存储。一条 `docker compose up -d` 就能跑起来，
适合放在家里的 NAS、小主机或常开的树莓派上。

- **给谁的**：衣服多、想按「能穿出去的一整套」来整理，而不是只往相册里堆图的人
- **技术形态**：React + FastAPI + PostgreSQL，抠图用本地 ONNX 小模型（纯 CPU）
- **多用户**：一套服务可以几个人各用各的，衣物与搭配互相看不见

---

## 功能

### 衣物整理

- 新建、编辑、删除；一件衣服可以配多张图，指定其中一张作封面
- 上传自动生成 240 / 720 px 的 WebP 缩略图，列表滚起来不卡
- **批量上传**：一次多选，一张一件，按文件大小自动分批，张数不限
- **批量操作**：加 / 删 / 覆盖标签、批量删除、一键排队抠图打标
- 卡片上直接翻看原图与抠图的大图（透明 PNG 垫棋盘格）

### AI 抠图与打标（后台异步）

- 上传后进后台队列：先抠图（`rembg` + alpha matting），再让视觉模型给出候选标签与名字
- **复核界面**三栏对照：原图 / 抠图 / 候选标签，可逐条采纳、改写，或整件重新处理
- **抠图精修**：擦除笔、恢复笔（从原图取像素补回）、框选局部重抠、缩放平移
  （放大到像素级贴着边缘修，方向键微调、空格拖拽）
- 没配大模型也能用：抠图照常，打标降级为手动

### 套装搭配

- 拖拽画布：任意摆放、缩放、旋转、镜像、调透明度，图层可排序
- 素材库直接取已抠好的衣服，自动裁掉透明边
- 导出合成图；一套搭配可另传多张**上身图**
- AI 推荐搭配名与标签（先给建议，确认后才保存）

### 标签与筛选

- 两套互不干扰的体系：衣物用 `大类 / 小类 / 颜色 / 材质`，搭配用 `季节 / 风格 / 色系`
- 级联：选小类自动带出大类，撤掉大类连带清空小类；颜色带色板可选
- 筛选墙：多标签**交集**筛选 + 名称模糊搜索 + 分页，另有「待复核」快捷入口
- 标签随时增删改，也能整套导出 / 导入

### 账号与多用户

- 两级角色：管理员、普通用户
- **注册三模式**：自由注册 / 邀请码注册 / 关闭注册，管理员在界面上随时切换，立刻生效
- **邀请码**：批量生成，可设可用次数（`1` = 一人一码）与有效期，随时吊销
- **按人配额**：衣物 / 搭配 / 标签取值各设上限，到顶时拦住新增并说清现状
- **大模型按人配置**：每人填自己的接口地址、模型名与 API Key，互不通用；接口只回脱敏串
- 管理员可建号、改配额与资料、重置密码（连带踢下线）、停用、删号（连带清数据）

> 打标与搭配推荐要求**能看图的视觉模型**（多模态），走 OpenAI 兼容接口：
> 地址填到 `/v1`、`/v4` 这一层，程序自己拼 `/chat/completions`。纯文本模型填了也用不了。

### 其他

- 删除衣物会先告诉你「哪几套搭配用到它」，默认保留搭配（被引用的层变成「素材已删除」），也可以勾选连删
- 主题三档：跟随系统 / 浅色 / 深色
- 统一的确认弹窗与错误边界，一处渲染出错不会整页白屏
- 窄屏适配，手机上能拍照上传、能改标签

---

## 快速开始

需要 Docker 与 Docker Compose v2，**2 核 / 4 GB 内存 / 32 GB 磁盘**起步。

### 1. 准备配置

```bash
cp .env.example .env
```

编辑 `.env`，**至少改这三项**：

```ini
POSTGRES_PASSWORD=<强密码>
WARDROBE_JWT_SECRET=<至少 32 字节随机串>
WARDROBE_BOOTSTRAP_PASSWORD=<管理员密码>
```

生成随机串：

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 2. 启动

```bash
docker compose up -d --build
```

首次启动会自动建表、跑完数据库迁移，并按 `WARDROBE_BOOTSTRAP_*` 创建管理员（幂等，
同名已存在则跳过）。抠图模型在 worker 首次启动时下载，视网络情况要等几分钟。

### 3. 登录

浏览器打开 **http://<宿主机IP>:43470**，用 `.env` 里的
`WARDROBE_BOOTSTRAP_USERNAME` / `WARDROBE_BOOTSTRAP_PASSWORD` 登录。

---

## 使用

第一次用，按这个顺序走一遍就能上手：

1. **传衣服** —— 衣橱页右上角「上传」，可一次多选；打开「上传后立即交给 AI 抠图打标」
   就不用再单独点一次
2. **等 AI 跑完** —— 后台会依次抠图、打标，卡片上的状态会从「处理中」变成「待复核」
3. **复核** —— 点「待复核」进三栏界面，标签不对就改，抠得毛糙就用精修工具修两笔
4. **整理标签** —— 左侧「标签管理」里增删自己的分类；预置的只有大类与季节，
   小类 / 颜色 / 材质 / 风格 / 色系按自己的习惯加
5. **搭一套** —— 搭配页新建，从素材库拖衣服上画布，摆好后保存，也可以顺手传几张上身图

几个日常会用到的操作：

| 想做的事 | 在哪 |
|---|---|
| 找「所有黑色的外套」 | 衣橱页顶部筛选栏，多选标签取交集 |
| 一次给 20 件加同一个标签 | 勾选卡片 → 批量 → 加标签 |
| 换封面 / 加图 / 删图 | 点开衣物详情 |
| 让别人也能用 | 管理页 → 注册设置（开邀请码或自由注册）→ 邀请码里生成 |
| 给别人配大模型 | 管理页 → 点进用户 → 模型配置（用户自己也能在设置页填） |

### 常用命令

```bash
docker compose ps                     # 查看状态
docker compose logs -f api            # 跟随后端日志
docker compose logs -f worker         # 抠图 / 打标在干什么
docker compose down                   # 停止并移除容器（数据保留）
docker compose down -v                # 同上；数据在宿主机目录里，不会被这个 -v 删掉

# 运维 CLI
docker compose exec api ./entrypoint.sh cli create-user --username xxx --password 'yyy'
docker compose exec api ./entrypoint.sh cli list-users
docker compose exec api ./entrypoint.sh cli reset-password --username xxx --password 'zzz'
docker compose exec api ./entrypoint.sh cli reseed-tags --username xxx --yes   # 标签拉回默认
```

---

## 数据与备份

```
web (nginx)  ──/api/──▶  api (FastAPI)
 :43470                      │
                             ├─▶ db (PostgreSQL 17)
                             ├─▶ redis (队列)
                             └─◀ worker (消费者：抠图 + 打标)
```

全部数据**绑定挂载到 compose 同目录下**（不用命名卷，整个部署就是一个文件夹，搬走即迁移）：

| 宿主机 | 容器内 | 内容 |
|---|---|---|
| `./data/postgres` | `/var/lib/postgresql/data` | 数据库 |
| `./data/redis` | `/data` | 队列持久化 |
| `./data/media` | `/data/media` | 原图、抠图、缩略图 |
| `./data/numba` | `/data/numba` | 抠图的编译缓存（删了只是下次启动慢几分钟） |
| `./models` | `/root/.u2net` | ONNX 抠图模型 |

- **备份**：`./data/postgres` 里是 PostgreSQL 数据目录，**别直接 `cp`**（写到一半会拿到坏快照）。
  用 `docker compose exec db pg_dump -U wardrobe wardrobe > backup.sql`，
  再顺手打包 `./data/media`。
- ⚠️ `./data/postgres` 必须在**本地盘**上，别放 SMB / NFS —— PostgreSQL 的文件锁与
  `fsync` 语义在网络文件系统上不可靠。
- `docker compose down` 不会删这些目录；`down -v` 也删不到它们（数据在宿主机目录里）。

### 升级

```bash
git pull
docker compose up -d --build
```

镜像重建后 `api` 启动时会自动把数据库迁移到最新版本，旧数据保留。

---

## 配置项

全部环境变量以 `WARDROBE_` 为前缀，完整注释见 [`.env.example`](.env.example)。

| 变量 | 默认 | 说明 |
|---|---|---|
| `WARDROBE_PORT` | 43470 | 宿主机对外端口 |
| `POSTGRES_PASSWORD` | — | 数据库密码，**必改** |
| `WARDROBE_JWT_SECRET` | — | 签发登录令牌的密钥，**必改**，至少 32 字节 |
| `WARDROBE_BOOTSTRAP_USERNAME` | — | 首启自动创建的管理员账号 |
| `WARDROBE_BOOTSTRAP_PASSWORD` | — | 上述管理员的密码 |
| `WARDROBE_ALLOW_REGISTRATION` | false | **首次**读设置时的初始注册模式，之后以库里的设置为准 |
| `WARDROBE_ACCESS_TOKEN_MINUTES` | 30 | 登录令牌有效期（分钟） |
| `WARDROBE_REFRESH_TOKEN_DAYS` | 30 | 刷新令牌有效期（天） |
| `WARDROBE_WORKER_MEM_LIMIT` | 1500m | worker 内存硬上限，超了只杀 worker 并自动重启，不影响宿主机 |
| `WARDROBE_WORKER_REFINE_PORT` | 8001 | worker 内部重抠接口端口（仅容器网络），设 0 关闭 |
| `WARDROBE_CORS_ORIGINS` | localhost:5173 | 开发期跨域白名单 |

关于 worker 内存：抠图（alpha matting）是全场最吃内存的一步，一张超大手机原图就能把
1 GB 内存的机器拖死。机器 ≤ 4 GB 给 `1200m~1500m`，≥ 8 GB 可给 `2048m`；
设 0 或删掉 = 不限制（不推荐）。

---

## 资源需求

| 配置 | 体验 |
|---|---|
| 2 核 / 4 GB / 32 GB | 可用。单张抠图约 3~6 秒 |
| 1 核 / 2 GB | 能跑，抠图慢（10~20 秒/张），建议把 `WARDROBE_WORKER_MEM_LIMIT` 降到 1200m |

worker 常驻一份 ONNX 模型（约 200 MB），加上推理时的中间缓冲，实际占用通常在
600 MB ~ 1.2 GB 之间。不需要 GPU —— 纯 CPU 即可。

首次启动会编译抠图依赖（numba），1 核机器上大约 5 分钟一次；之后走 `./data/numba`
缓存，重启只要十几秒。**别把 `./data/numba` 挂在只读或 tmpfs 上**，否则每次重启都要重编译。

---

## 常见问题

**框选重抠提示「抠图服务没在跑」**
API 进程自身不带抠图能力，框选重抠要 worker 代算。确认 `worker` 容器活着
（`docker compose ps`），且 `.env` 里 `WARDROBE_WORKER_REFINE_PORT` 不是 0。

**衣服一直停在「处理中」**
先看 `docker compose logs -f worker`。常见原因：模型还在下载；机器太慢导致单件超时
（默认 600 秒）；或者内存不足被 OOM 杀掉（`docker inspect` 看 `OOMKilled`）。
调大 `WARDROBE_WORKER_MEM_LIMIT` 后重启即可。

**打标报「缺少视觉模型」**
在设置页填上自己的模型接口（必须是多模态），或者让管理员代设。没配也能用，只是要手打标签。

**上传大图报 413**
自带的前端容器已经放宽到 64 MB，如果前面还挂了自己的 Nginx / Traefik / Caddy，
要把它们的请求体上限也调到 50 MB 以上（Nginx 的 `client_max_body_size`）。

---

## 本地开发

见 [`docs/开发说明.md`](docs/开发说明.md) —— 前后端启动、测试、构建与发版流程。

---

## 文档与许可

- [`CHANGELOG.md`](CHANGELOG.md) — 版本变更记录
- [`评估报告.md`](评估报告.md) — 工作量、算力、技术选型与设计决议
- [`docs/开发说明.md`](docs/开发说明.md) — 本地开发、测试、构建与发布

代码以 **MIT** 发布，见 [`LICENSE`](LICENSE)。注意第三方组件的许可与之独立：
抠图模型（U²-Net / `u2net.onnx`）不随仓库分发，由 `rembg` 在构建时自行下载，遵循其上游许可；
打标与搭配推荐调用的视觉模型按各家服务条款使用。商用前请自行确认。
