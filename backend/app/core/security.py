"""密码哈希与令牌签发。"""

import hashlib
import secrets
from datetime import datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.config import get_settings
from app.core.time import now_utc

_hasher = PasswordHasher()


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """返回 ``(明文, sha256 摘要)``。明文只在签发时返回给客户端一次。"""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def create_access_token(user_id: int, session_id: int | None = None) -> tuple[str, datetime]:
    """返回 ``(token, 过期时间)``。

    ``sid`` 把访问令牌绑到**具体那一次登录**（``user_sessions`` 那一行）。有了它，
    管理员重置密码 / 点「吊销该设备」才能**立刻**生效 —— 否则 JWT 是自证的，
    令牌没到期之前（默认 30 分钟）被踢的人照样能继续用。
    """
    s = get_settings()
    now = now_utc()
    exp = now + timedelta(minutes=s.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
        "typ": "access",
    }
    if session_id is not None:
        payload["sid"] = session_id
    token = jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)
    return token, exp


def decode_access_token(token: str) -> tuple[int, int | None] | None:
    """解出 ``(user_id, session_id)``；令牌无效返回 ``None``。

    ``session_id`` 为 ``None`` 表示这是一个**批次 6 之前签发的旧令牌**（payload 里
    没有 ``sid``）。调用方一律当无效处理 —— 上线后每人重新登录一次，
    比留一条「旧令牌绕过会话吊销」的后门强。
    """
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None
    sid = payload.get("sid")
    return user_id, int(sid) if isinstance(sid, int) else None
