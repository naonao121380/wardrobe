"""用户与会话模型。

设计约束（见评估报告 §8.1）：用户隔离从第一天起就是行级 ``owner_id``。
本文件只有身份表；业务表（衣物 / 搭配）在批次 2 引入时同样带 ``owner_id``。

批次 6 起这里多了三组「用户自带」的东西：

- **配额**（``*_quota``）：管理员给每个用户设的上限，``None`` 表示不限。
  只约束「用户自己新增」，不追溯已有数据 —— 把配额调小不会让已有衣物消失。
- **大模型配置**（``llm_*``）：**不默认注入**，由用户自己填模型地址 / API Key /
  模型名称；用户没填时管理员可以代他填（``llm_from_admin`` 记来源，界面据此显示
  「由管理员配置」）。不同用户的模型互不通用，调用一律按 owner 取自己的那一份 ——
  见 :mod:`app.services.llm`。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # ---------- 配额（None = 不限） ----------
    #: 可添加的标签取值数量（两个 scope 合计）
    tag_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 可添加的衣物数量
    garment_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: 可添加的搭配数量
    outfit_quota: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ---------- 大模型（OpenAI 兼容接口） ----------
    #: 模型地址，如 ``https://open.bigmodel.cn/api/paas/v4``
    llm_base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: API Key。**明文存储** —— 它要能回填给用户看，也只在服务端使用；
    #: 接口一律脱敏返回（见 app/schemas/admin.py 的 mask_key）。
    llm_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 模型名称，如 ``glm-4v-flash``
    llm_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 这份配置是不是管理员代设的（用户自己填的为 False）
    llm_from_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def llm_configured(self) -> bool:
        """模型地址与模型名都填了才算「配好了」—— 少一个都调不通。"""
        return bool((self.llm_base_url or "").strip() and (self.llm_model or "").strip())


class UserSession(Base, TimestampMixin):
    """一个刷新令牌 = 一台设备的一次登录。支持列出设备与「登出所有设备」。"""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    # 只存 sha256 摘要，明文刷新令牌仅签发时返回一次
    refresh_token_hash: Mapped[str] = mapped_column(
        String(128), unique=True, index=True, nullable=False
    )
    device_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
