"""首启引导：按环境变量创建管理员账号。

幂等 —— 同名用户已存在则直接返回。
"""

import logging

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.user import User
from app.services.tags import seed_default_categories

log = logging.getLogger(__name__)


async def ensure_bootstrap_admin() -> None:
    s = get_settings()
    if not s.bootstrap_username or not s.bootstrap_password:
        return
    async with SessionLocal() as db:
        existing = (
            await db.execute(select(User).where(User.username == s.bootstrap_username))
        ).scalar_one_or_none()
        if existing is not None:
            return
        db.add(
            User(
                username=s.bootstrap_username,
                password_hash=hash_password(s.bootstrap_password),
                display_name=s.bootstrap_username,
                is_admin=True,
                is_active=True,
            )
        )
        await db.flush()
        user_id = (
            await db.execute(select(User.id).where(User.username == s.bootstrap_username))
        ).scalar_one()
        cats, values = await seed_default_categories(db, user_id)
        await db.commit()
        log.info("已创建引导管理员账号：%s（默认标签 %d 类别 / %d 取值）", s.bootstrap_username, cats, values)
