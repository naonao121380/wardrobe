"""鉴权路由：登录 / 刷新 / 登出 / 设备管理 / 改密。

令牌模型：
- access token：JWT，短期（默认 30 分钟），只放内存。
- refresh token：随机串，**只有 sha256 摘要入库**，支持轮换与单设备吊销。
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.deps import CurrentUser, DbSession, client_info
from app.core.security import (
    create_access_token,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)
from app.core.time import as_aware, now_utc
from app.models.user import User, UserSession
from app.schemas.auth import (
    LoginRequest,
    PasswordChangeRequest,
    RefreshRequest,
    RegisterRequest,
    SessionInfo,
    TokenResponse,
    UserOut,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _guess_device(user_agent: str | None) -> str:
    """从 UA 粗略推断设备名，仅用于设备列表展示。"""
    if not user_agent:
        return "未知设备"
    ua = user_agent.lower()
    os_name = "未知系统"
    for key, label in (
        ("windows", "Windows"),
        ("iphone", "iPhone"),
        ("ipad", "iPad"),
        ("android", "Android"),
        ("mac os", "macOS"),
        ("linux", "Linux"),
    ):
        if key in ua:
            os_name = label
            break
    browser = "浏览器"
    for key, label in (
        ("edg/", "Edge"),
        ("chrome/", "Chrome"),
        ("firefox/", "Firefox"),
        ("safari/", "Safari"),
    ):
        if key in ua:
            browser = label
            break
    return f"{browser} · {os_name}"


async def _issue_tokens(
    db: AsyncSession,
    user: User,
    request: Request,
    device_label: str | None = None,
) -> TokenResponse:
    """签发一对令牌，并在库里登记一条设备会话。"""
    s = get_settings()
    raw_refresh, digest = new_refresh_token()
    info = client_info(request)
    ua = info["user_agent"]
    label = (device_label or "").strip() or _guess_device(ua)

    us = UserSession(
        user_id=user.id,
        refresh_token_hash=digest,
        device_label=label[:128],
        user_agent=ua,
        ip_address=info["ip_address"],
        expires_at=now_utc().replace(microsecond=0) + timedelta(days=s.refresh_token_days),
        last_seen_at=now_utc(),
    )
    db.add(us)
    await db.commit()
    await db.refresh(us)

    access, exp = create_access_token(user.id)
    return TokenResponse(
        access_token=access,
        refresh_token=raw_refresh,
        expires_at=exp,
        session_id=us.id,
        user=UserOut.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 不区分「用户不存在」与「密码错误」，避免用户名枚举
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户名或密码错误")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "账号已停用")
    return await _issue_tokens(db, user, request, payload.device_label)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DbSession) -> TokenResponse:
    s = get_settings()
    if not s.allow_registration:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前未开放注册")

    dup = (
        await db.execute(select(User).where(User.username == payload.username))
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")
    if payload.email:
        dup_email = (
            await db.execute(select(User).where(User.email == payload.email))
        ).scalar_one_or_none()
        if dup_email is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已被占用")

    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return await _issue_tokens(db, user, request, payload.device_label)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest, request: Request, db: DbSession) -> TokenResponse:
    digest = hash_refresh_token(payload.refresh_token)
    us = (
        await db.execute(
            select(UserSession).where(UserSession.refresh_token_hash == digest)
        )
    ).scalar_one_or_none()
    if us is None or us.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "刷新令牌无效")
    if as_aware(us.expires_at) <= now_utc():
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "刷新令牌已过期")

    user = await db.get(User, us.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")

    # 轮换：旧会话立即作废，签发新会话（检测到重放时旧令牌已不可用）
    label = us.device_label
    us.revoked_at = now_utc()
    await db.commit()
    return await _issue_tokens(db, user, request, label)


@router.post("/logout")
async def logout(payload: RefreshRequest, db: DbSession) -> dict[str, bool]:
    digest = hash_refresh_token(payload.refresh_token)
    await db.execute(
        update(UserSession)
        .where(UserSession.refresh_token_hash == digest, UserSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()
    return {"ok": True}


@router.post("/logout-all")
async def logout_all(user: CurrentUser, db: DbSession) -> dict[str, int]:
    res = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()
    return {"revoked": res.rowcount or 0}


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.get("/sessions", response_model=list[SessionInfo])
async def list_sessions(user: CurrentUser, db: DbSession) -> list[SessionInfo]:
    rows = (
        (
            await db.execute(
                select(UserSession)
                .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
                .order_by(UserSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [SessionInfo.model_validate(r) for r in rows]


@router.delete("/sessions/{session_id}")
async def revoke_session(session_id: int, user: CurrentUser, db: DbSession) -> dict[str, bool]:
    us = await db.get(UserSession, session_id)
    if us is None or us.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    us.revoked_at = now_utc()
    await db.commit()
    return {"ok": True}


@router.post("/password")
async def change_password(
    payload: PasswordChangeRequest, user: CurrentUser, db: DbSession
) -> dict[str, int]:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "当前密码不正确")
    user.password_hash = hash_password(payload.new_password)
    # 改密后吊销该用户全部会话，强制所有设备重新登录
    res = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()
    return {"revoked": res.rowcount or 0}
