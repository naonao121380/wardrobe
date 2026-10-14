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
    version: str = "0.1.0"
    debug: bool = False

    # 数据层
    database_url: str = "postgresql+asyncpg://wardrobe:wardrobe@db:5432/wardrobe"
    redis_url: str = "redis://redis:6379/0"

    # 鉴权
    jwt_secret: str = Field(default="dev-only-change-me-at-least-32-bytes-long", min_length=32)
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = 30
    refresh_token_days: int = 30

    # 注册策略：默认关闭，首个管理员由 bootstrap 环境变量创建
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

    # 逗号分隔，避免复杂类型的 JSON 解析问题
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
