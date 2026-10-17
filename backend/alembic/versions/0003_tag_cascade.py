"""标签取值支持父子级联（大类 → 小类）

Revision ID: 0003_tag_cascade
Revises: 0002_garments
Create Date: 2026-10-14

加两列自引用外键：

- ``tag_categories.parent_category_id`` —— 声明本类别是谁的子级（「小类」的父是「大类」）
- ``tag_values.parent_value_id``        —— 本取值挂在父类别下的哪个取值上（「风衣」的父是「外套」）

两个外键都是 **ON DELETE SET NULL**，不是 CASCADE —— 删「大类」只把「小类」降级成
顶层类别，删大类取值「外套」只把「风衣」变成未归类，都不该顺手抹掉用户的另一份数据。

数据迁移：给既有用户的「小类」类别挂上同 scope 的「大类」作为父类别。
历史的小类取值没有父值，一律留 NULL（前端归到「未归类」一档），不做猜测。

⚠️ SQLite 不支持 ALTER TABLE ADD CONSTRAINT，所以用 ``batch_alter_table`` 走
「建新表 → 拷数据 → 换名」那套；PostgreSQL 上它就是普通 ALTER。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_tag_cascade"
down_revision: str | None = "0002_garments"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("tag_categories") as batch:
        batch.add_column(sa.Column("parent_category_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tag_categories_parent",
            "tag_categories",
            ["parent_category_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_tag_categories_parent_category_id", ["parent_category_id"])

    with op.batch_alter_table("tag_values") as batch:
        batch.add_column(sa.Column("parent_value_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_tag_values_parent", "tag_values", ["parent_value_id"], ["id"], ondelete="SET NULL"
        )
        batch.create_index("ix_tag_values_parent_value_id", ["parent_value_id"])

    # 「小类」挂到同 scope 的「大类」下
    conn = op.get_bind()
    rows = conn.execute(
        sa.text(
            "SELECT child.id AS child_id, parent.id AS parent_id "
            "FROM tag_categories AS child "
            "JOIN tag_categories AS parent "
            "  ON parent.owner_id = child.owner_id AND parent.scope = child.scope "
            "WHERE child.name = '小类' AND parent.name = '大类'"
        )
    ).fetchall()
    for row in rows:
        conn.execute(
            sa.text("UPDATE tag_categories SET parent_category_id = :p WHERE id = :c"),
            {"p": row.parent_id, "c": row.child_id},
        )


def downgrade() -> None:
    with op.batch_alter_table("tag_values") as batch:
        batch.drop_index("ix_tag_values_parent_value_id")
        batch.drop_constraint("fk_tag_values_parent", type_="foreignkey")
        batch.drop_column("parent_value_id")

    with op.batch_alter_table("tag_categories") as batch:
        batch.drop_index("ix_tag_categories_parent_category_id")
        batch.drop_constraint("fk_tag_categories_parent", type_="foreignkey")
        batch.drop_column("parent_category_id")
