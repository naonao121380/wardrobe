"""批次 4：套装搭配（画布 + 图层 + 成图 + 搭配侧标签）

Revision ID: 0005_outfits
Revises: 0004_ai_pipeline
Create Date: 2026-10-15

新增四张表::

    outfits         一套搭配（名称 / 备注 / 画布底色）
    outfit_items    画布上的图层（归一化坐标 + 旋转 + 层序）
    outfit_images   成图（合成的 result 或用户上传的 upload），一套多张
    outfit_tags     搭配 ↔ 标签取值（季节 / 风格 / 色系）

两个外键都是 **ON DELETE SET NULL**：删衣物 / 删图不会连带删掉搭配，只让那一层
退化成「素材已删除」（衣物名存在 ``name_snapshot`` 里，界面能看出缺了什么）。
搭配是用户花时间摆出来的成果，不该因为清理衣橱就整份消失 —— 见
``app/models/outfit.py`` 的模块文档。

⚠️ 新建表不用 ``batch_alter_table``（那是为了给 SQLite 改既有表才用的）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_outfits"
down_revision: str | None = "0004_ai_pipeline"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outfits",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("background", sa.String(16), nullable=False, server_default="#ffffff"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )

    op.create_table(
        "outfit_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outfit_id", sa.Integer(), sa.ForeignKey("outfits.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("garment_id", sa.Integer(), sa.ForeignKey("garments.id", ondelete="SET NULL"),
                  nullable=True, index=True),
        sa.Column("garment_image_id", sa.Integer(),
                  sa.ForeignKey("garment_images.id", ondelete="SET NULL"), nullable=True),
        sa.Column("name_snapshot", sa.String(128), nullable=True),
        sa.Column("x", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("y", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("w_ratio", sa.Float(), nullable=False, server_default="0.5"),
        sa.Column("rotation", sa.Float(), nullable=False, server_default="0"),
        sa.Column("z", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flip_h", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("opacity", sa.Float(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )

    op.create_table(
        "outfit_images",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outfit_id", sa.Integer(), sa.ForeignKey("outfits.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("owner_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("kind", sa.String(16), nullable=False, server_default="result"),
        sa.Column("storage_key", sa.String(255), nullable=False),
        sa.Column("thumb_small_key", sa.String(255), nullable=False),
        sa.Column("thumb_medium_key", sa.String(255), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("height", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_type", sa.String(64), nullable=False, server_default="image/png"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    )

    op.create_table(
        "outfit_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outfit_id", sa.Integer(), sa.ForeignKey("outfits.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.Column("tag_value_id", sa.Integer(), sa.ForeignKey("tag_values.id", ondelete="CASCADE"),
                  nullable=False, index=True),
        sa.UniqueConstraint("outfit_id", "tag_value_id", name="uq_outfit_tag"),
    )


def downgrade() -> None:
    op.drop_table("outfit_tags")
    op.drop_table("outfit_images")
    op.drop_table("outfit_items")
    op.drop_table("outfits")
