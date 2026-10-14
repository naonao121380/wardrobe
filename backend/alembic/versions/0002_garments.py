"""garments + 标签体系（单品 / 搭配共用表，靠 scope 区分）

Revision ID: 0002_garments
Revises: 0001_initial
Create Date: 2026-10-14

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_garments"
down_revision: str | None = "0001_initial"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "tag_categories",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("owner_id", "scope", "name", name="uq_tag_category_owner_scope_name"),
    )
    op.create_index("ix_tag_categories_owner_id", "tag_categories", ["owner_id"])
    op.create_index("ix_tag_categories_scope", "tag_categories", ["scope"])

    op.create_table(
        "tag_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "category_id",
            sa.Integer(),
            sa.ForeignKey("tag_categories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
        sa.UniqueConstraint("category_id", "value", name="uq_tag_value_category_value"),
    )
    op.create_index("ix_tag_values_owner_id", "tag_values", ["owner_id"])
    op.create_index("ix_tag_values_category_id", "tag_values", ["category_id"])

    op.create_table(
        "garments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        *_timestamps(),
    )
    op.create_index("ix_garments_owner_id", "garments", ["owner_id"])

    op.create_table(
        "garment_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "garment_id",
            sa.Integer(),
            sa.ForeignKey("garments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("kind", sa.String(length=16), nullable=False, server_default="original"),
        sa.Column("storage_key", sa.String(length=255), nullable=False),
        sa.Column("thumb_small_key", sa.String(length=255), nullable=False),
        sa.Column("thumb_medium_key", sa.String(length=255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(length=64), nullable=False, server_default="image/jpeg"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        *_timestamps(),
    )
    op.create_index("ix_garment_images_garment_id", "garment_images", ["garment_id"])
    op.create_index("ix_garment_images_owner_id", "garment_images", ["owner_id"])

    op.create_table(
        "garment_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "garment_id",
            sa.Integer(),
            sa.ForeignKey("garments.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tag_value_id",
            sa.Integer(),
            sa.ForeignKey("tag_values.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("garment_id", "tag_value_id", name="uq_garment_tag"),
    )
    op.create_index("ix_garment_tags_garment_id", "garment_tags", ["garment_id"])
    op.create_index("ix_garment_tags_tag_value_id", "garment_tags", ["tag_value_id"])

    # 为已有用户补齐默认标签类别。新用户由应用层在创建时播种（app/services/tags.py）。
    defaults: dict[str, list[str]] = {
        "garment": ["大类", "小类", "颜色", "材质"],
        "outfit": ["季节", "风格", "色系"],
    }
    conn = op.get_bind()
    user_ids = [row[0] for row in conn.execute(sa.text("SELECT id FROM users")).fetchall()]
    insert_sql = sa.text(
        "INSERT INTO tag_categories (owner_id, scope, name, sort_order, created_at, updated_at) "
        "VALUES (:owner_id, :scope, :name, :sort_order, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    )
    for user_id in user_ids:
        for scope, names in defaults.items():
            for index, name in enumerate(names):
                conn.execute(
                    insert_sql,
                    {"owner_id": user_id, "scope": scope, "name": name, "sort_order": index},
                )


def downgrade() -> None:
    op.drop_index("ix_garment_tags_tag_value_id", table_name="garment_tags")
    op.drop_index("ix_garment_tags_garment_id", table_name="garment_tags")
    op.drop_table("garment_tags")

    op.drop_index("ix_garment_images_owner_id", table_name="garment_images")
    op.drop_index("ix_garment_images_garment_id", table_name="garment_images")
    op.drop_table("garment_images")

    op.drop_index("ix_garments_owner_id", table_name="garments")
    op.drop_table("garments")

    op.drop_index("ix_tag_values_category_id", table_name="tag_values")
    op.drop_index("ix_tag_values_owner_id", table_name="tag_values")
    op.drop_table("tag_values")

    op.drop_index("ix_tag_categories_scope", table_name="tag_categories")
    op.drop_index("ix_tag_categories_owner_id", table_name="tag_categories")
    op.drop_table("tag_categories")
