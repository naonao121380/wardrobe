"""首启引导：按环境变量创建管理员账号。

幂等 —— 同名用户已存在则直接返回。
"""

import logging

from sqlalchemy import select

from app.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models.user import User

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
        await db.commit()
        log.info("已创建引导管理员账号：%s", s.bootstrap_username)
