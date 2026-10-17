"""运行时配置。所有环境变量统一使用 WARDROBE_ 前缀。"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="WARDROBE_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "wardrobe"
    version: str = "1.0.0"
    debug: bool = False

    # 数据层
    database_url: str = "postgresql+asyncpg://wardrobe:wardrobe@db:5432/wardrobe"
    redis_url: str = "redis://redis:6379/0"

    # 鉴权
    jwt_secret: str = Field(default="dev-only-change-me-at-least-32-bytes-long", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    # 注册策略。
    # ⚠️ 批次 6 起**注册模式以库里的 app_settings 为准**（管理员在界面上随时可改，
    # 重启后仍然生效）；这里的值只在「库里还没有设置行」时决定初始模式。
    allow_registration: bool = False
    bootstrap_username: str | None = None
    bootstrap_password: str | None = None

    # 媒体文件根目录
    media_root: str = "/data/media"
    # 媒体签名 URL 有效期（秒）。图片走 <img> 标签，无法带 Authorization 头，
    # 因此改用签名 URL —— 见 app/core/media.py。
    media_url_ttl_seconds: int = 7 * 24 * 3600
    max_upload_bytes: int = 20 * 1024 * 1024
    thumbnail_small_px: int = 240
    thumbnail_medium_px: int = 720

    # ---------- AI 流水线的输入尺寸 ----------
    # ⚠️ 抠图前**必须**把图缩到这个长边以内，**不要**直接喂原图。
    # 手机原图 3500×4500（≈15.7 MP）时，rembg 的 alpha matting 是 pymatting 的
    # closed-form 求解，在**原尺寸**上算：内存与耗时都是超线性的，1~4 核的小机器
    # 会被一张图啃光内存、连宿主机一起拖死（2026-10-17 真实事故：worker 涨到 4 GB、
    # 队列不动、宿主卡死强制断电）。缩到 1600 后同一张图约 1250×1600（≈2 MP），
    # 内存降一个数量级，1 vCPU 上也能在十几秒内跑完。
    # 抠图结果只用来展示（前端大图也就 720~1600 级别），1600 已经够用。
    # 设成 0 = 不缩放（不推荐，除非你确定机器很强）。
    ai_input_max_edge: int = 1600

    # 逗号分隔，避免复杂类型的 JSON 解析问题
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # ---------- 批次 3：AI 流水线（Worker） ----------
    # provider 一律可降级：装不上 / 没配 key 就退回 none，衣橱照样能用（DECISIONS D7）
    ai_matting_provider: str = "none"  # none / rembg
    # ⚠️ 批次 6 起打标**不再有全局 VLM 配置**：模型地址 / API Key / 模型名称
    # 由每个用户自己填（管理员可代填），见 app/services/llm.py 与 app/models/user.py。
    # 这里只剩「本地模型」这一档可控：设成 clip 时，没配模型的用户回落到本机 CLIP。
    ai_tagger_provider: str = "none"  # none / clip

    worker_poll_seconds: float = 2.0  # 队列空时的休眠
    worker_batch_size: int = 2  # 一轮最多捞几件
    worker_stuck_minutes: int = 30  # processing 卡多久算超时，退回 pending 重跑
    worker_job_timeout_seconds: float = 600.0  # 单件处理硬超时，超时判 failed，别让队列堵死
    # 超时后是否**主动退出进程**（默认开）。
    # 原因：推理跑在线程里，`asyncio.wait_for` 只能取消 await，取消不掉线程。
    # 卡住的那个线程会一直占着单核 + 一块推理内存，后续任务全排在它后面等下一个超时，
    # 表现就是「队列一直处理中、内存只涨不降」。退出后由 compose 的
    # `restart: unless-stopped` 拉起干净进程（超时那件已判 failed，不会重复吃雷）。
    worker_hard_exit_on_timeout: bool = True

    # 抠图模型（rembg 的 session 名）。
    #   轻量首选 silueta（43MB，2C4G 友好，比 u2netp 细很多）
    #   精度优先 u2net（176MB，轮廓更稳）；isnet-general-use 更细但同样重
    #   还想再进一步用 birefnet-general（400MB+，慢，低配机器不推荐）
    # 模型文件要预置到 ~/.u2net/ 或 $WARDROBE_MODEL_DIR，下载不动时 Worker 会降级
    matting_model: str = "u2net"

    # alpha matting：在不透明/透明之间算一层 trimap，给边缘做羽化。
    # 关掉会得到「硬切」的边（毛绒、针织边缘会很假）；打开每张多花 0.3~1s。
    matting_alpha_matting: bool = True
    matting_foreground_threshold: int = 240  # 判定为「确定前景」的阈值
    matting_background_threshold: int = 10  # 判定为「确定背景」的阈值
    matting_erode_size: int = 10  # trimap 的腐蚀半径，边缘毛躁时调大

    # CLIP 零样本兜底：需要额外装 torch + open_clip，没装就自动降级 none
    clip_model: str = "ViT-B-32"
    clip_pretrained: str = "laion2b_s34b_b79k"
    clip_threshold: float = 0.18  # 低于该相似度就不要，宁缺毋滥

    # VLM（OpenAI 兼容接口）—— ⚠️ 默认**不注入**：留空即「没有配」，AI 打标不可用，
    # 由用户在设置页填自己的模型地址 / API Key / 模型名称（或管理员代填）。
    # 下面三项留作**运维侧的批量预填**用：`python -m app.cli seed-llm --username <u>`
    # 会把它们写进指定用户的配置，方便把老部署平移到按用户配置。
    vlm_base_url: str | None = None
    vlm_api_key: str | None = None
    vlm_model: str = "glm-4v-flash"
    vlm_timeout_seconds: float = 90.0

    # ---------- 框选重抠（API ↔ Worker）----------
    # 抠图模型只在 Worker 镜像里（DECISIONS D5），而「框选重抠」是同步接口，
    # 天然落在 API 进程。所以二选一：
    #   • 配了 worker_refine_url → API 把活转给 Worker 的内部接口（Docker 部署）；
    #   • 留空                → 在当前进程里算（裸机 / 本机开发，装了 rembg 才有效）。
    # ⚠️ 别填成对外地址：Worker 那个接口没有鉴权，只该走容器网络。
    worker_refine_url: str = ""
    worker_refine_timeout_seconds: float = 120.0
    # Worker 侧内部 HTTP 端口，0 = 不起（不想开这个口子时设 0）。
    # 只监听容器网络：发行版 compose 没有 publish 这个端口，外面打不到。
    worker_refine_port: int = 8001

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
