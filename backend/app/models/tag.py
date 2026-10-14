"""标签体系。

单品与搭配是**两套完全独立**的标签体系，靠 ``scope`` 区分（见评估报告 §8.1）。
两者共用同一组表，但彼此的类别与取值互不可见、互不影响。
"""

import enum

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin


class TagScope(str, enum.Enum):
    """标签体系的作用域。"""

    garment = "garment"  # 单品
    outfit = "outfit"  # 套装搭配


class TagCategory(Base, TimestampMixin):
    """标签类别，例如「大类」「小类」「颜色」「材质」，用户可自定义增删。"""

    __tablename__ = "tag_categories"
    __table_args__ = (
        UniqueConstraint("owner_id", "scope", "name", name="uq_tag_category_owner_scope_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    scope: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    values: Mapped[list["TagValue"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="TagValue.sort_order, TagValue.id",
    )


class TagValue(Base, TimestampMixin):
    """类别下的具体取值，例如「外套」「米白」「羊毛」。"""

    __tablename__ = "tag_values"
    __table_args__ = (UniqueConstraint("category_id", "value", name="uq_tag_value_category_value"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("tag_categories.id", ondelete="CASCADE"), index=True, nullable=False
    )
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    category: Mapped[TagCategory] = relationship(back_populates="values")
