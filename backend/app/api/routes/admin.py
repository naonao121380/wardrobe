"""管理员接口：用户管理、配额、代设大模型、注册模式与邀请码。

**全部端点都要求管理员身份**（``AdminUser`` 依赖），非管理员一律 403。

几条刻意的保护：

- **不能删自己、不能停用自己** —— 误操作一次就把自己锁在门外，代价太大。
- **不能取消最后一个管理员**（删号 / 降级 / 停用三条路都拦）：系统里没有管理员的
  时候，注册模式、配额、别人的密码就再也改不动了，只能进容器敲 CLI。
- **管理员不受配额限制**（见 :mod:`app.services.quota`），否则把自己配成 0 就再也
  改不回来。
- 删用户**连带删数据与媒体文件**，且删文件放在 commit 之后：库里先删干净，
  再动磁盘，避免留下「库里没有、盘上还在」的孤儿。
"""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import AdminUser, DbSession
from app.core.media import delete_keys
from app.core.security import hash_password
from app.core.time import now_utc
from app.models.garment import Garment
from app.models.outfit import Outfit
from app.models.system import InviteCode
from app.models.user import User, UserSession
from app.schemas.admin import (
    AdminLlmUpdate,
    AdminPasswordReset,
    AdminUserCreate,
    AdminUserDetailOut,
    AdminUserOut,
    AdminUserUpdate,
    InviteCodeCreateIn,
    InviteCodeOut,
    QuotasOut,
    RegistrationSettingsIn,
    RegistrationSettingsOut,
    UserStatsOut,
)
from app.schemas.auth import QuotaItem, SessionInfo
from app.services import quota as quota_service
from app.services import system as system_service
from app.services.llm import apply_llm_settings, clear_llm_settings, llm_settings_out
from app.services.tags import seed_default_categories

router = APIRouter(prefix="/admin", tags=["admin"])


# ---------- 统计 ----------


async def _counts(db: AsyncSession, owner_ids: list[int]) -> dict[int, dict[str, int]]:
    """一批用户的衣物 / 搭配 / 标签用量 + 活跃设备数。

    ⚠️ 用量口径与 :mod:`app.services.quota` **必须一致**（标签只数用户自建的，
    不含播种的预置取值）。管理页看到的数字和用户被拦时的数字对不上，
    是最容易起争执的地方，所以这里直接复用配额那个计数器。
    """
    out: dict[int, dict[str, int]] = {
        uid: {"garments": 0, "outfits": 0, "tags": 0, "sessions": 0} for uid in owner_ids
    }
    if not owner_ids:
        return out

    for key, kind in (("garments", "garment"), ("outfits", "outfit"), ("tags", "tag")):
        for owner_id, total in (await quota_service.count_by_owner(db, owner_ids, kind)).items():
            if owner_id in out:
                out[owner_id][key] = total

    rows = (
        await db.execute(
            select(UserSession.user_id, func.count())
            .where(
                UserSession.user_id.in_(owner_ids),
                UserSession.revoked_at.is_(None),
            )
            .group_by(UserSession.user_id)
        )
    ).all()
    for owner_id, total in rows:
        if owner_id in out:
            out[owner_id]["sessions"] = int(total)
    return out


async def _last_seen(db: AsyncSession, owner_ids: list[int]) -> dict[int, object]:
    """最近一次活跃时间（所有设备里最新的 last_seen_at）。"""
    if not owner_ids:
        return {}
    rows = (
        await db.execute(
            select(UserSession.user_id, func.max(UserSession.last_seen_at))
            .where(UserSession.user_id.in_(owner_ids))
            .group_by(UserSession.user_id)
        )
    ).all()
    return {owner_id: last for owner_id, last in rows}


def _quotas_out(user: User, used: dict[str, int]) -> QuotasOut:
    return QuotasOut(
        garments=QuotaItem(used=used.get("garments", 0), limit=user.garment_quota),
        outfits=QuotaItem(used=used.get("outfits", 0), limit=user.outfit_quota),
        tags=QuotaItem(used=used.get("tags", 0), limit=user.tag_quota),
    )


def _stats_out(used: dict[str, int]) -> UserStatsOut:
    return UserStatsOut(
        garments=used.get("garments", 0),
        outfits=used.get("outfits", 0),
        tags=used.get("tags", 0),
        sessions=used.get("sessions", 0),
    )


async def _get_user(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    return user


async def _admin_count(db: AsyncSession) -> int:
    return int(
        (
            await db.execute(
                select(func.count()).select_from(User).where(User.is_admin.is_(True))
            )
        ).scalar_one()
    )


# ---------- 用户 ----------


@router.get("/users", response_model=list[AdminUserOut])
async def list_users(_: AdminUser, db: DbSession) -> list[AdminUserOut]:
    """全部用户 + 各自的用量与配额。按 id 排序（建号顺序，稳定好看）。"""
    users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    ids = [u.id for u in users]
    counts = await _counts(db, ids)
    seen = await _last_seen(db, ids)
    return [
        AdminUserOut(
            id=u.id,
            username=u.username,
            display_name=u.display_name,
            email=u.email,
            is_admin=u.is_admin,
            is_active=u.is_active,
            created_at=u.created_at,
            last_seen_at=seen.get(u.id),  # type: ignore[arg-type]
            stats=_stats_out(counts[u.id]),
            quotas=_quotas_out(u, counts[u.id]),
            llm=llm_settings_out(u),
        )
        for u in users
    ]


async def _detail(db: AsyncSession, user: User) -> AdminUserDetailOut:
    counts = await _counts(db, [user.id])
    seen = await _last_seen(db, [user.id])
    sessions = (
        (
            await db.execute(
                select(UserSession)
                .where(UserSession.user_id == user.id)
                .order_by(UserSession.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    base = AdminUserOut(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        last_seen_at=seen.get(user.id),  # type: ignore[arg-type]
        stats=_stats_out(counts[user.id]),
        quotas=_quotas_out(user, counts[user.id]),
        llm=llm_settings_out(user),
    )
    return AdminUserDetailOut(
        **base.model_dump(), sessions=[SessionInfo.model_validate(s) for s in sessions]
    )


@router.post("/users", response_model=AdminUserDetailOut, status_code=status.HTTP_201_CREATED)
async def create_user(payload: AdminUserCreate, _: AdminUser, db: DbSession) -> AdminUserDetailOut:
    """管理员建号。默认标签体系照常播种（否则新用户进去连标签都没有）。"""
    dup = (
        await db.execute(select(User.id).where(User.username == payload.username))
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被占用")
    if payload.email:
        dup_email = (
            await db.execute(select(User.id).where(User.email == payload.email))
        ).scalar_one_or_none()
        if dup_email is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已被占用")

    user = User(
        username=payload.username,
        email=payload.email,
        display_name=payload.display_name or payload.username,
        password_hash=hash_password(payload.password),
        is_admin=payload.is_admin,
        garment_quota=payload.garment_quota,
        outfit_quota=payload.outfit_quota,
        tag_quota=payload.tag_quota,
    )
    if payload.llm_base_url or payload.llm_model:
        apply_llm_settings(
            user,
            base_url=payload.llm_base_url,
            model=payload.llm_model,
            api_key=payload.llm_api_key,
            from_admin=True,
        )
    db.add(user)
    await db.flush()
    await seed_default_categories(db, user.id)
    await db.commit()
    await db.refresh(user)
    return await _detail(db, user)


@router.get("/users/{user_id}", response_model=AdminUserDetailOut)
async def get_user(user_id: int, _: AdminUser, db: DbSession) -> AdminUserDetailOut:
    return await _detail(db, await _get_user(db, user_id))


@router.patch("/users/{user_id}", response_model=AdminUserDetailOut)
async def update_user(
    user_id: int, payload: AdminUserUpdate, admin: AdminUser, db: DbSession
) -> AdminUserDetailOut:
    """改资料 / 配额 / 角色 / 启用状态。只动传上来的字段。"""
    user = await _get_user(db, user_id)
    fields = payload.model_dump(exclude_unset=True)

    if "display_name" in fields:
        name = (fields["display_name"] or "").strip()
        user.display_name = name or None
    if "email" in fields:
        email = (fields["email"] or "").strip() or None
        if email and email != user.email:
            dup = (
                await db.execute(
                    select(User.id).where(User.email == email, User.id != user.id)
                )
            ).scalar_one_or_none()
            if dup is not None:
                raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已被占用")
        user.email = email

    if fields.get("is_active") is False:
        if user.id == admin.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能停用自己的账号")
        if user.is_admin and await _admin_count(db) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少要保留一个可用的管理员")
        user.is_active = False
        revoke_all = True  # 停用就顺手把设备踢下线，否则已发的 access token 还能用一会儿
    elif fields.get("is_active") is True:
        user.is_active = True
        revoke_all = False
    else:
        revoke_all = False

    if fields.get("is_admin") is not None:
        if user.id == admin.id and fields["is_admin"] is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能取消自己的管理员权限")
        if user.is_admin and fields["is_admin"] is False and await _admin_count(db) <= 1:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少要保留一个管理员")
        user.is_admin = bool(fields["is_admin"])

    for key in ("garment_quota", "outfit_quota", "tag_quota"):
        clear_flag = fields.get(f"clear_{key}", False)
        if clear_flag:
            setattr(user, key, None)
        elif key in fields and fields[key] is not None:
            setattr(user, key, int(fields[key]))

    if revoke_all:
        await db.execute(
            update(UserSession)
            .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
            .values(revoked_at=now_utc())
        )

    await db.commit()
    await db.refresh(user)
    return await _detail(db, user)


@router.post("/users/{user_id}/password")
async def reset_password(
    user_id: int, payload: AdminPasswordReset, _: AdminUser, db: DbSession
) -> dict[str, int]:
    """重置密码，无需知道原密码。**顺手吊销该用户全部会话** —— 管理员重置密码的
    典型场景就是「账号疑似被盗」，不踢下线等于没重置。
    """
    user = await _get_user(db, user_id)
    user.password_hash = hash_password(payload.new_password)
    res = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()
    return {"revoked": res.rowcount or 0}


@router.post("/users/{user_id}/logout")
async def force_logout(user_id: int, _: AdminUser, db: DbSession) -> dict[str, int]:
    """把那台设备全踢下线（不改密码）。"""
    await _get_user(db, user_id)
    res = await db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now_utc())
    )
    await db.commit()
    return {"revoked": res.rowcount or 0}


@router.put("/users/{user_id}/llm", response_model=AdminUserDetailOut)
async def set_user_llm(
    user_id: int, payload: AdminLlmUpdate, _: AdminUser, db: DbSession
) -> AdminUserDetailOut:
    """管理员代设某个用户的大模型。用户自己没填时靠它把 AI 打开。"""
    user = await _get_user(db, user_id)
    apply_llm_settings(
        user,
        base_url=payload.base_url,
        model=payload.model,
        api_key=payload.api_key,
        from_admin=True,
    )
    await db.commit()
    await db.refresh(user)
    return await _detail(db, user)


@router.delete("/users/{user_id}/llm", response_model=AdminUserDetailOut)
async def clear_user_llm(user_id: int, _: AdminUser, db: DbSession) -> AdminUserDetailOut:
    """清掉这个用户的模型配置（代设错了 / 想让他自己填）。"""
    user = await _get_user(db, user_id)
    clear_llm_settings(user)
    await db.commit()
    await db.refresh(user)
    return await _detail(db, user)


@router.delete("/users/{user_id}")
async def delete_user(user_id: int, admin: AdminUser, db: DbSession) -> dict[str, object]:
    """删除用户**及其全部数据**（衣物 / 搭配 / 标签 / 会话 / 媒体文件）。

    外键都是 ``ON DELETE CASCADE``，所以删用户行本身就会清干净业务行；
    这里额外做的是**先把媒体文件的 key 收集起来**，commit 之后再删文件 ——
    数据库先干净，磁盘后动手（顺序反了就会留下「库里没有、盘上还在」的孤儿）。
    """
    user = await _get_user(db, user_id)
    if user.id == admin.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "不能删除自己的账号")
    if user.is_admin and await _admin_count(db) <= 1:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "至少要保留一个管理员")

    keys: list[str | None] = []
    garment_images = (
        (await db.execute(select(Garment).where(Garment.owner_id == user.id).options(selectinload(Garment.images))))
        .scalars()
        .all()
    )
    for garment in garment_images:
        for img in garment.images:
            keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]

    outfits = (
        (await db.execute(select(Outfit).where(Outfit.owner_id == user.id).options(selectinload(Outfit.images))))
        .scalars()
        .all()
    )
    for outfit in outfits:
        for img in outfit.images:
            keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]

    username = user.username
    await db.delete(user)
    await db.commit()

    delete_keys(keys)
    return {"ok": True, "username": username, "deleted_files": len(keys)}


# ---------- 注册模式 ----------


async def _registration_out(db: AsyncSession) -> RegistrationSettingsOut:
    settings = await system_service.get_app_settings(db)
    usable = sum(
        1
        for code in (await db.execute(select(InviteCode))).scalars().all()
        if system_service.is_usable(code)
    )
    return RegistrationSettingsOut(mode=settings.registration_mode, invite_codes_usable=usable)


@router.get("/settings", response_model=RegistrationSettingsOut)
async def read_settings(_: AdminUser, db: DbSession) -> RegistrationSettingsOut:
    return await _registration_out(db)


@router.patch("/settings", response_model=RegistrationSettingsOut)
async def update_settings(
    payload: RegistrationSettingsIn, _: AdminUser, db: DbSession
) -> RegistrationSettingsOut:
    await system_service.set_registration_mode(db, payload.mode)
    return await _registration_out(db)


# ---------- 邀请码 ----------


def _code_out(code: InviteCode) -> InviteCodeOut:
    return InviteCodeOut(
        id=code.id,
        code=code.code,
        note=code.note,
        max_uses=code.max_uses,
        used_count=code.used_count,
        remaining=code.remaining,
        expires_at=code.expires_at,
        revoked_at=code.revoked_at,
        created_at=code.created_at,
        usable=system_service.is_usable(code),
    )


@router.get("/invite-codes", response_model=list[InviteCodeOut])
async def list_invite_codes(_: AdminUser, db: DbSession) -> list[InviteCodeOut]:
    """全部邀请码，新的在前（生成完立刻能看见）。"""
    rows = (
        (await db.execute(select(InviteCode).order_by(InviteCode.id.desc()))).scalars().all()
    )
    return [_code_out(c) for c in rows]


@router.post("/invite-codes", response_model=list[InviteCodeOut], status_code=status.HTTP_201_CREATED)
async def create_invite_codes(
    payload: InviteCodeCreateIn, admin: AdminUser, db: DbSession
) -> list[InviteCodeOut]:
    codes = await system_service.generate_unique_codes(db, payload.count)
    expires_at = (
        now_utc() + timedelta(days=payload.expires_in_days)
        if payload.expires_in_days
        else None
    )
    rows = [
        InviteCode(
            code=code,
            note=payload.note,
            created_by=admin.id,
            max_uses=payload.max_uses,
            expires_at=expires_at,
        )
        for code in codes
    ]
    db.add_all(rows)
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [_code_out(r) for r in rows]


@router.delete("/invite-codes/{code_id}", response_model=InviteCodeOut)
async def revoke_invite_code(code_id: int, _: AdminUser, db: DbSession) -> InviteCodeOut:
    """吊销（软删）。已经用掉的次数留在记录里，方便查账。"""
    code = await db.get(InviteCode, code_id)
    if code is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "邀请码不存在")
    if code.revoked_at is None:
        code.revoked_at = now_utc()
        await db.commit()
        await db.refresh(code)
    return _code_out(code)
