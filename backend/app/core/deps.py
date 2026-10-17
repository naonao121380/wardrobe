"""FastAPI 依赖：当前用户、数据库会话、客户端信息。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.core.time import as_aware, now_utc
from app.db import get_session
from app.models.user import User, UserSession

_bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_session)]


async def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    session: DbSession,
) -> User:
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "未提供凭据", headers={"WWW-Authenticate": "Bearer"}
        )
    claims = decode_access_token(creds.credentials)
    if claims is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "凭据无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user_id, session_id = claims
    if session_id is None:
        # 没有 sid 的旧令牌：解不出「是哪次登录」，也就无法判断有没有被吊销
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "登录状态已升级，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")

    # 每次都核对会话行：重置密码 / 吊销设备 / 登出后，手上那个访问令牌立刻失效，
    # 而不是「再等最多 30 分钟」。代价是一次主键查询。
    us = await session.get(UserSession, session_id)
    if (
        us is None
        or us.user_id != user_id
        or us.revoked_at is not None
        or as_aware(us.expires_at) <= now_utc()
    ):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def get_admin_user(user: CurrentUser) -> User:
    """管理员专属端点用。非管理员一律 403（不是 404 —— 不必藏「这里有管理接口」）。"""
    if not user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return user


AdminUser = Annotated[User, Depends(get_admin_user)]


def client_info(request: Request) -> dict[str, str | None]:
    """从请求里提取客户端 IP / UA。信任反代写入的 X-Forwarded-For 首段。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent") or ""
    return {"ip_address": ip, "user_agent": ua[:512] or None}
