"""套装搭配与画布图层（批次 4）。

一件搭配 = 一块画布 + 若干图层 + 若干成图 + 搭配侧标签（季节 / 风格 / 色系）。

坐标为什么全部归一化
--------------------
图层的位置与大小一律存**相对画布的比例**，不存像素：

- ``x`` / ``y``      —— 图层中心在画布里的位置，0~1（0.5 = 画布正中）
- ``w_ratio``        —— 图层显示宽度 ÷ 画布宽度
- ``rotation``       —— 角度，与屏幕无关

这样同一份数据在手机窄屏和桌面宽屏上回显**一模一样**，导出成图时只要指定画布像素
尺寸（默认 900×1200）即可，不需要记住「当初是在多大的屏幕上摆的」。

素材被删了怎么办
----------------
``garment_id`` / ``garment_image_id`` 两个外键都是 **ON DELETE SET NULL**：
删掉一件衣物或它的一张图，搭配**不会被连带删掉**，而是那一层退化成
「素材已删除」（``name_snapshot`` 还留着衣物名，界面上能看出缺了什么）。
搭配是用户花时间摆出来的成果，不能因为清理衣橱就整份消失。
"""

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


#: 导出成图时的画布像素尺寸（3:4）。前端显示的画布比例与它一致，所见即所得。
CANVAS_WIDTH = 900
CANVAS_HEIGHT = 1200


class Outfit(Base, TimestampMixin):
    """一套搭配。它自己不带图 —— 成图在 :class:`OutfitImage` 里，可以有多张。"""

    __tablename__ = "outfits"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: 画布底色（十六进制）。摆浅色衣服时换成深色背景更看得清
    background: Mapped[str] = mapped_column(String(16), default="#ffffff", nullable=False)

    items: Mapped[list["OutfitItem"]] = relationship(
        back_populates="outfit", cascade="all, delete-orphan"
    )
    images: Mapped[list["OutfitImage"]] = relationship(
        back_populates="outfit", cascade="all, delete-orphan"
    )
    tag_links: Mapped[list["OutfitTag"]] = relationship(
        back_populates="outfit", cascade="all, delete-orphan"
    )


class OutfitItem(Base, TimestampMixin):
    """画布上的一层（一件衣物的一张图）。

    ``z`` 越大越靠上；同一搭配内允许重复引用同一件衣物（比如左右两只鞋各摆一层）。
    """

    __tablename__ = "outfit_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    outfit_id: Mapped[int] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )

    #: 引用的衣物；衣物被删时置 NULL，图层保留（见模块文档）
    garment_id: Mapped[int | None] = mapped_column(
        ForeignKey("garments.id", ondelete="SET NULL"), index=True, nullable=True
    )
    #: 用的是哪一张图（优先抠图）；图被删时同样置 NULL
    garment_image_id: Mapped[int | None] = mapped_column(
        ForeignKey("garment_images.id", ondelete="SET NULL"), nullable=True
    )
    #: 衣物名快照：衣物删掉后图层还知道这层原来是什么
    name_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)

    x: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    y: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    w_ratio: Mapped[float] = mapped_column(Float, default=0.5, nullable=False)
    rotation: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    z: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    flip_h: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    opacity: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)

    outfit: Mapped[Outfit] = relationship(back_populates="items")


class OutfitImage(Base, TimestampMixin):
    """搭配的图，一套可以有多张。

    ``kind``：
    - ``result`` —— 编辑器保存时合成出来的搭配图（只留最新一张，卡片默认显示它）
    - ``worn``   —— **上身图**：用户上传的真人穿搭照。可多张，也**可以一张都不传** ——
      搭配本身已经能表达「怎么穿」，上身图只是额外记录「穿上去什么样」。
    - ``upload`` —— 早期别名，与 ``worn`` 同义。写库时统一归一成 ``worn``；
      老数据里可能还留着这个值，读的时候两者都要认。

    排序即「卡片默认显示哪张」：``result`` 固定 ``sort_order=0``（没有就退回第一张），
    其余按上传先后排在后面。``sort_order`` 最小的那张就是搭配的封面（``cover_url``）。
    """

    __tablename__ = "outfit_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    outfit_id: Mapped[int] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"), index=True, nullable=False
    )
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), default="result", nullable=False)

    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_small_key: Mapped[str] = mapped_column(String(255), nullable=False)
    thumb_medium_key: Mapped[str] = mapped_column(String(255), nullable=False)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bytes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), default="image/png", nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    outfit: Mapped[Outfit] = relationship(back_populates="images")


class OutfitTag(Base):
    """搭配 ↔ 标签取值（季节 / 风格 / 色系）的多对多关联。"""

    __tablename__ = "outfit_tags"
    __table_args__ = (UniqueConstraint("outfit_id", "tag_value_id", name="uq_outfit_tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    outfit_id: Mapped[int] = mapped_column(
        ForeignKey("outfits.id", ondelete="CASCADE"), index=True, nullable=False
    )
    tag_value_id: Mapped[int] = mapped_column(
        ForeignKey("tag_values.id", ondelete="CASCADE"), index=True, nullable=False
    )

    outfit: Mapped[Outfit] = relationship(back_populates="tag_links")
