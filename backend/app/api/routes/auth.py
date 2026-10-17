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
from app.core.time import now_utc
from app.models.user import User, UserSession
from app.schemas.auth import (
    LlmSettingsIn,
    LlmSettingsOut,
    LoginRequest,
    PasswordChangeRequest,
    ProfileUpdate,
    QuotaOut,
    RefreshRequest,
    RegisterRequest,
    RegistrationPolicyOut,
    SessionInfo,
    TokenResponse,
    UserOut,
)
from app.services import quota as quota_service
from app.services import system as system_service
from app.services.llm import apply_llm_settings, clear_llm_settings, llm_settings_out
from app.services.tags import seed_default_categories

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

    access, exp = create_access_token(user.id, us.id)
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


@router.get("/registration", response_model=RegistrationPolicyOut)
async def registration_policy(db: DbSession) -> RegistrationPolicyOut:
    """公开的注册策略。**不需要登录** —— 登录页要用它决定显不显示注册入口。"""
    settings = await system_service.get_app_settings(db)
    mode = settings.registration_mode
    return RegistrationPolicyOut(mode=mode, invite_required=mode == "invite")


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterRequest, request: Request, db: DbSession) -> TokenResponse:
    """自助注册。开不开、要不要邀请码，由库里的注册模式说了算。

    三种模式（见 :mod:`app.services.system`）：

    - ``open``   谁都能注册；
    - ``invite`` 必须带一个可用邀请码，且**和建用户在同一笔事务里原子占用一次**；
    - ``closed`` 直接拒绝，账号只能由管理员创建。
    """
    settings = await system_service.get_app_settings(db)
    mode = settings.registration_mode
    if mode == "closed":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "当前未开放注册，请联系管理员创建账号")

    invite = None
    if mode == "invite":
        if not (payload.invite_code or "").strip():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "本次注册需要邀请码")
        invite = await system_service.find_usable_code(db, payload.invite_code)
        if invite is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "邀请码无效、已过期或用尽")

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

    if invite is not None and not await system_service.consume_code(db, invite.id):
        # 同一枚「一人一码」被两个人同时用：落败方到此为止，别建出第二个账号
        raise HTTPException(status.HTTP_409_CONFLICT, "邀请码刚刚被用掉了，请换一个")

    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    await db.flush()
    await seed_default_categories(db, user.id)
    await db.commit()
    await db.refresh(user)
    return await _issue_tokens(db, user, request, payload.device_label)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(payload: RefreshRequest, request: Request, db: DbSession) -> TokenResponse:
    digest = hash_refresh_token(payload.refresh_token)
    now = now_utc()

    # 原子 compare-and-swap：只有第一个请求能把 revoked_at 从 NULL 改掉。
    # 并发刷新（多标签页 / React StrictMode 双跑 effect）时落败方拿不到行，直接 401。
    # 若写成「先 SELECT 判断再 UPDATE」，两个并发请求会双双通过判断、各建一个会话。
    row = (
        await db.execute(
            update(UserSession)
            .where(
                UserSession.refresh_token_hash == digest,
                UserSession.revoked_at.is_(None),
                UserSession.expires_at > now,
            )
            .values(revoked_at=now)
            .returning(UserSession.user_id, UserSession.device_label)
        )
    ).first()
    await db.commit()

    if row is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "刷新令牌无效或已过期")

    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在或已停用")

    return await _issue_tokens(db, user, request, row.device_label)


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


@router.patch("/me", response_model=UserOut)
async def update_me(payload: ProfileUpdate, user: CurrentUser, db: DbSession) -> UserOut:
    """改自己的资料。目前只开放显示名 —— 用户名是登录凭据，改了会让所有设备对不上。

    传 ``null`` 表示清空（回落到用户名显示）；只传想改的字段，别的保持不动。
    """
    fields = payload.model_dump(exclude_unset=True)
    if "display_name" in fields:
        name = (fields["display_name"] or "").strip()
        user.display_name = name or None
    await db.commit()
    await db.refresh(user)
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


# ---------- 自己的大模型配置 ----------


@router.get("/llm", response_model=LlmSettingsOut)
async def read_my_llm(user: CurrentUser) -> LlmSettingsOut:
    """我的模型配置（API Key 只回脱敏串）。"""
    return llm_settings_out(user)


@router.put("/llm", response_model=LlmSettingsOut)
async def update_my_llm(
    payload: LlmSettingsIn, user: CurrentUser, db: DbSession
) -> LlmSettingsOut:
    """自己填模型地址 / API Key / 模型名称。

    ``api_key`` 不传 = 保持不变（只改模型名时不用重新粘密钥）；传空串 = 清空。
    自己填过之后 ``llm_from_admin`` 归零 —— 这份配置算用户自己的了。
    """
    apply_llm_settings(
        user,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
        from_admin=False,
    )
    await db.commit()
    await db.refresh(user)
    return llm_settings_out(user)


@router.delete("/llm", response_model=LlmSettingsOut)
async def clear_my_llm(user: CurrentUser, db: DbSession) -> LlmSettingsOut:
    clear_llm_settings(user)
    await db.commit()
    await db.refresh(user)
    return llm_settings_out(user)


# ---------- 自己的配额用量 ----------


@router.get("/quota", response_model=QuotaOut)
async def read_my_quota(user: CurrentUser, db: DbSession) -> QuotaOut:
    """我的用量与上限。设置页给自己看，心里有数。"""
    used = await quota_service.usage(db, user.id)
    return QuotaOut(**quota_service.quota_summary(user, used))
