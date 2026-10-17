"""衣物单品与图片。"""

from datetime import datetime  # noqa: F401  (processed_at 的类型注解用到)

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class GarmentStatus:
    """批次 3 的流水线状态（DECISIONS D6）。

    不用 enum 落库：状态会随流水线演进增减，字符串 + 白名单校验比 DB enum 好改。
    """

    PENDING = "pending"  # 已入队，等 Worker 捞
    PROCESSING = "processing"  # Worker 正在抠图 / 打标
    NEEDS_REVIEW = "needs_review"  # 有候选结果，等用户确认
    READY = "ready"  # 已确认，正常出现在衣橱墙
    FAILED = "failed"  # 处理失败，可重试

    ALL = (PENDING, PROCESSING, NEEDS_REVIEW, READY, FAILED)

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.ALL


class Garment(Base, TimestampMixin):
    __tablename__ = "garments"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    #: 流水线状态，默认 ready —— 不跑 AI 时衣橱照样能用
    status: Mapped[str] = mapped_column(
        String(16), default=GarmentStatus.READY, server_default=GarmentStatus.READY,
        nullable=False, index=True,
    )
    #: Worker 产出的候选结果（JSON 字符串），复核界面消费；确认后清空
    ai_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Worker 最后一次处理完成的时刻；卡住的任务靠它排查
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    images: Mapped[list["GarmentImage"]] = relationship(
        back_populates="garment",
        cascade="all, delete-orphan",
        order_by="GarmentImage.sort_order, GarmentImage.id",
    )
    tag_links: Mapped[list["GarmentTag"]] = relationship(
        back_populates="garment", cascade="all, delete-orphan"
    )


class GarmentImage(Base, TimestampMixin):
    """一张衣物图片 + 两档预生成缩略图。

    ``kind`` 为 ``original``（用户上传的原图）或 ``cutout``（批次 3 的抠图结果）。
    列表页按 ``sort_order`` 取第一张作为封面。
    """

    __tablename__ = "garment_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    garment_id: Mapped[int] = mapped_column(
        ForeignKey("garments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), default="original", nullable=False)

    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_small_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_medium_key: Mapped[str] = mapped_column(String(255), nullable=False)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), default="image/jpeg", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    garment: Mapped[Garment] = relationship(back_populates="images")


class GarmentTag(Base):
    """衣物 ↔ 标签取值 的多对多关联。"""

    __tablename__ = "garment_tags"
    __table_args__ = (UniqueConstraint("garment_id", "tag_value_id", name="uq_garment_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    garment_id: Mapped[int] = mapped_column(
        ForeignKey("garments.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tag_value_id: Mapped[int] = mapped_column(
        ForeignKey("tag_values.id", ondelete="CASCADE"), index=True, nullable=False
    )

    garment: Mapped[Garment] = relationship(back_populates="tag_links")
