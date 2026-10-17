"""批次 3：衣物 AI 流水线状态机

Revision ID: 0004_ai_pipeline
Revises: 0003_tag_cascade
Create Date: 2026-10-14

给 ``garments`` 加三列，把 DECISIONS D6 的状态机落到库里::

    pending ──► processing ──► needs_review ──► ready
                    │                              ▲
                    └──────► failed ──► (重试) ─────┘

- ``status``        —— 流水线状态，默认 ``ready``（不跑 AI 时衣物照样可用）
- ``ai_suggestion`` —— Worker 产出的候选结果（JSON 字符串），前端复核界面消费
- ``processed_at``  —— Worker 最后一次处理完成的时间，便于排查卡住的任务

``status`` 带 server_default 而不是只在 ORM 层给默认值 —— 迁移前的既有衣物
全部落在 ``ready``，不会因为升级就变成「待复核」把用户的衣橱清空。

⚠️ 和 0003 一样走 ``batch_alter_table``，SQLite 才能重建表。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_ai_pipeline"
down_revision: str | None = "0003_tag_cascade"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

#: 与 app/models/garment.py::GarmentStatus 保持一致
STATUSES = ("pending", "processing", "needs_review", "ready", "failed")


def upgrade() -> None:
    with op.batch_alter_table("garments") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(16),
                nullable=False,
                server_default="ready",
            )
        )
        batch.add_column(sa.Column("ai_suggestion", sa.Text(), nullable=True))
        batch.add_column(sa.Column("processed_at", sa.DateTime(), nullable=True))
        batch.create_index("ix_garments_status", ["status"])

    # PostgreSQL 上加个 CHECK，SQLite 的 batch 重建会丢约束，所以只在 PG 上建
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        allowed = ", ".join(f"'{s}'" for s in STATUSES)
        op.execute(
            sa.text(
                f"ALTER TABLE garments ADD CONSTRAINT ck_garments_status "
                f"CHECK (status IN ({allowed}))"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text("ALTER TABLE garments DROP CONSTRAINT IF EXISTS ck_garments_status")
        )

    with op.batch_alter_table("garments") as batch:
        batch.drop_index("ix_garments_status")
        batch.drop_column("processed_at")
        batch.drop_column("ai_suggestion")
        batch.drop_column("status")
