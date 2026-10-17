"""全局设置（注册模式）与邀请码的领域逻辑。

注册模式三档，存在 ``app_settings`` 单行表里：:

    open    无限制自助注册 —— 谁都能注册
    invite  邀请码注册 —— 必须带一个可用的邀请码
    closed  关闭注册 —— 账号由管理员创建

``Settings.allow_registration`` 只在**第一次**读设置（表里还没行）时决定初始值，
之后就以库里的为准 —— 否则管理员在界面上开了注册，重启一次又被环境变量盖回去。
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.time import as_aware, now_utc
from app.models.system import REGISTRATION_MODES, AppSetting, InviteCode

log = logging.getLogger(__name__)

#: 邀请码字母表：去掉 0/O/1/I/L 这些看起来一样的字符，方便用户手抄
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_CODE_LEN = 8


def _initial_mode() -> str:
    """表里还没有设置行时，按环境变量决定初始注册模式。"""
    return "open" if get_settings().allow_registration else "closed"


async def get_app_settings(db: AsyncSession) -> AppSetting:
    """拿全局设置（没有就按当前配置补一行）。"""
    row = (await db.execute(select(AppSetting).where(AppSetting.id == 1))).scalar_one_or_none()
    if row is not None:
        return row

    row = AppSetting(id=1, registration_mode=_initial_mode())
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        # 两个请求同时补行，落败方拿现成的
        await db.rollback()
        row = (await db.execute(select(AppSetting).where(AppSetting.id == 1))).scalar_one()
        return row
    await db.refresh(row)
    log.info("初始化全局设置：注册模式 = %s", row.registration_mode)
    return row


async def set_registration_mode(db: AsyncSession, mode: str) -> AppSetting:
    if mode not in REGISTRATION_MODES:
        raise ValueError(f"未知的注册模式：{mode}")
    row = await get_app_settings(db)
    row.registration_mode = mode
    await db.commit()
    await db.refresh(row)
    return row


# ---------- 邀请码 ----------


def generate_code() -> str:
    """生成一个 ``XXXX-XXXX`` 的邀请码（去掉易混字符）。"""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(_CODE_LEN))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_code(code: str | None) -> str:
    """把用户输入归一化：去空格、转大写、补上中间那个短横。

    用户抄码时可能漏掉短横、写成小写、或者前后带空格，这些都该被当成同一个码。
    """
    cleaned = "".join(ch for ch in (code or "").upper() if ch.isalnum())
    if len(cleaned) == _CODE_LEN:
        return f"{cleaned[:4]}-{cleaned[4:]}"
    return cleaned


async def generate_unique_codes(db: AsyncSession, count: int) -> list[str]:
    """批量生成不重复（且库里也没有）的码。"""
    existing = set(
        (await db.execute(select(InviteCode.code))).scalars().all()
    )
    codes: list[str] = []
    while len(codes) < count:
        code = generate_code()
        if code in existing or code in codes:
            continue
        codes.append(code)
    return codes


def is_usable(code: InviteCode, now: datetime | None = None) -> bool:
    """这个码现在还能用吗（未吊销 / 没过期 / 还有次数）。"""
    if code.revoked_at is not None:
        return False
    stamp = now or now_utc()
    if code.expires_at is not None and as_aware(code.expires_at) <= stamp:
        return False
    if code.max_uses is not None and code.used_count >= code.max_uses:
        return False
    return True


async def find_usable_code(db: AsyncSession, raw: str | None) -> InviteCode | None:
    """按用户输入找可用的邀请码（找不到 / 不可用都返回 None）。"""
    normalized = normalize_code(raw)
    if not normalized:
        return None
    code = (
        await db.execute(select(InviteCode).where(InviteCode.code == normalized))
    ).scalar_one_or_none()
    if code is None or not is_usable(code):
        return None
    return code


async def consume_code(db: AsyncSession, code_id: int) -> bool:
    """原子占用一次。返回是否抢到。

    写成 ``UPDATE ... WHERE used_count < max_uses RETURNING`` 而不是
    「先 SELECT 判断再 UPDATE」：后者在两个人同时用同一个「一人一码」注册时，
    会双双通过判断、各建一个账号（和刷新令牌轮换是同一类坑，见 routes/auth.py）。
    """
    stmt = (
        update(InviteCode)
        .where(
            InviteCode.id == code_id,
            InviteCode.revoked_at.is_(None),
            or_(
                InviteCode.max_uses.is_(None),
                InviteCode.used_count < InviteCode.max_uses,
            ),
            or_(InviteCode.expires_at.is_(None), InviteCode.expires_at > now_utc()),
        )
        .values(used_count=InviteCode.used_count + 1)
        .returning(InviteCode.id)
    )
    row = (await db.execute(stmt)).first()
    return row is not None
