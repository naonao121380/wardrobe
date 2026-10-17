"""全局运行设置（单行表）与注册邀请码。

为什么单开一张表而不是塞进 ``config.Settings``
------------------------------------------------
``Settings`` 来自环境变量，进程启动时读一次就固定了 —— 而「注册模式」是管理员
在界面上随时要改的东西，改了必须立刻生效、并且重启后还在。环境变量做不到这一点，
所以落到库里；``Settings.allow_registration`` 退化成**首次建表时的初始值**
（见 :func:`app.services.system.registration_mode`）。

单行表用 ``id=1`` 固定主键，不给「多行取哪一行」留解释空间。
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin

#: 注册模式。``open`` = 无限制自助注册；``invite`` = 需要邀请码；``closed`` = 关闭注册。
REGISTRATION_MODES = ("open", "invite", "closed")


class AppSetting(Base, TimestampMixin):
    """全局开关。**永远只有 id=1 这一行**（``get_app_settings`` 负责按需创建）。"""

    __tablename__ = "app_settings"

    id: Mapped[int] = mapped_column(primary_key=True)

    registration_mode: Mapped[str] = mapped_column(
        String(16), default="closed", nullable=False
    )


class InviteCode(Base, TimestampMixin):
    """注册邀请码。

    一次可创建多个（``max_uses=1`` 的「一人一码」或 ``None`` 的「不限次」都支持），
    用在 ``invite`` 注册模式下。吊销是软删（``revoked_at``），已用掉的记录要能查账。
    """

    __tablename__ = "invite_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    #: 备注，例如「给小王」「2026 秋招」—— 对不上人时只能靠它回忆
    note: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: 生成者。管理员被删时置空，邀请码本身留着（可能已经发出去了）
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: 最多可用次数，None = 不限
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def remaining(self) -> int | None:
        """还能用几次；``None`` 表示不限次。"""
        if self.max_uses is None:
            return None
        return max(self.max_uses - self.used_count, 0)
