"""标签体系。

单品与搭配是**两套完全独立**的标签体系，靠 ``scope`` 区分（见评估报告 §8.1）。
两者共用同一组表，但彼此的类别与取值互不可见、互不影响。

层级
----
标签是**两层**结构（类别 → 取值），但支持一层「取值挂取值」的父子关系，用来做
「大类 → 小类」的级联：

- ``TagCategory.parent_category_id``：声明本类别是谁的子级。例如「小类」的父类别是
  「大类」。为 NULL 表示它是顶层类别（大类 / 颜色 / 材质）。
- ``TagValue.parent_value_id``：本取值挂在父类别下的哪个取值上。例如「风衣」的父值
  是「外套」。

于是「选了大类=外套，小类只列风衣/夹克/西装」不需要额外表。
颜色 / 材质这类正交维度的父类别是 NULL，取值也不挂父值。
"""

import enum

from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint, false
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

    #: 父类别。为 NULL 表示顶层类别（大类 / 颜色 / 材质 / 季节…）；
    #: 「小类」指向「大类」，于是它的取值可以挂到具体的大类取值上。
    #:
    #: ⚠️ 外键用 ``SET NULL`` 而不是 ``CASCADE``：删掉「大类」只是把「小类」降级成
    #: 顶层类别，不该顺手把用户的另一整个维度连同取值一起抹掉。
    parent_category_id: Mapped[int | None] = mapped_column(
        ForeignKey("tag_categories.id", ondelete="SET NULL"), index=True, nullable=True
    )

    values: Mapped[list["TagValue"]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        order_by="TagValue.sort_order, TagValue.id",
        foreign_keys="TagValue.category_id",
    )
    parent_category: Mapped["TagCategory | None"] = relationship(
        remote_side="TagCategory.id", back_populates="child_categories", foreign_keys=[parent_category_id]
    )
    child_categories: Mapped[list["TagCategory"]] = relationship(
        back_populates="parent_category", foreign_keys=[parent_category_id]
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

    #: 是不是**系统预置**的取值（建号时播种的那些）。
    #:
    #: 配额（``User.tag_quota``「可添加标签数量」）只数用户自己新增的 ——
    #: 播种一次就有 ~130 个取值，把它们算进去的话任何小于 130 的配额一上来就爆。
    is_seed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false(), nullable=False
    )

    #: 父取值，指向**父类别**下的某个取值。只有「小类」这类子类别才会用到；
    #: 为 NULL 表示未归类（在级联筛选里会归到「未归类」一档）。
    #:
    #: ⚠️ 同样是 ``SET NULL``：删掉大类取值「外套」时，「风衣」降级为未归类，
    #: 而不是被连带删除、顺带把衣物上的标签也解掉。
    parent_value_id: Mapped[int | None] = mapped_column(
        ForeignKey("tag_values.id", ondelete="SET NULL"), index=True, nullable=True
    )

    category: Mapped[TagCategory] = relationship(
        back_populates="values", foreign_keys=[category_id]
    )
    parent_value: Mapped["TagValue | None"] = relationship(
        remote_side="TagValue.id", back_populates="child_values", foreign_keys=[parent_value_id]
    )
    child_values: Mapped[list["TagValue"]] = relationship(
        back_populates="parent_value", foreign_keys=[parent_value_id]
    )
