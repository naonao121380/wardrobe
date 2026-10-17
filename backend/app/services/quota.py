"""按用户的配额校验。

三档资源各有一个上限（``User.garment_quota`` / ``outfit_quota`` / ``tag_quota``），
``None`` = 不限。规则只有两条：

1. **只管新增**。上限调小不会让已经存在的衣物 / 搭配 / 标签消失 —— 那是用户的数据，
   不该因为管理员改了个数字就被删掉。超了就先拦住新的，让他自己清理。
2. **批量要按整批算**。一次上传 30 件而配额只剩 5 个名额，整批拒绝并说清楚，
   不要「先存 5 件再报错」—— 那种半成功状态最难收拾。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.garment import Garment
from app.models.outfit import Outfit
from app.models.tag import TagValue
from app.models.user import User

#: 资源名 → (用户表上的配额字段, 统计用的模型)
RESOURCES: dict[str, tuple[str, type]] = {
    "garment": ("garment_quota", Garment),
    "outfit": ("outfit_quota", Outfit),
    "tag": ("tag_quota", TagValue),
}

#: 超限时给人看的名字
LABELS = {"garment": "衣物", "outfit": "搭配", "tag": "标签取值"}


class QuotaExceeded(Exception):
    """超出配额。路由层把它翻成 403，带上这句话。"""

    def __init__(self, kind: str, used: int, limit: int, adding: int) -> None:
        label = LABELS.get(kind, kind)
        remain = max(limit - used, 0)
        detail = (
            f"{label}数量已达上限（{limit}），当前已有 {used}"
            + (f"，这次要新增 {adding} 个，只剩 {remain} 个名额。" if adding > 1 else "。")
            + "请先删除一些，或联系管理员调整配额。"
        )
        super().__init__(detail)
        self.kind = kind
        self.used = used
        self.limit = limit
        self.adding = adding


def _extra_where(kind: str):
    """各类资源的「算不算数」附加条件。

    标签特殊：**只数用户自己新增的取值**。建号时播种的 ~130 个预置取值
    （``is_seed``）不算 —— 把它们算进去，任何小于 130 的配额一上来就爆，
    配额这功能就等于没有。
    """
    if kind == "tag":
        return TagValue.is_seed.is_(False)
    return None


async def count_by_owner(db: AsyncSession, owner_ids: list[int], kind: str) -> dict[int, int]:
    """一批用户在某类资源上的用量（管理页列表用，避免按用户循环查库）。"""
    if not owner_ids:
        return {}
    _, model = RESOURCES[kind]
    stmt = (
        select(model.owner_id, func.count())
        .where(model.owner_id.in_(owner_ids))
        .group_by(model.owner_id)
    )
    extra = _extra_where(kind)
    if extra is not None:
        stmt = stmt.where(extra)
    return {owner_id: int(total) for owner_id, total in (await db.execute(stmt)).all()}


async def count_resource(db: AsyncSession, owner_id: int, kind: str) -> int:
    """某类资源当前有多少。"""
    return (await count_by_owner(db, [owner_id], kind)).get(owner_id, 0)


async def usage(db: AsyncSession, owner_id: int) -> dict[str, int]:
    """一次把三类的用量都数出来（管理页与设置页都要）。"""
    return {
        "garments": await count_resource(db, owner_id, "garment"),
        "outfits": await count_resource(db, owner_id, "outfit"),
        "tags": await count_resource(db, owner_id, "tag"),
    }


def limit_of(user: User, kind: str) -> int | None:
    field, _ = RESOURCES[kind]
    return getattr(user, field)


async def assert_can_add(
    db: AsyncSession, user: User, kind: str, adding: int = 1
) -> None:
    """还能不能再加 ``adding`` 个。不能就抛 :class:`QuotaExceeded`。

    管理员**不受配额限制** —— 否则管理员把自己锁在门外（比如把标签配额调成 0），
    谁也没法再把设置改回来。
    """
    if user.is_admin:
        return
    limit = limit_of(user, kind)
    if limit is None:
        return
    used = await count_resource(db, user.id, kind)
    if used + max(adding, 1) > limit:
        raise QuotaExceeded(kind, used, limit, max(adding, 1))


def quota_summary(user: User, used: dict[str, int]) -> dict[str, dict[str, int | None]]:
    """拼成 ``QuotaOut`` 的形状。"""
    return {
        "garments": {"used": used["garments"], "limit": user.garment_quota},
        "outfits": {"used": used["outfits"], "limit": user.outfit_quota},
        "tags": {"used": used["tags"], "limit": user.tag_quota},
    }


async def ensure(db: AsyncSession, user: User, kind: str, adding: int = 1) -> None:
    """路由层用的入口：超限直接抛 403（带一句人话）。"""
    from fastapi import HTTPException, status  # 惰性导入：服务层不依赖 web 框架

    try:
        await assert_can_add(db, user, kind, adding)
    except QuotaExceeded as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, str(exc)) from exc
