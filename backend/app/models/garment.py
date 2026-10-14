"""衣物单品与图片。"""

from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class Garment(Base, TimestampMixin):
    __tablename__ = "garments"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

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
