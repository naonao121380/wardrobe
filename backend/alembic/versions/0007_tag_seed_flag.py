"""批次 6 补丁：给标签取值加「是否系统预置」标记

Revision ID: 0007_tag_seed_flag
Revises: 0006_admin
Create Date: 2026-10-16

为什么要这一列
--------------
批次 6 引入了「可添加标签数量」配额。但建号时会播种一整套默认标签
（大类 8 + 小类 81 + 颜色一套 + 搭配侧季节/风格/色系），**一次就有 ~130 个取值** ——
如果配额把这些也算进去，任何小于 130 的配额一上来就是「已超限」，
这功能等于没有。

所以给取值加 ``is_seed``：播种时置 ``true``，配额只数 ``is_seed = false``
（用户自己新增的）。见 ``app/services/quota.py::_extra_where``。

**回填**：既有数据里没有这一列，按「类别名 + 取值名」去当前默认表里对一遍，
对得上的标成预置。对不上的一律留 ``false``（宁可把用户自建的算进去，
也不要把用户的东西当成预置而漏计）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_tag_seed_flag"
down_revision: str | None = "0006_admin"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _preset_pairs() -> list[tuple[str, str]]:
    """(类别名, 取值名) 的预置表。与 ``app/services/tags.py`` 用同一份常量。"""
    from app.services.tags import DEFAULT_CASCADE_VALUES, DEFAULT_VALUES

    pairs: list[tuple[str, str]] = []
    for cats in DEFAULT_VALUES.values():
        for category_name, values in cats.items():
            pairs += [(category_name, v) for v in values]

    for groups in DEFAULT_CASCADE_VALUES.values():
        # 子类别名就是级联表里的键（「小类」），取值挂在各自的父取值下
        for child_name, by_parent in groups.items():
            for _parent, values in by_parent.items():
                pairs += [(child_name, v) for v in values]
    return pairs


def upgrade() -> None:
    with op.batch_alter_table("tag_values") as batch:
        batch.add_column(
            sa.Column(
                "is_seed",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    conn = op.get_bind()
    pairs = _preset_pairs()
    marked = 0
    for category_name, value in pairs:
        res = conn.execute(
            sa.text(
                "UPDATE tag_values SET is_seed = :yes WHERE value = :value AND category_id IN "
                "(SELECT id FROM tag_categories WHERE name = :cat)"
            ),
            {"yes": True, "value": value, "cat": category_name},
        )
        marked += res.rowcount or 0
    print(f"    回填 is_seed：预置对 {len(pairs)} 组，命中 {marked} 行")


def downgrade() -> None:
    with op.batch_alter_table("tag_values") as batch:
        batch.drop_column("is_seed")
