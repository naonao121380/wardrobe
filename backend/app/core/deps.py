"""FastAPI 依赖：当前用户、数据库会话、客户端信息。"""

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import decode_access_token
from app.db import get_session
from app.models.user import User

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
    user_id = decode_access_token(creds.credentials)
    if user_id is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "凭据无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def client_info(request: Request) -> dict[str, str | None]:
    """从请求里提取客户端 IP / UA。信任反代写入的 X-Forwarded-For 首段。"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        ip = fwd.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent") or ""
    return {"ip_address": ip, "user_agent": ua[:512] or None}
