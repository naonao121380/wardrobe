# 变更记录

本项目按批次推进，`v1.0.0` 是计划内五个批次全部交付后的首个完整版。

---

## v1.0.0 发版后调整 — 2026-10-17（搭配 AI 缺 httpx / 框选重抠缺 rembg / 首页改版）

**用户反馈**：*搭配 ai 推荐时提示没安装 httpx 包；手动抠图框选重抠提示 rembg 不可用：
No module named 'rembg'*。外加一句：*首页不要放开发历程了，改成欢迎和简介的页面*。

**共同点**：两个 bug 都是「依赖装在了别的镜像里」，而且**只存在于 Docker 部署**。
裸机（201）把两套依赖塞在同一个 venv，本地怎么点都好使 —— 属于测不出来的那类坑。

### 1. 修 bug：搭配 AI 推荐报「httpx 未安装」

`httpx` 只写在 `requirements-worker.txt` 里，而「搭配 AI 推荐」走的是
`/outfits/{id}/ai-suggest` → `tagger_for_user()` → `VlmTagger.suggest()` → `import httpx`
——**整个调用链都在 API 进程里**。所以 worker 打得标，API 一调就报缺依赖。

修法：把 `httpx>=0.27` 挪进 `requirements.txt`（API 与 Worker 共用同一份）。同时把那句
报错改成指向修法（「服务端缺少 httpx 依赖，需要重建镜像」），别再只说「未安装」。

### 2. 修 bug：框选重抠报「rembg 不可用：No module named 'rembg'」

`POST /garments/{id}/images/{image_id}/matte-box` 在 API 进程里直接
`import app.worker.refine` 跑 `remat_box`，可按 DECISIONS D5，**rembg 只装在 Worker 镜像里**。
于是这个功能在 Docker 部署里**从来就没成功过**。

为什么不能给 API 也装上：那会让入口容器多背 u2net 的 180MB 模型 + ORT 运行时，一次重抠
就要加载；它一 OOM，整个网站都挂。所以改成「同一台机器上唯一装了模型的进程代算」：

```
API  matte-box 路由（app/api/routes/garments.py）
  └ app/services/refine.py
      ├ 配了 WARDROBE_WORKER_REFINE_URL → POST worker:8001/refine/matte-box
      │     只传两个 storage_key（api 与 worker 共用 ./data/media，不搬图片），
      │     Worker 算完把 PNG 回传
      └ 没配 → 本进程里算（裸机 / 本机开发）
```

配套细节：

| 要点 | 说明 |
|---|---|
| Worker 侧接口 | 新增 `app/worker/refine_api.py`，随 `python -m app.worker` 在**后台线程**里起 uvicorn（放主循环会被 uvicorn 顶掉 SIGTERM 处理器）。只走容器网络，compose **不 publish** |
| 不落库 | Worker 只负责「算」并回传 PNG；DB 记录与文件替换仍由 API 做，算失败也写不坏数据 |
| 单线程 | 走 `_AI_POOL`，与后台流水线串行；1~4 核并行只会一起慢、一起吃内存 |
| crop 也要缩 | 框可以一下框住整张 15 MP 原图，送模型前同样按 `WARDROBE_AI_INPUT_MAX_EDGE` 缩，掩码按**原始裁切**尺寸拉回 |
| 文案 | 连不上 Worker → 503「抠图服务没在跑」；等太久 → 504；框非法 → 400（原因原样显示） |

新增两个变量：`WARDROBE_WORKER_REFINE_PORT`（worker，默认 8001，0=关掉）、
`WARDROBE_WORKER_REFINE_URL`（api，compose 里已写死为 `http://worker:8001`）。

### 3. 首页：去掉「开发路线」，改成欢迎与简介

原来首页下半屏是一张「批次 1~5 已完成」的路线表 —— 发版之后它对用户就没有意义了。
改成：欢迎语 + 一句话介绍 + 两个入口（进入衣橱 / 搭一套）+ 四张统计卡 +
「这里能做什么」（四条）+「第一次用，三步就够」+ AI 服务状态 + 版本脚注。
统计卡多了一张「标签类别 / 取值」，点数比翻标签页快。

### 4. 顺手修：三个用例的签名 URL 偶发失败

`sign_url` 的 `e` 是**秒级**时间戳，两次调用跨过整秒就不相等，
`assert cover_url == thumb_medium_url` 因此会随机器负载随机失败（这一轮慢机器上就撞到了
`test_set_primary_image`）。改成比「去掉 query 的路径」，签名不在比较范围里。

### 5. 查这个 bug 时挖出来的：容器里 `import rembg` 要 6 分钟

验证框选重抠时发现请求 120s 都不返回，一路查下去是 numba：

`pymatting/util/kdtree.py` 里有 `@njit(..., cache=True, parallel=True)`，
`import rembg` 会**当场编译**这些函数（LLVM）。裸机上 venv 的 `site-packages` 可写，
numba 把编译产物缓存进去，第二次起只要 7 秒 —— 所以本地从来没觉得慢。
**容器里镜像层是只读的**，缓存写不进去，于是每次启动都重编译一遍：
J1800（1 vCPU、无 AVX）实测 **376.9 s**，期间第一次抠图/框选重抠全在干等，
日志一行都没有，看起来就是「卡死」。

这也可能是「衣服一直处理中」的另一半原因：`worker_job_timeout_seconds` 是 600s，
重启后第一件衣服如果撞上这 6 分钟编译 + 大图推理，很容易正好超时。

修法（只改 compose，不动镜像）：给 worker 一个**可写**的 numba 缓存目录

```yaml
environment:
  NUMBA_CACHE_DIR: /data/numba
volumes:
  - ./data/numba:/data/numba
```

实测同一台机器：冷缓存 `import rembg` **376.9s** → 热缓存 **24.7s**（快 15 倍），
`new_session` 9.1s → 2.8s。缓存随部署目录保留，重启 worker 不再重新编译。

### 验收

- pytest **318 passed / 0 failed**（新增 `test_refine_proxy.py` 7 项：Worker 接口的
  正常/穿越/缺文件/错误映射 + API 侧远程/本机两条分发路径）。
- `tsc --noEmit` 干净；`smoke_release.sh` 新增三节：9g（API 镜像里有 httpx）、
  9h（先烤热 numba 缓存并断言缓存生效，再**在 api 容器里真调一次 service 层，
  走 HTTP 到 worker 用真模型算一张图**）。

---

## v1.0.0 发版后调整 — 2026-10-17（worker 内存暴涨 / 一直「处理中」/ 内存上限）

**用户反馈**：*worker 的内存持续上升（接近 4gb），上传的衣服一直提示处理中，这正常吗？
是不是有什么卡死的地方*。随后补了一句：*构建机也在宿主机上……请在下一个上传的东西里
设置这个程序的内存占用上限，写进 env 文件里*。

**一句话结论**：不正常，是两个独立的 bug 叠在一起 —— 一个把内存撑爆，一个让任务永远
停在「处理中」。现场日志（`processed_at` 的 `TypeError`）直接指认了第二个。

### 1. 修 bug：15 MP 手机原图把 worker 撑到 4 GB

`rembg` 的 alpha matting 走 pymatting 的 closed-form 求解，复杂度跟**像素数**强相关。
用户直接上传手机原图（3500×4500 ≈ 15.7 MP），在 1~4 核的小机器上跑一次就能吃光内存。
更糟的是 `asyncio.to_thread` 的默认线程池会开 `min(32, cpu+4)` 个线程，而
`asyncio.wait_for` **只能取消 await，取消不掉线程** —— 超时后那个线程还在推理，
下一件又投进另一个线程，几个推理同时占内存，RSS 只涨不降，最终连宿主机一起拖死。

三处一起改：

| 改动 | 位置 | 作用 |
|---|---|---|
| 抠图前降采样（长边 ≤ 1600） | `worker/pipeline.py::_fit_for_ai`、`config.ai_input_max_edge` | 15.7 MP → 2 MP，内存降一个数量级，肉眼无差 |
| 专用**单线程**池 `_AI_POOL` | `worker/pipeline.py` | 同一时刻只允许一个推理在跑，最坏是「慢」，不是「爆内存」 |
| 超时后主动退出进程（退出码 75） | `worker/runner.py::run_forever` | 线程取消不掉，靠 `restart: unless-stopped` 拉干净进程 |

### 2. 修 bug：超时后写库失败 → 永远卡在「处理中」（真根因）

现场日志的关键一条：

```
TypeError: can't subtract offset-naive and offset-aware datetimes
[SQL: UPDATE garments SET status=$1, ..., processed_at=$3::TIMESTAMP WITHOUT TIME ZONE, ...]
```

`processed_at` 建模时是 `DateTime`（= `timestamp without time zone`），
而 `_fail` / 流水线落库时写的是 `now_utc()`（aware）。PostgreSQL 的 asyncpg 对
不带时区的列**拒绝 aware 值**，直接抛 `DataError`；异常从 `_fail` 里冒出去，
这件衣物就**永远留在 processing** —— 30 分钟后被 `requeue_stuck` 回收重跑，
再次超时、再次写不进去，无限循环。前端看到的就是「一直处理中 + 页面反复刷新」。

★ **SQLite 不校验时区**，所以本地 311 个测试全绿、线上才炸 —— 这次专门加了
PG 侧断言（冒烟里直接在生产那套 PG 上跑一遍 `_fail`），把这个盲区堵上。

修法：`app/core/time.py` 新增 `now_naive_utc()`，三处写 `processed_at` 全部改用它，
`updated_at` 仍用 aware（它是 `timestamptz`）。**无数据库迁移**，不需要改表结构。

### 3. 新增：worker 内存硬上限，写进 `.env`

```ini
# .env
WARDROBE_WORKER_MEM_LIMIT=1500m
```
compose 侧 `mem_limit: ${WARDROBE_WORKER_MEM_LIMIT:-1500m}` —— 即使老 `.env` 里没有这一行，
默认 1500m 也会生效（**升级不需要动 `.env`**，想调再改）。超限只会 OOM 掉 worker 容器
并自动重启，那件衣服 30 分钟后回收，宿主机不受影响。

### 验收

- 后端 pytest **311 passed / 0 failed**（新增：`now_naive_utc`、`_fail` 时区语义、
  超时判死 + 退出标记、`_fit_for_ai` 四组）。
- `smoke_release.sh` 新增 `9e) worker 内存上限`（读 `HostConfig.Memory`）与
  `9f) 超时判死写库`（**在 PG 上**真实调用 `_fail`）两节。
- 镜像只重建 api / worker（web 无改动），仍为 `1.0.0`。

---

## v1.0.0 发版后调整 — 2026-10-17（favicon / 筛选折叠 / 标签导入导出 / 413）

**用户要求**：*bug：部署后浏览器 icon 缺失，应该是缺了文件。新增功能：筛选项可以收起或展开。
新增功能：用户可导入导出配置好的筛选标签。完成后上传飞牛新的镜像，依然是 1.0.0 的版本，
要确保无损升级，不要丢用户信息*。随后追加：*批量上传时提示 413 上传失败，这个问题一起修复下*。

四件事全在 **1.0.0 内部**增量修，不升版本号（产物直接替换，NAS 上同名文件覆盖）。

### 1. 修 bug：浏览器图标（favicon）缺失

根因不在前端，在 `frontend/Dockerfile`：多阶段构建的 build 阶段**漏了 `COPY public`**。
vite 会把 `public/` 原样拷进 `dist/`，少这一行 → 镜像里 `/usr/share/nginx/html` 根本没有
`favicon.svg`。

更麻烦的是它「隐身」：nginx 的 SPA 回退 `try_files $uri $uri/ /index.html` 对任何找不到的
路径都回 `index.html`，于是 `/favicon.svg` 返回 **200 + text/html**。浏览器只在控制台留一句
MIME 警告，网络面板里看着完全正常 —— 所以这个 bug 藏了一整版。

修两处：
- `frontend/Dockerfile`：`RUN npm run build` 前补 `COPY public ./public`；
- `frontend/nginx.conf`：静态扩展名（svg/ico/png/jpg/js/css/字体/map）单列一个 location，
  `try_files $uri =404`，缺失就老实 404，不再冒充 index.html。
  ⚠️ 这条正则 location 会抢 `/api/media/*.jpg`，所以 `/api/` 必须写成 `location ^~ /api/`。

### 2. 新增：筛选栏可折叠

`OutfitsPage` 的筛选栏改成衣物页同款 `filter-panel`（标题 + 折叠箭头 + 已选计数 + 清空），
折叠状态存 localStorage，下次进页面保持上次选择。两页交互现在一致。

### 3. 新增：筛选标签配置导入 / 导出

`GET /api/tags/export?scope=garment|outfit`、`POST /api/tags/import`。

- 导出文件里**只有名字、没有 id** —— id 换个库/换个账号就全变了，没法当锚点；
  级联关系（大类 → 小类）也用名字表达：
  `{"kind":"wardrobe.tag-config","version":1,"categories":[{"name":"大类","values":[{"value":"T恤","parent":"上装"}]}]}`；
- 导入**按名字去重、只增不删**：已存在的一律跳过并计数，绝不覆盖用户现有配置；
- 新建走 `quota_service.ensure`，配额不够整单拒绝并说明还差多少；
- **无数据库迁移** —— 对「无损升级」最友好；
- 前端入口在标签管理弹窗底部：导出下载 JSON，导入选文件后给「创建 / 跳过」统计。
- 后端测试新增 `tests/test_tag_config.py`（11 项）。

### 4. 修 bug：批量上传 413

**现象**：一次选十几张照片上传，浏览器只提示「上传失败」，服务端日志里什么都没有。

**根因**：`client_max_body_size` 是**按单个请求体**算的，而前端把所有文件塞进**一个**
FormData。单张限 20 MB，三张原图就过 32m —— 请求在网关就被拒，压根没到后端。所以后端
「单张超限只跳过这一张」的容错完全没机会生效：**一张都进不去**。`.201` 上的系统 nginx
更小（25m），两张就炸。

三层一起修（改一层没用）：

| 层 | 改了什么 | 为什么 |
|---|---|---|
| 前端 | 新增 `src/lib/upload.ts`：`splitFilesByBytes()` 按字节切批，**24 MiB 一批、串行发** | 张数无关，字节数才是关键（20 张 200KB ≠ 3 张 8MB） |
| 后端 | `batch/upload` 新增 `start_index`（已建件数），命名接着上一批续编号 | 否则第二批又从 `(2)` 开始，一整墙衣服大面积重名 |
| 网关 | `client_max_body_size` 32m → **64m**；`proxy_read_timeout` 120s → **300s**；413 改回 JSON | 1 vCPU 上 24 MB 那批要几十秒，120s 会把大批量掐断 |

另外两处细节：
- 前端进度显示「第 x/y 批 · 已传 n/m」，批次内失败时报「已成功 N 张，第 x/y 批失败」——
  不会让用户以为全丢了；
- nginx 413 默认回自己的 HTML 错误页，前端解析不出 `detail`，只能显示「请求失败（413）」。
  加 `error_page 413 = @payload_too_large` 后回后端同款 `{"detail":"..."}`，前端也给了一句
  兜底人话；
- `/garments/{id}/images` 补图同样分批（同一个坑）；
- `.201` 裸机部署的站点配置以前是手工写在机器上的（改一次漏一次），现在进仓库：
  `backend/.verify/nginx-wardrobe.conf`，`deploy_ai201.sh` 会幂等同步 + `nginx -t` 后 reload。

### 验收

| 项 | 结果 |
|---|---|
| 后端单测 | **300 passed / 0 failed**（原 287 → 298 → **300**，新增 `start_index` 2 项） |
| 无损升级 `verify_upgrade.sh` | **18/18** —— 旧镜像建库灌数据 → 换新镜像 `up -d` → 各表计数 `1/3/0/8/133/0` 一致、用户表逐字段 md5 一致、alembic 未变、改过的显示名还在 |
| 发行版冒烟 `smoke_release.sh` | **47/47**（原 30 → 47） |
| 413 专项 | 35 MB 请求不再 413（旧 32m 的坑，回 400）；70 MB 返回 413 且 body 是 JSON；镜像内 nginx 是 `64m`；产物含 `start_index` |

> 踩到的一个脚本坑：`verify_upgrade.sh` 里用 `[ -f data/postgres/PG_VERSION ]` 断言「数据还在」，
> 但 PG 把目录 chown 成 uid 70 + 700，普通用户**连进目录都不行** → 误报「PG 数据不见了」，
> 而数据其实好好的（后面 4) 的计数和哈希全过）。改成 `sudo -n test -f`。教训：用权限受限的路径
> 做存在性判断，要先想清楚「谁有权看」。

---

## v1.0.0 发版后调整 — 2026-10-17（存储改绑定挂载，去掉命名卷）

**用户要求**：*不要搞存储卷，全部挂载成 compose 同文件夹下的文件目录*。

### 改动

三份 compose（源码版 `docker-compose.yml`、发行版 `docker-compose.yml`、
发行版 `docker-compose.build.yml`）统一把命名卷换成**绑定挂载**，
并删掉顶层 `volumes:` 段：

| 原来（命名卷） | 现在（绑定挂载） | 容器内 |
|---|---|---|
| `db-data` | `./data/postgres` | `/var/lib/postgresql/data` |
| `redis-data` | `./data/redis` | `/data` |
| `media-data` | `./data/media` | `/data/media` |
| `./models`（本来就是绑定） | 不变 | `/root/.u2net` |

好处：**整个部署 = 一个目录**，`tar` 一下就是完整备份，换机直接拷走；
`docker compose down -v` 也不再会带走数据（它只管命名卷）。

### 实测确认的两个关键点

- ★ **不需要手工 `chown`**。两个官方镜像的 entrypoint 都以 root 启动、自己修正属主：
  `postgres:17-alpine` 把数据目录 chown 成 **uid 70**（`drwx------`），
  `redis:7-alpine` chown 成 **uid 999**。实测两者都正常起、`pg_isready` 接受连接、
  `redis-cli ping` 回 PONG，AOF 落在 `data/redis/appendonlydir`。
  反过来手工改成自己的 uid，PostgreSQL 会以 `data directory has wrong ownership` 拒绝启动。
- ⚠️ **`data/postgres` 必须放本地盘**，不能放 SMB / NFS —— PG 的文件锁与 `fsync`
  语义在网络文件系统上不可靠。已写进 README 与 compose 头部注释。

### 验收（`out/release-1.0.0/smoke_release.sh`，断言 18 → **30**）

新增 12 条存储断言，**30/30 全过**：

- 逐个容器 `docker inspect` 校验挂载 **`type` 必须是 `bind`**，且 `source` 正好是
  `./data/{postgres,redis,media}` 与 `./models`（db / redis / api / worker 五处）；
- **`docker volume ls` 里没有任何本项目前缀的命名卷**（这正是本次改动的目的）；
- 数据真的落在绑定目录里：`data/postgres/PG_VERSION`、`data/redis/appendonlydir`、
  `data/media` 都在；
- ★ **`docker compose down -v` 之后 `data/postgres/PG_VERSION` 仍在** ——
  绑定挂载不随 `-v` 删除，这是这次改动最实在的收益。

> 踩到的两个小坑（与产品无关，是脚本自身的）：
> ① `data/postgres` 属主是 uid 70、权限 700 → 清理冒烟目录必须 `sudo rm -rf`；
> ② 冒烟目录里的 `./models` 是**软链**（省 180MB 拷贝），而 **Docker 不解析软链** ——
> `docker inspect` 报的 Source 就是软链本身的路径，不是 `readlink -f` 之后的结果。

### 交付（已回传飞牛 NAS，09:36）

首次上传（08:43）时 NAS 上还是**命名卷版**——这次改动只改了本地与 `.201` 的 release 目录，
NAS 没跟上。补推 4 个变更文件，镜像包与 `.env` 未动（哈希不变）：

| 文件 | 体积变化 | sha256（新） |
|---|---|---|
| `docker-compose.yml` | 3590 → 4832 B | `68c877bc…572cf8` |
| `docker-compose.build.yml` | 3691 → 3871 B | `4ca40500…4b93dc` |
| `README.md` | 7245 → 9738 B | `375e19eb…812e83` |
| `SHA256SUMS.txt` | 重算（7 项） | — |
| `wardrobe-1.0.0-images.tar.gz` / `.env` / `models/*.onnx` | 未动 | 与首次一致 |

README 只改了一行：`down -v` 的注释由「停 + 删命名卷」改成「本包全是绑定挂载，`./data` 原封不动」——
原句字面上自相矛盾（既说删命名卷，又说本包没有命名卷）。

复核走**两条互不依赖的链路**：

- `.201` 挂 cifs → NAS 侧 `sha256sum -c` **7/7 成功**，再 `cmp` 逐文件与 release 目录比对字节一致；
- 本机 Windows 直连 UNC（`\\10.215.171.102\workbuddy`）用 `Get-FileHash` 独立复核 **7/7 OK**，
  并确认 NAS 上那份 `docker-compose.yml` 已无顶层 `volumes:` 段。

推送脚本留档 `out/release-1.0.0/update_nas.sh`：只推变更文件 + 双侧校验，不再重传 458 MB 镜像包。

---

## v1.0.0 发版 — 2026-10-17

**首个完整版：五个镜像 + compose + `.env` + ONNX 模型打成离线部署包。**

### 发版期间修掉的 bug（发版冒烟真撞出来的，不是猜的）

- ★ **Worker 会在「迁移竞态」下变成永久僵尸**（`app/worker/runner.py`）。
  `run_forever` 整进程共用一个 `AsyncSession`，而它的兜底 `except` 只有
  `log.exception(...)` + `await _idle(poll)`，**没有 `session.rollback()`**。
  PostgreSQL 的语义是「一条语句失败 → 整个事务 abort」，同一连接上后续任何查询都只会回
  `current transaction is aborted, commands ignored until end of transaction block`。
  于是：`docker compose up -d` 同时拉起 `api` 和 `worker` → worker 的 `depends_on`
  只等 `db` healthy → 它抢在 `alembic upgrade head` 之前查询 → `relation "garments" does not exist`
  → **事务 abort 后再也没恢复过**：容器 `Up`、CPU 不高、日志每 2 秒刷同一个异常，**一件衣物都不处理**。
  - 修：`run_forever` 的兜底 `except` 与 `run_once` 的单件 `except`（含 `asyncio.TimeoutError`）
    **都先 `await session.rollback()`**。后者尤其要紧 —— 不回滚的话 `_fail()` 自己的 `UPDATE`
    也会撞上 aborted 事务，衣物要一直卡在 `processing` 等 30 分钟回收。
  - 定性证据（同一 session 上先打一张不存在的表，再不回滚重试 `select 1`）：
    ① `ProgrammingError`（预期失败）→ ② **仍然坏**（`current transaction is aborted`）
    → ③ 显式 `rollback()` 后**正常**。
  - API 侧不受影响：每请求 `async with SessionLocal()`，退出时自动回滚。
  - 详见 `handoff/KNOWN_TRAPS.md` 第 24 条。

### 发版物（`out/release-1.0.0/`）

| 文件 | 大小 | sha256 |
|---|---|---|
| `wardrobe-1.0.0-images.tar.gz` | 458,165,881 B | `297866e1fac55238fb47f7e95ddc564b5f260a84890e171677aecf0258cf90bf` |
| `docker-compose.yml`（发行版，纯镜像） | 3,590 B | `74f2201d5bb1787fe14f839726e21553b7526845a098b47eeefa468efe70b3cd` |
| `docker-compose.build.yml`（源码构建版） | 3,691 B | `789218183e3097a9d1b03587e920cc07396c2ec21e8cdf7a9a7c081a9379dc57` |
| `.env`（含随机密钥） | 3,681 B | `18d06c68a8572a8580241b449d59b6459498c0d7a8bbc10b8073f7241531222c` |
| `README.md` | 7,245 B | `4815b9f594ca4dcbeff62fc1e2baf1d642de908136ef7ed75532f60dd7a94fce` |
| `models/u2net.onnx` | 175,997,641 B | `8d10d2f3bb75ae3b6d527c77944fc5e7dcd94b29809d47a739a7a728a912b491` |
| `models/u2netp.onnx` | 4,574,861 B | `309c8469258dda742793dce0ebea8e6dd393174f89934733ecc8b14c76f4ddd8` |

镜像（`docker save` 进上面那个 tar.gz；`docker load` 后 ID 应完全一致）：

| 镜像 | image ID | 未压缩大小 |
|---|---|---|
| `wardrobe-api:1.0.0` | `sha256:9613ddb3de1ca2a3b8af07ef3b7d93cde286c4ba69c8d5e5dc4894292af2695c` | 356,866,517 B |
| `wardrobe-web:1.0.0` | `sha256:998429f22b0e2aecd4744f69bd44a9f282fb240946f5250d32d1d47401cefa3e` | 74,105,542 B |
| `wardrobe-worker:1.0.0` | `sha256:597dfe07cc00a448dfe62aa79b08fead0e52f863e36820583f9dc9179b7102cd` | 1,101,409,292 B |
| `postgres:17-alpine` | `sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24` | 423,189,986 B |
| `redis:7-alpine` | `sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499` | 57,300,241 B |

> 两个官方镜像也一起打进去了 —— 离线机器不用再联网拉。
> ⚠️ `tar.gz` 只有 458 MB 而镜像合计约 2.0 GB：containerd image store 下 `docker save`
> 导出的 blob 本来就是**压缩态**，再 gzip 几乎没有收益（462 MB → 458 MB），属正常。

### 验收

- **构建**：三个应用镜像在 `10.215.170.201`（Debian 12 / Docker 29.9.1 / 1 vCPU / 2 GB）构建成功；
  worker 镜像里 `rembg 2.0.85` / `onnxruntime 1.31.0` / `pymatting` / `scikit-image` 均在。
- **端到端冒烟**（`out/release-1.0.0/smoke_release.sh`，用**发行版 compose + `.env`** 真起一套）：
  **18 项断言全过、0 失败** —— db `healthy`、`/api/healthz` 报 `version=1.0.0`、
  `/api/readyz` 报 `database=true`、迁移到 `0007_tag_seed_flag`、引导管理员登录拿到 token、
  `/api/meta` 报 `matting_provider=rembg`、`/api/garments` 可读、worker `running` 且看得到
  `u2net.onnx`、nginx 首页与 `/wardrobe` 回退均 200。
  - ★ **竞态回归**：脚本**故意先起 worker、后起 api**，逼出「迁移前查询」；
    断言 worker 日志里 **`current transaction is aborted` 计数为 0** ——
    第一条 `relation "garments" does not exist` 照旧出现（预期内），但没有 aborted 残留 = 修复生效。
- **`docker load` 往返**：把五个 tag 全部 `rmi` 掉，再从 `wardrobe-1.0.0-images.tar.gz` 重新 `load`，
  五个 image ID 与打包时记录**逐字节一致**（含 size）。
- **打包完整性**：`gzip -dc tar.gz` 的字节流 sha256 == 原 tar 的 sha256（`096d0e95…`），
  `gzip -t` CRC 通过，tar 内 81 条目、OCI 布局（`index.json` / `oci-layout` / `blobs/`）。

### 交付

上传到飞牛 NAS（`10.215.171.102`）SMB 共享 `workbuddy/docker_images/wardrobe/`：
`wardrobe-1.0.0-images.tar.gz`、`docker-compose.yml`、`docker-compose.build.yml`、`.env`、
`README.md`、`SHA256SUMS.txt`、`models/{u2net,u2netp}.onnx`。
（由 `10.215.170.201` 直接 `mount -t cifs` 写入，传完在 NAS 侧 `sha256sum -c SHA256SUMS.txt` 复核。）

### 未做

- **未推 git**（用户明确「暂不上传 git，我还没建仓库」）。
- **未跑「上传图片 → 抠图 → 打标」的完整业务链路** —— 需要真实图片样本，留给首次部署时验。

### 部署

- 发版物落在 `projects/wardrobe/out/release-1.0.0/`；`.201` 上的构建/打包/上传脚本在
  `/data/wardrobe-release/`（`build_release.sh` / `build_r2.sh` / `pack_release.sh` /
  `smoke_release.sh` / `upload_smb.sh` / `probe_txn.py`）。

---

## 未发布 — 2026-10-17（「用抠图做封面」不该关掉复核）

**用户反馈**：*复核 AI 结果里，点完用抠图做封面，依然返回 ai 建议页，而不是直接跳首页*。

复现后确认根因在**按钮接错了回调**：`ReviewDialog` 里「用抠图做封面」走的是 `onDone`
（复核流程的终点回调），而不是 `onUpdated`（只同步列表、不关弹窗）。于是：

1. 点一下 → 弹窗**直接关掉**；
2. 名称与标签**没被采纳**、状态仍是 `needs_review`；
3. 回到衣物页，卡片上还挂着「复核 AI 结果」→ 再点又回到同一个 AI 建议页。

看着就像「点了封面却被退回 AI 建议」，而且关窗之后再也没法点「采纳并入库」——
一次操作被劈成了两趟。

### 修复

- **「用抠图做封面」改成不关弹窗**（走 `onUpdated`，与 `CutoutEditor` 保存后的处理一致）：
  设完封面弹窗留着，按钮变「已是封面」并禁用，弹窗顶部给一句提示
  「已把抠图设为封面 —— 改好名字和标签后点「采纳并入库」完成复核。」，
  接着改名字/标签，最后点「采纳并入库」一次结束。
- 与**衣物编辑弹窗里的「设封面」**（`GarmentEditor` 的 `imageAction`，同样不关窗）对齐 ——
  同一个语义在两处不该有两种行为。
- 顺手把按钮的 `title` 写清：「复核不会因此结束」。

### 验收（部署版 `10.215.170.201:8080`，`out/cover-bug/verify.js`）

**13/13 通过、0 console 错误**：
线上跑的是新前端 `index-ryS9YqRc.js`；按钮初始「用抠图做封面」→ 点击后**弹窗仍然开着**、
标题仍是「复核：…」、按钮变「已是封面」且禁用、弹窗里出现「已把抠图设为封面」提示、
「采纳并入库」仍可用；列表里该卡片封面缩略图同步换掉；再点「采纳并入库」后弹窗关闭，
**该件不再挂「复核 AI 结果」**（已入库，不再回到 AI 建议页）。

### 部署

- 只换前端 `frontend/dist` → `/data/wardrobe/web/dist`，后端与 worker 未动。
- 备份 `/data/wardrobe/web/dist.bak-20261017-062658`。

---

## 未发布 — 2026-10-16（抠图修整的视口导航）

**「一放大就只剩中心点，够不到要修的那块」**

用户反馈原话：*修整抠图的时候，如果放大，无法调整具体改哪块，只能看到中心点*。

复现下来是两件事叠在一起：**缩放的锚点写死在舞台正中**（`+` / `−` / 滑杆 / 滚轮
一律把新比例套在中心，放大后要修的地方早被推出画面），而**平移只有「空格 + 拖」和
「鼠标中键拖」两种**，都只写在底部提示文字里 —— 等于没有入口。

### 新增

- **抓手工具**（工具栏第 4 个 chip）。选中后左键直接拖画面。这是「移动视图」最像常识
  的那条路，不用先读说明。`.refine-stage.tool-pan` 给 `cursor: grab`（拖动中 `grabbing`，
  用 `:not(.panning)` 压特异度）。
- **滚轮 = 缩放，且以光标为锚点**。指针放到要修的地方再滚，那块就留在原地长大。
  **不再要求按住 Ctrl**（原实现要求 Ctrl，且锚点仍是中心）。
  `Shift + 滚轮` 放行给浏览器，用来滚弹窗。
- **导航器（左上角小地图）**。只在「放大到看不全」时出现（`scale > fitScale`），
  显示整张图缩略 + 当前视口方框；点哪儿跳哪儿，也可以拖方框扫。
  回答「我现在看的是整张图的哪个位置」。缩略图随每次 `paint` 刷新，擦掉的部分
  在缩略图里也看得出是透明。
- **方向键微调平移**（`ArrowLeft/Right/Up/Down`，`Shift` 加速到 120px/步）。
  贴着边缘修的时候比拖鼠标稳。

### 变更

- **`+` / `−` / 滑杆的缩放锚点从「舞台正中」改成「鼠标最后停留的位置」**
  （`lastAnchorRef`，由 `onPointerMove` 维护）。指针停在裙摆上点 `+`，长出来的就是
  裙摆，而不是又一次居中。鼠标没进过画面时才回落到中心。
- 底部提示文案重写：把「放大 / 移动」的每条路径点名写清，并指向左上角小地图。

### 修复（都是验收里量出来的，不是猜的）

- **导航器放右下角会被弹窗 footer 盖住** —— 1262×568 这类短视口下 `.modal-body`
  溢出 105px，导航器底部 34px 落在 footer 底下；`elementFromPoint` 打过去返回的是
  `BUTTON.btn-primary`（「保存修整结果」），点一下弹窗直接关了。
  改**左上角**（顶部从上往下排，不会被 footer 压）。
- **取景框永远是 170px（= min-height）** —— `.modal` 只有 `max-height`、**没有确定高度**，
  `.modal-body` 的高度仍由内容决定，里面再 `flex` 也分不到多余空间。
  给 `.modal-refine` 加 `height: min(calc(95vh - 24px), 880px)`，
  `.modal-refine .modal-body` 改纵向 flex，`.refine-stage` 改 `flex: 1 1 0` 吃掉剩余高度。
  1280×900 下舞台 170 → **491px**；1262×568 下 176px 且 body 溢出 **0**。
- **导航器缩略图一直是空的** —— 画布尺寸 effect 依赖 `[navBox, drawNav, canvasSize]`，
  **缺 `showNav`**：适应窗口时导航器根本没渲染（`navCanvasRef.current === null`），
  放大后才挂上去，effect 不重跑，画布永远停在 0×0。依赖补上 `showNav`。
- `CutoutEditor.tsx` 里 `drawNav` 必须定义在 `paint` **之前**（`paint` 要调它），
  否则 TS2448 / TS2454。

### 测试

- `npm run typecheck` / `npm run build` 通过（`index-De2EU7bV.js` / `index-DqINiw2J.css`）。
- **回归套件 `backend/.verify/cutout_zoom.js` 扩到 66 项、全绿**（原 50 项），
  新增：滚轮（不按 Ctrl）直接缩放 / `Shift + 滚轮`放行、抓手工具（类名、光标、拖拽位移）、
  方向键 24px 与 `Shift` 加速 120px、导航器（适应窗口时不出现 / 放大时出现 / 在左上角 /
  缩略图有内容 / 左下角命中的是它自己 / 点击不误关弹窗 / 视口方框跟着移动）。
  `BASE` 改成可被 `WARDROBE_BASE` 覆盖，同一份脚本能打本机 dev 与部署版。
- 本机 dev 两种视口（1262×568 / 1280×900）浏览器全绿，0 console 错误；
  回归项（擦除 / 恢复 / 框选重抠 / 撤销 / 重做）全部正常。
- **部署版实测**（`WARDROBE_BASE=http://10.215.170.201:8080`）：66/66、0 console 错误。
  关键数据：滚轮 24%→40%→200% 时指针下的图像像素 `(834.0, 643.8)` **漂移 0.00 个图像像素**；
  放大后半径 4px 的一笔圆心 `(835.5, 645.5)` vs 目标 `(835, 645)`、外框 `8×8`；
  导航器 `left+8 top+8`、缩略图 `67×84` 不透明 3060px、`elementFromPoint` 命中 `refine-nav`；
  桌面 1440×900 / 1366×768 / 1280×720 都不纵向翻页（取景框 491 / 366 / 320px）。
- **顺手修掉回归套件里三处「慢机器上会假失败」的写法**（1 vCPU 上真撞出来的）：
  ① 登录用固定 `waitForTimeout(1200)` —— argon2 校验 + 播种标签取值要 1~3 s，
  改成等登录表单消失（条件等待）；② 保存用固定 `waitForTimeout(2800)` —— 重编码 PNG +
  两张缩略图会超时，改成等编辑器消失；③ 方向键断言没考虑 `clampPan` 的边界 ——
  从右下极限出发只剩 30px 行程，先把画面拖到左上极限再测。
  （与 `handoff/DEPLOY-ai201.md` 里那条「固定 sleep + 紧跟断言都得重审」是同一类坑。）
- 测试用的「缩放验证件」播种到部署库后**用完即删**（`DELETE /api/garments/18`，
  级联清掉 2 张图与缩略图），复核回到 17 衣物 / 34 图。

### 部署

- 只换了前端：`frontend/dist` → `/data/wardrobe/web/dist`（nginx `:8080` 静态根，
  `/etc/nginx/sites-available/wardrobe:4`）。后端与 worker **未动**。
- 线上主 JS `index-De2EU7bV.js` sha256 `02acbe4e283dbe7c…` 与本地构建产物逐字节一致。
- 备份：`/data/wardrobe/web/dist.bak-20261016-212222`
  （回滚：`rm -rf /data/wardrobe/web/dist && cp -a <bak> /data/wardrobe/web/dist`）。

---

## 部署迁移 — 2026-10-16

**CT202（待退役）→ ai-build-debian `10.215.170.201:8080`**

不是代码变更，是**部署位置**变了：CT202（`10.215.171.12`）待退役，整套迁到内网构建机
ai-build-debian（Debian 12 / **1 vCPU J1800** / 1.9 GiB 内存 / 16 GB 系统盘 + 251 GB HDD）。
**所有服务端文件都落在 `/data`（HDD）**，一点没碰 16 GB 的系统盘。

| 项 | 位置 |
|---|---|
| 代码 | `/data/wardrobe` |
| venv | `/data/wvenv`（重建，**没拷** CT202 的 `/opt/wvenv` —— shebang 写死了 `/opt`） |
| 库 / 媒体 | `/data/wardrobe/data/dev.db` / `data/media` |
| 抠图模型 | `/data/wardrobe/models/`，由 `U2NET_HOME` 显式指定（rembg 默认写 `~/.u2net`，那在 SSD 上） |
| 单元 | `wardrobe-api` / `wardrobe-worker` / `wardrobe-backup.timer`（新增）/ nginx `:8080` |

### 新增

- **`wardrobe-backup.timer`**：每天 03:30 用 sqlite3 的在线备份 API（`.backup`）导出库到
  `/data/backup/wardrobe/`，gzip 后留 30 天，**并做 `PRAGMA integrity_check` 校验**。
  不要用 `cp` —— rollback journal 模式下会拷到半截事务。
- **`handoff/DEPLOY-ai201.md`**：迁移的完整步骤、可复现命令、为什么选裸机而不是 compose。
- `.verify/release_ai201.js`：部署版浏览器验收（由 `release_ct202.js` 改 4 处生成）。
- `.verify/deploy_ai201.sh`：往 201 发版的脚本（机器上是 `/data/wardrobe/deploy.sh`）。

### 变更

- `.verify/smoke_admin.py` 的基址改成读 `WARDROBE_API_BASE` 环境变量（默认不变），
  一份脚本能打本机 dev / CT202 / 201。
- **修 `release_ct202.js` / `release_ai201.js` 里的慢机器时序竞态**：建号后固定
  `waitForTimeout(1400)` 再断言弹窗关闭 —— 2C4G 够用，1 vCPU 不够（argon2 哈希 +
  播种整套标签取值要 1~3 s），首轮 148 项挂 3 项。改条件等待后满分。
  以后把部署目标换成更慢的机器，所有「固定 sleep + 紧跟断言」都得重审。

### 迁移里最该记住的一点

**媒体 key 是相对路径**（`garments/{owner_id}/{uuid}.jpg`），**JWT secret 两边都是默认值**。
这两件事合起来意味着：换 `media_root` 前缀不用改库，跨机器令牌直接有效。
实测 CT202 签发的 access token 打 201 的 `/api/auth/me` → **200**，用户不用重新登录。

### 验收

`smoke_admin.py` **38/38**；`release_ai201.js` **148/148**（0 console 错误）；
AI 流水线端到端 **27 s 出抠图（680×1200）→ needs_review**；
数据与迁移前完全一致（1 用户 / 17 衣物 / 1 搭配 / 34 图 / 131 标签值 / `alembic 0007`）。

---

## 未发布（批次 6）— 2026-10-16

**多用户与管理员：注册三模式 · 按人配额 · 按人配置大模型**

> 发版前先做了整树快照：`snapshots/wardrobe-v1.0.0-20261016-110712.tar.gz`
> （11.4 MB，含开发库与已构建前端），回退步骤见 `snapshots/README.md`。

### 新增

- **管理员与普通用户两级角色**（`users.is_admin`）。新增 `/api/admin/*` 一组接口，
  **每个端点都独立校验管理员**（非管理员 403，不是 404 —— 不必藏「这里有管理接口」）：
  - `GET /api/admin/users` 列表：角色、启用状态、衣物/搭配/标签用量、配额上限、
    大模型是否配好、最近活跃时间。用量口径直接复用配额那个计数器 ——
    管理页显示的数字和用户被拦时的数字对不上，是最容易起争执的地方
  - `GET /api/admin/users/{id}` 详情：上面这些 + 该用户的全部登录设备
  - `POST/PATCH/DELETE /api/admin/users[/{id}]` 建号、改资料/配额/角色/状态、删号
  - `POST /api/admin/users/{id}/password` 重置密码（**连带吊销该用户全部会话**）
  - `POST /api/admin/users/{id}/logout` 只踢下线、不改密码
  - `PUT|DELETE /api/admin/users/{id}/llm` 代设 / 清除该用户的大模型
  - 删号会连带清掉衣物 / 搭配 / 标签 / 会话 / **媒体文件**（库先删、盘后删，
    顺序反了会留下「库里没有、盘上还在」的孤儿）
- **注册三模式**：`open` 无限制自助注册 / `invite` 邀请码注册 / `closed` 关闭注册。
  存在 `app_settings` 单行表里，管理员在界面上改完**立刻生效、重启后还在**
  （`WARDROBE_ALLOW_REGISTRATION` 退化成「表里还没有设置行时的初始值」——
  否则界面上关掉注册、服务重启一次又被环境变量静默盖回来）。
  新增**公开**的 `GET /api/auth/registration`，登录页在拿到令牌之前就知道
  该不该显示注册入口、要不要邀请码。
- **邀请码**（`invite_codes` 表）：`XXXX-XXXX`，字母表去掉 `0/O/1/I/L` 这些
  看起来一样的字符，方便手抄；大小写、短横、前后空格都不敏感；可设可用次数
  （`1` = 一人一码，留空 = 不限）与有效期；吊销是软删，已用次数留着查账。
  占用写成 `UPDATE ... WHERE used_count < max_uses RETURNING` **原子**操作 ——
  写成「先 SELECT 判断再 UPDATE」时，两个人同时用同一枚码会双双通过判断、各建一个账号
  （和刷新令牌轮换是同一类并发坑，见开发说明）。
- **按人配额**：`users.garment_quota / outfit_quota / tag_quota`（`None` = 不限）。
  规则三条：**只管新增**（上限调小不会删用户已有的东西）、**批量按整批算**
  （一次传 30 张而只剩 5 个名额就整批拒绝，不要「先存 5 件再报错」）、
  **管理员豁免**（否则管理员把标签配额调成 0 就把自己锁在门外，谁也改不回来）。
  超限返回 403 并说清上限与现状；用户可在设置页自查 `GET /api/auth/quota`。
- **按用户的大模型**：`users.llm_base_url / llm_api_key / llm_model / llm_from_admin`。
  - **不默认注入**：新用户没有任何模型配置，`/meta` 报 `llm_configured=false`
  - **不同用户互不通用**：打标一律按 owner 现造 provider（`tagger_for_user`），
    A 的 Key 绝不会被拿去处理 B 的衣物
  - **用户没填时管理员可代设**：落到同一组字段上，`llm_from_admin` 记来源，
    设置页会提示「当前这份配置由管理员代设」，用户自己保存一次就归自己
  - `GET|PUT|DELETE /api/auth/llm`：接口**只回脱敏串**（`sk-abc…wxyz`，≤12 位整体打码）；
    `api_key` 是三态 —— 不传 = 保持不变、空串 = 清空、有值 = 覆盖
  - 唯一不带密钥的例外是本地 CLIP（`ai_tagger_provider=clip`）：它没有地址与 Key 可填，
    属「机器上装没装」而不是「谁的配置」，仍由运维全局开关控制
- **CLI**：`python -m app.cli seed-llm --username <名字>` 把环境变量里的 VLM 配置
  平移进指定用户（既有部署升级时用），`show-settings` 打印当前注册模式与邀请码概况。

### 前端

- **登录页**：加登录 / 注册两档 tab（只在开放注册时出现），按
  `/auth/registration` 的结果决定显示；`invite` 模式下多一个邀请码输入框，
  输入自动转大写、提示「大小写与短横不敏感」；底部一行小字说明当前注册模式。
- **管理页 `/admin`**（新增，导航项仅管理员可见）：
  - 用户表格：**用量顶到上限的单元格标黄**，提醒管理员该放配额了
  - 用户详情弹窗：三张统计卡（已用 / 上限）、资料与配额编辑、角色与状态切换、
    踢下线、重置密码、代设/清除大模型、设备列表
  - **危险区删号要输入用户名确认**，且必须与目标用户名完全一致才解锁
  - 注册设置三档单选 + 邀请码生成（一次可生成多枚）/ 复制 / 吊销
  - 普通用户直接打 `/admin` 会被挡回概览（`RequireAdmin`），服务端另有 403 兜底
- **设置页**：新增「大模型」区块（地址 / 模型名 / API Key，脱敏回显、
  一键清除、代设提示）与「配额」区块（不限 / 已用 x / 上限 y）。
  大模型区块里带一块**「怎么挑模型」说明**（`.hint-block`）：必须是能看图的
  **视觉（多模态）模型**（附 `glm-4v-flash` / `qwen-vl-max` / `gpt-4o-mini` 示例，
  并点名纯文本模型不行）、地址要填到 `/v4` / `/v1` 这一层、图片以 base64 内联发送、
  只认 JSON 回包，以及存好之后去哪儿试（衣物自动打标 / 搭配的「AI 推荐」）；
  管理员建号与代设模型处也各有一行同样的要求提示。

### 改进

- **VLM 报错带上上游的原因**（`VlmTagger._http_error`）：配错模型时用户能直接看到
  原因，而不是一个光秃秃的 `400`。上游的说明折成一行、截断 200 字符拼进提示 ——
  `model not found` = 模型名写错、`invalid content type` = 填了不看图的纯文本模型、
  `invalid api key` / `insufficient balance` 同理 —— 一路显示到「待确认」的提示里。
  搭配编辑器的「AI 推荐」按钮在未配置时，提示改为指向「设置 → 大模型」。

### 修复（都是发布冒烟里真撞出来的）

- **访问令牌没有绑定会话** —— 重置密码 / 吊销设备 / 停用账号之后，那个人手上
  那个 JWT 还能继续用（最长 30 分钟），因为 JWT 是自证的。现在访问令牌带
  `sid`（哪一次登录），每次请求都核对 `user_sessions` 那一行，
  上面三种操作**立刻**生效。代价是一次主键查询。
  > 批次 6 之前签发的旧令牌（payload 里没有 `sid`）一律当无效处理 ——
  > 上线后每人重新登录一次，比留一条「旧令牌绕过会话吊销」的后门强。
- **预置标签把配额撑爆** —— 建号时播种 ~130 个标签取值，全被算进「可添加标签数量」，
  于是 `/auth/quota` 报 `tags: {"used": 131, "limit": 3}`，配额这功能形同虚设。
  加 `tag_values.is_seed` 标记（迁移 0007 回填 286 行），配额只数用户自己新增的取值。

### 迁移

- `0006_admin`：`users` 加 7 列（三档配额 + 四个大模型字段）、建 `app_settings`
  与 `invite_codes`。`app_settings` **故意不插行** —— 插一行会钉死初始模式，
  让 `WARDROBE_ALLOW_REGISTRATION=true` 的既有部署静默失去注册入口
- `0007_tag_seed_flag`：`tag_values` 加 `is_seed`，并按默认取值目录回填

### 测试

- 新增 `tests/test_admin.py`（33 项）、`test_quota.py`（10 项）、
  `test_registration.py`（21 项）、`test_user_llm.py`（19 项），覆盖权限边界、
  三条自我保护、配额三档与批量、注册三模式与邀请码（原子占用 / 大小写不敏感 /
  吊销 / 过期）、按用户模型隔离与代设、`sid` 会话校验；
  `test_worker.py` 补一条：VLM 报错要带出上游说明（`_http_error`）
- **测试夹具从内存库换成临时文件库**：内存 SQLite 必须配 `StaticPool`，
  也就是所有会话共用同一条连接 —— 并发用例的两个请求于是落在同一个事务里，
  落败方回滚会把胜方**还没提交**的 INSERT 一起抹掉。「同一枚邀请码不能同时
  注册两个人」这类用例因此失真，只有分开连接才和 PostgreSQL 的并发语义一致
  （另加 `journal_mode=MEMORY` + `synchronous=OFF` 把速度拉回来）
- `pytest` **287 passed**（原 213 + 新增 74）
- `release.js` 新增 **H 节**：导航可见性、列表用量与接口对齐、注册三档切换与
  登录页 tab 联动、邀请码生成/吊销与计数、建号（含配额与代设模型）、
  详情弹窗、改配额、重置密码、危险区确认删除、普通用户被挡回、设置页大模型往返
- `release.js` **148 / 148**（本机 dev）、`release_ct202.js` **148 / 148**（部署版），
  均 0 console 错误（新增 2 条：设置页写清了「必须视觉模型」的要求与用法）
- `backend/.verify/smoke_admin.py` 接口冒烟 **38 / 38**
- 已部署 CT202 `10.215.171.12:8080`：替换 `app/` + `web/dist`，
  `alembic upgrade` 到 `0007_tag_seed_flag`，两个服务重启后 active；数据未动

---

## 未发布 — 2026-10-16

**搭配编辑器的素材库：等高校准 + 按标签筛选**

### 新增

- **素材库可按标签筛选**：素材库标题右侧新增「筛选」按钮，点开是勾选式弹窗，
  选完「应用」才生效。同一组里选多个是「或」，不同组之间是「且」。
  - 只列**有衣物在用**的标签（类别与取值都按 `usage_count` 过滤）——
    摆出用不到的取值等于摆一堆必然筛出空结果的按钮；顺带把历史遗留的
    同名旧类别挡在门外
  - 筛选生效时按钮标出已选数量，旁边多一个 ✕ 一键清空
  - 搜索框（衣物名）与标签筛选叠加使用；命中为空时提示并给出「清空筛选」

### 优化

- **素材库与画布等高**（桌面端）：左栏原来只有自身内容那么高，190px 的窄栏底下
  要空出 200px 上下的白边。现在三栏等高拉伸，卡片网格吃掉剩余高度，
  列表底边与画布底边齐平，一屏多显示一排素材；手机上仍回到 32vh 上限
- **主按钮终于上了主色**：`className="btn primary"` 用的是不存在的 `.primary`
  （真实类名是 `.btn-primary`），「保存搭配」「新建搭配」一直渲染成普通按钮

### 修复

- **网格行被压扁**：网格容器有确定高度后，`.olib-cell` 的 `overflow: hidden`
  让行轨自动最小尺寸变成 0，网格会把所有行压扁去凑容器高度（实测行高 61px、
  图片 119px，图溢出卡片被裁）。行轨改为 `max-content` + `align-content: start`
- **「仅显示前 60 件」的触发条件写错了**：原来比的是「命中数 > 列表长度」，
  而没有图片的衣物本来就会被过滤掉，于是 19 命中 / 18 列表也会提示被截断。
  改成只有命中数真的超过一页上限时才提示

### 测试

- `release.js` / `release_ct202.js` **各 93 项全过**（新增 G 节 23 项：三栏等高的
  实测几何、卡片未被压扁、未截断时不提分页、筛选分区与 `usage_count` 一致、
  弹窗层级命中测试、Esc 只关最上层、按标签筛出的件数与接口对齐、一键清空恢复、
  手机单列回退）
- 回归：`pytest` 213 passed、`restore.js` 13/13、`cutout_zoom.js` 全部通过

---

## v1.0.0 — 2026-10-16

**收尾批次（批次 5）+ 回填此前各批次**

### 新增

- **删除级联提示**：删衣物前先查有哪些搭配用到它，弹窗列出搭配名与各用了几层；
  默认**只删衣物、保留搭配**（被引用的层退化成「素材已删除」，层上仍留着衣物名），
  也可勾选「连同这 N 套搭配一起删除」。批量删除同样适用。
  - `DELETE /api/garments/{id}?cascade_outfits=` · `POST /api/garments/batch/delete` 支持 `cascade_outfits`
  - `GET /api/outfits/referencing` 支持一次查一批（`garment_ids` 可重复），
    不必为选中的 200 件各发一次请求
- **显示名可改**：`PATCH /api/auth/me`，设置页加编辑入口（用户名是登录凭据，不可改）
- **运行信息接口**：`GET /api/meta` 汇总版本号、抠图 / 打标 provider 与可用性、是否开放注册
- **概览页重做**：衣物 / 搭配 / 待处理三项统计（可点进对应页）、AI 服务状态、
  版本号、与实际一致的开发路线；「待处理」卡片直接跳到 `/wardrobe?attention=1`
- **「待复核」筛选落在 URL 上**：可分享、刷新不丢
- **主题三档**：跟随系统 / 浅色 / 深色，存 `localStorage`；`index.html` 内的引导脚本
  在首帧前写好 `data-theme`，不闪白。样式表改用 `data-theme` 属性选择器，
  不再依赖 `prefers-color-scheme` 媒体查询
- **错误边界**：页面某处渲染出错不再整页白屏，给出重试 / 刷新 / 复制错误信息
- **衣物看大图**：卡片上直接翻阅原图与抠图（透明图垫棋盘格）
- **统一删除确认弹窗**：`lib/confirm.tsx` 的 `confirmDialog()` 取代**全部**原生
  `window.confirm`（原生弹窗在手机上样式不可控，也无法承载「引用了它的搭配」这类结构化信息）。
  覆盖删衣物、删搭配、删衣物图片、删上身图、删标签类别 / 取值。
  确认框按 Promise 封装（`if (!(await confirmDialog({...}))) return;`），
  层级由挂在路由之后的宿主统一管理

### 修复

- **`delete_keys` 吞不下 `SystemExit`**：文件安全钩子（沙箱的删除重定向 / 批量删除守卫）
  拦下删除时是 `raise SystemExit(1)`，而它是 `BaseException` 的旁支、`except Exception`
  接不住 —— 数据库事务早已提交的删除请求会因此变成 500。改为显式吞掉
  `(Exception, SystemExit)`，并加单测钉住。
- **`.badge` 被两条规则打架**：状态角标那条把通用 `.badge` 一并变成了
  `position: absolute`，没有定位祖先的「管理员」徽章会飞到视口左上角。
  角标规则收窄为 `.gcard-thumb .badge`。
- **确认框被自己的弹窗盖住**：`.overlay` 都是 `z-index: 50`，层级由 DOM 顺序决定，
  而确认框宿主一度排在路由之前 —— 「编辑衣物时弹删除确认」这一路点不到按钮
  （元素状态却全是「可见、可用、稳定」，静态断言照样通过）。宿主改到路由之后。
- **`Esc` 一次关掉两层弹窗**：`Dialog` 给每个实例都挂 `keydown`，确认框套在编辑弹窗上时
  一次 `Esc` 会把两层一起关掉。改成只有最上面那层响应。

### 测试

- `pytest` **213 passed**（新增 15 条：级联删除 / 跨用户隔离 / 资料更新 / meta / 删文件兜底）
- 浏览器端到端 `backend/.verify/release.js`（本机 dev）**76 项全过**、
  `release_ct202.js`（部署版）**76 项全过**，均零 console 错误，
  1440×900 / 1366×768 / 390×844 无横向滚动，全程无原生 `confirm` / `alert`
- 回归：`restore.js` 13/13、`cutout_zoom.js` 全部通过
- 批量删除接口 15 轮压测 0 失败

### 部署

- CT202 `10.215.171.12:8080`：版本 `1.0.0`，前端 `index-CVQEOh6y.js` / `index-DsPFUPZY.css`，
  后端与 worker 已重启，`/api/meta` 报 `matting=rembg` / `tagger=vlm`

### 版本

- 版本号由 `0.1.0` 提升到 `1.0.0`（`app/config.py`、`frontend/package.json`、`backend/pyproject.toml`）

---

## 批次 1–4（此前交付内容摘要）

- **批次 1**：账号体系、刷新令牌轮换与多设备会话、`owner_id` 行级隔离、Alembic 迁移骨架
- **批次 2**：衣物 CRUD、双标签体系（级联 + 颜色色板）、缩略图筛选墙、签名 URL 图片访问
- **批次 3**：上传 → 抠图 Worker → 打标 Provider → 复核界面；抠图精修（擦除 / 恢复 /
  框选重抠 / 缩放平移）；批量上传、批量打标、批量删除、一键抠图打标
- **批次 4**：套装搭配编辑器（拖拽画布、图层、素材库、成图导出、上身图）、
  搭配侧标签、AI 推荐命名与标签

详见 `docs/开发说明.md` 的分批次设计说明。
