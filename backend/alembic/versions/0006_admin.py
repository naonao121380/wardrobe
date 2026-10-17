"""批次 6：多用户管理 —— 用户配额、按用户大模型配置、注册模式与邀请码

Revision ID: 0006_admin
Revises: 0005_outfits
Create Date: 2026-10-16

三件事::

1. ``users`` 加 7 列：
   - 配额 ``tag_quota`` / ``garment_quota`` / ``outfit_quota``（NULL = 不限）
   - 大模型 ``llm_base_url`` / ``llm_api_key`` / ``llm_model``（不默认注入，用户自填）
   - 来源标记 ``llm_from_admin``（这份配置是不是管理员代设的）

2. 新表 ``app_settings`` —— 全局开关的单行表，目前只放注册模式。
   ⚠️ **故意不在迁移里插行**：插了就等于把「注册模式」钉死在 ``closed``，
   而 ``WARDROBE_ALLOW_REGISTRATION=true`` 的既有部署会静默失去注册入口。
   缺失的行由 ``app.services.system.get_app_settings()`` 按首次读取时的配置补上。

3. 新表 ``invite_codes`` —— 邀请码。``max_uses`` NULL = 不限次，
   吊销走软删 ``revoked_at``（已用掉的记录要能查账）。

⚠️ 改既有表走 ``batch_alter_table``，SQLite 才能重建。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_admin"
down_revision: str | None = "0005_outfits"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("tag_quota", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("garment_quota", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("outfit_quota", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("llm_base_url", sa.String(512), nullable=True))
        batch.add_column(sa.Column("llm_api_key", sa.Text(), nullable=True))
        batch.add_column(sa.Column("llm_model", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column(
                "llm_from_admin",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.create_table(
        "app_settings",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "registration_mode", sa.String(16), nullable=False, server_default="closed"
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "invite_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("note", sa.String(128), nullable=True),
        sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("max_uses", sa.Integer(), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_invite_codes_code", "invite_codes", ["code"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_invite_codes_code", table_name="invite_codes")
    op.drop_table("invite_codes")
    op.drop_table("app_settings")

    with op.batch_alter_table("users") as batch:
        batch.drop_column("llm_from_admin")
        batch.drop_column("llm_model")
        batch.drop_column("llm_api_key")
        batch.drop_column("llm_base_url")
        batch.drop_column("outfit_quota")
        batch.drop_column("garment_quota")
        batch.drop_column("tag_quota")
