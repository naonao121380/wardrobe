"""模型公共部件。"""

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """创建 / 更新时间戳。

    注意：SQLite 不保存时区信息，PG 保存。任何跨库的 datetime 比较都必须先经过
    ``app.core.time.as_aware`` 归一化，否则会抛 naive/aware 混用的 TypeError。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
