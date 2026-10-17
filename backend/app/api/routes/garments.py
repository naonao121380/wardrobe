"""衣物单品路由：CRUD、标签绑定、图片上传。

列表接口返回的是**带签名的缩略图 URL**，前端可直接塞进 ``<img src>``。
"""

import io
import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from PIL import Image
from sqlalchemy import delete, distinct, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.media import MediaError, delete_keys, resolve_key, sign_url, store_image
from app.core.time import now_naive_utc, now_utc
from app.models.garment import Garment, GarmentImage, GarmentStatus, GarmentTag
from app.models.outfit import Outfit, OutfitItem
from app.models.tag import TagCategory, TagValue
from app.schemas.garment import (
    AffectedOutfitOut,
    AiSuggestionOut,
    AiSuggestionTagOut,
    BatchFileSkipOut,
    BatchSkipOut,
    GarmentBatchDeleteOut,
    GarmentBatchIdsIn,
    GarmentBatchProcessIn,
    GarmentBatchProcessOut,
    GarmentBatchTagsIn,
    GarmentBatchTagsOut,
    GarmentBatchUploadOut,
    GarmentCreate,
    GarmentDeleteOut,
    GarmentImageOut,
    GarmentListOut,
    GarmentOut,
    GarmentReview,
    GarmentTagOut,
    GarmentUpdate,
    ImageOrderUpdate,
    MatteBoxIn,
    QueueStatusOut,
)
from app.services import quota as quota_service
from app.services.refine import remat_box

router = APIRouter(prefix="/garments", tags=["garments"])

log = logging.getLogger(__name__)

SCOPE = "garment"

#: 分面筛选的上限。前端会把「各维度的或条件」做笛卡尔积摊平成 tag_groups，
#: 理论上会膨胀，这里卡一道防止恶意/误用把 SQL 撑爆。
MAX_TAG_GROUPS = 128
MAX_TAG_GROUP_VALUES = 16


def _parse_tag_groups(raw: list[str]) -> list[list[int]]:
    """把 ``?tag_groups=1,2&tag_groups=3`` 解析成 ``[[1, 2], [3]]``。

    **组内 AND、组间 OR** —— 这是前端分面筛选需要的语义：

    - 「上装的开衫 **或** 下装的牛仔裤」→ ``[上装, 开衫]`` + ``[下装, 牛仔裤]``
    - 「（上装或下装）**且** 黑色」→ 前端把两个维度做笛卡尔积：
      ``[上装, 黑]`` + ``[下装, 黑]``

    旧的 ``tag_value_ids`` 是全局 AND，保留不动（e2e 与老客户端仍在用）。
    """
    groups: list[list[int]] = []
    for item in raw:
        pieces = [p.strip() for p in item.split(",") if p.strip()]
        try:
            ids = [int(p) for p in pieces]
        except ValueError as exc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"tag_groups 格式非法：{item!r}（应为逗号分隔的数字）"
            ) from exc
        if ids:
            groups.append(list(dict.fromkeys(ids)))

    if len(groups) > MAX_TAG_GROUPS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"tag_groups 最多 {MAX_TAG_GROUPS} 组，收到 {len(groups)} 组"
        )
    for group in groups:
        if len(group) > MAX_TAG_GROUP_VALUES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"单个 tag_groups 最多 {MAX_TAG_GROUP_VALUES} 个标签，收到 {len(group)} 个",
            )
    return groups


# ---------- 内部工具 ----------


async def _load_garment(db: AsyncSession, owner_id: int, garment_id: int) -> Garment:
    """按 id 取衣物，**强制刷新**已加载的关系集合。

    ``SessionLocal`` 是 ``expire_on_commit=False``，所以 commit 之后对象仍带着
    旧的 ``images`` / ``tag_links``；此时 ``selectinload`` 默认**不会覆盖**已加载
    的集合，会读到脏数据（改过标签、调过封面顺序都看不出来）。这里用
    ``populate_existing`` 让关系以数据库为准重新填充。
    """
    garment = (
        await db.execute(
            select(Garment)
            .where(Garment.id == garment_id, Garment.owner_id == owner_id)
            .options(selectinload(Garment.images), selectinload(Garment.tag_links))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if garment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "衣物不存在")
    return garment


async def _tag_lookup(db: AsyncSession, owner_id: int) -> dict[int, tuple[int, str, str]]:
    """一次性取出该用户全部标签取值 → (类别id, 类别名, 取值)。"""
    rows = (
        await db.execute(
            select(TagValue.id, TagValue.value, TagCategory.id, TagCategory.name)
            .join(TagCategory, TagCategory.id == TagValue.category_id)
            .where(TagValue.owner_id == owner_id)
        )
    ).all()
    return {row[0]: (row[2], row[3], row[1]) for row in rows}


def _ai_suggestion(
    garment: Garment, lookup: dict[int, tuple[int, str, str]]
) -> AiSuggestionOut | None:
    """把库里的 JSON 建议解析成结构化输出；脏数据只降级为 None，不让列表接口 500。"""
    raw = garment.ai_suggestion
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None

    value_ids = [int(v) for v in data.get("tag_value_ids") or [] if isinstance(v, int)]
    tags = []
    for value_id in value_ids:
        info = lookup.get(value_id)
        if info is None:
            continue  # 建议里的标签可能已被删除，直接跳过
        category_id, category_name, value = info
        tags.append(
            AiSuggestionTagOut(
                value_id=value_id,
                value=value,
                category_id=category_id,
                category_name=category_name,
            )
        )

    return AiSuggestionOut(
        name=data.get("name"),
        tag_value_ids=value_ids,
        tags=tags,
        provider=str(data.get("provider") or "none"),
        confidence=data.get("confidence"),
        error=data.get("error"),
    )


def _serialize(garment: Garment, lookup: dict[int, tuple[int, str, str]]) -> GarmentOut:
    # 关系上的 order_by 只在「首次加载」时生效；配合 expire_on_commit=False，
    # 内存里改过 sort_order 后不能指望集合自动重排，这里显式排一次。
    images = [
        GarmentImageOut(
            id=img.id,
            kind=img.kind,
            width=img.width,
            height=img.height,
            sort_order=img.sort_order,
            thumb_small_url=sign_url(img.thumb_small_key),
            thumb_medium_url=sign_url(img.thumb_medium_key),
            original_url=sign_url(img.storage_key),
        )
        for img in sorted(garment.images, key=lambda i: (i.sort_order, i.id))
    ]

    value_ids = [link.tag_value_id for link in garment.tag_links]
    tags: list[GarmentTagOut] = []
    for value_id in value_ids:
        info = lookup.get(value_id)
        if info is None:
            continue
        category_id, category_name, value = info
        tags.append(
            GarmentTagOut(
                value_id=value_id, value=value, category_id=category_id, category_name=category_name
            )
        )
    tags.sort(key=lambda t: (t.category_name, t.value))

    return GarmentOut(
        id=garment.id,
        name=garment.name,
        notes=garment.notes,
        created_at=garment.created_at,
        updated_at=garment.updated_at,
        cover_url=images[0].thumb_medium_url if images else None,
        images=images,
        tag_value_ids=value_ids,
        tags=tags,
        status=garment.status,
        ai_suggestion=_ai_suggestion(garment, lookup),
    )


async def _valid_tag_ids(
    db: AsyncSession, owner_id: int, tag_value_ids: list[int]
) -> list[int]:
    """过掉已删除 / 不属于本作用域的取值 id，保留原顺序。"""
    if not tag_value_ids:
        return []
    unique = list(dict.fromkeys(tag_value_ids))
    valid = set(
        (
            await db.execute(
                select(TagValue.id)
                .join(TagCategory, TagCategory.id == TagValue.category_id)
                .where(
                    TagValue.id.in_(unique),
                    TagValue.owner_id == owner_id,
                    TagCategory.scope == SCOPE,
                )
            )
        )
        .scalars()
        .all()
    )
    return [v for v in unique if v in valid]


async def _replace_tags(
    db: AsyncSession, garment: Garment, tag_value_ids: list[int], owner_id: int
) -> None:
    unique_ids = list(dict.fromkeys(tag_value_ids))
    if unique_ids:
        valid = set(
            (
                await db.execute(
                    select(TagValue.id)
                    .join(TagCategory, TagCategory.id == TagValue.category_id)
                    .where(
                        TagValue.id.in_(unique_ids),
                        TagValue.owner_id == owner_id,
                        TagCategory.scope == SCOPE,
                    )
                )
            )
            .scalars()
            .all()
        )
        invalid = [v for v in unique_ids if v not in valid]
        if invalid:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无效的标签取值：{invalid}")

    await db.execute(delete(GarmentTag).where(GarmentTag.garment_id == garment.id))
    await db.flush()
    for value_id in unique_ids:
        db.add(GarmentTag(garment_id=garment.id, tag_value_id=value_id))


# ---------- 列表 / 详情 ----------


@router.get("", response_model=GarmentListOut)
async def list_garments(
    user: CurrentUser,
    db: DbSession,
    q: str | None = Query(default=None, max_length=128, description="按名称模糊搜索"),
    tag_value_ids: Annotated[list[int] | None, Query(description="多选标签，取交集（旧语义）")] = None,
    tag_groups: Annotated[
        list[str] | None, Query(description="标签分组，组内取交集、组间取并集，如 '1,2'")
    ] = None,
    status_filter: Annotated[
        list[str] | None,
        Query(
            description="按流水线状态过滤，可多选：pending/processing/needs_review/ready/failed",
            alias="status",
        ),
    ] = None,
    unprocessed: bool = Query(
        default=False,
        description="只返回**还没抠过图**的衣物（没有任何 kind=cutout 的图片）",
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=48, ge=1, le=200),
) -> GarmentListOut:
    stmt = select(Garment).where(Garment.owner_id == user.id)

    if q and q.strip():
        stmt = stmt.where(Garment.name.ilike(f"%{q.strip()}%"))

    if unprocessed:
        # 「有没有抠图结果」只看图片表里有没有 cutout，不看 status ——
        # 老数据是后来迁移补的 ready，status 全绿但没有抠图。
        has_cutout = (
            select(GarmentImage.id)
            .where(GarmentImage.garment_id == Garment.id, GarmentImage.kind == "cutout")
            .exists()
        )
        stmt = stmt.where(~has_cutout)

    if status_filter:
        wanted = [s for s in dict.fromkeys(status_filter) if s]
        bad = [s for s in wanted if not GarmentStatus.is_valid(s)]
        if bad:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无效的状态：{bad}")
        stmt = stmt.where(Garment.status.in_(wanted))

    if tag_groups:
        # 组内 AND、组间 OR
        clauses = []
        for wanted in _parse_tag_groups(tag_groups):
            matched = (
                select(GarmentTag.garment_id)
                .where(GarmentTag.tag_value_id.in_(wanted))
                .group_by(GarmentTag.garment_id)
                .having(func.count(distinct(GarmentTag.tag_value_id)) == len(wanted))
            )
            clauses.append(Garment.id.in_(matched))
        if clauses:
            stmt = stmt.where(or_(*clauses))
    elif tag_value_ids:
        wanted = list(dict.fromkeys(tag_value_ids))
        # 取交集：衣物必须同时拥有全部被选中的标签
        matched = (
            select(GarmentTag.garment_id)
            .where(GarmentTag.tag_value_id.in_(wanted))
            .group_by(GarmentTag.garment_id)
            .having(func.count(distinct(GarmentTag.tag_value_id)) == len(wanted))
        )
        stmt = stmt.where(Garment.id.in_(matched))

    total = (
        await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))
    ).scalar_one()

    rows = (
        (
            await db.execute(
                stmt.options(selectinload(Garment.images), selectinload(Garment.tag_links))
                .order_by(Garment.created_at.desc(), Garment.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    lookup = await _tag_lookup(db, user.id)
    return GarmentListOut(
        items=[_serialize(g, lookup) for g in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


# ---------- 批量入队 / 队列状态 ----------
#
# ⚠️ 这两条必须注册在下面 ``/{garment_id}...`` 之前。FastAPI 按注册顺序匹配，
# ``/garments/queue-status`` 会被 ``/{garment_id}`` 吃掉（garment_id="queue-status"），
# ``/garments/batch/process`` 会被 ``/{garment_id}/process`` 吃掉 —— 晚注册就永远轮不到。


@router.get("/queue-status", response_model=QueueStatusOut)
async def queue_status(user: CurrentUser, db: DbSession) -> QueueStatusOut:
    """各状态的衣物数量，供前端判断「这批活儿跑完没」。

    Worker 是独立进程，跑完不会通知 API；但反过来讲，让前端盲刷整个列表也太吵 ——
    这里只回几个计数，轮询起来几乎不要钱。
    """
    rows = (
        await db.execute(
            select(Garment.status, func.count())
            .where(Garment.owner_id == user.id)
            .group_by(Garment.status)
        )
    ).all()
    counts = {status: n for status, n in rows}
    return QueueStatusOut(
        pending=counts.get(GarmentStatus.PENDING, 0),
        processing=counts.get(GarmentStatus.PROCESSING, 0),
        needs_review=counts.get(GarmentStatus.NEEDS_REVIEW, 0),
        ready=counts.get(GarmentStatus.READY, 0),
        failed=counts.get(GarmentStatus.FAILED, 0),
    )


@router.post("/batch/process", response_model=GarmentBatchProcessOut)
async def batch_enqueue(
    payload: GarmentBatchProcessIn, user: CurrentUser, db: DbSession
) -> GarmentBatchProcessOut:
    """批量入队：把选中的衣物一次性交给 Worker。

    - 不存在的 id / 不是自己的 → 跳过并在 ``skipped`` 里说明，不整批失败
    - ``processing`` 的跳过（Worker 正在跑，插一脚只会算两遍）
    - 已经是 ``pending`` 的照样算入队成功（幂等）
    """
    ids = list(dict.fromkeys(payload.garment_ids))
    if not ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有选择任何衣物")

    rows = (
        await db.execute(
            select(Garment).where(Garment.id.in_(ids), Garment.owner_id == user.id)
        )
    ).scalars().all()
    found = {g.id: g for g in rows}

    enqueued: list[int] = []
    skipped: list[BatchSkipOut] = []
    for garment_id in ids:
        garment = found.get(garment_id)
        if garment is None:
            skipped.append(BatchSkipOut(id=garment_id, reason="衣物不存在"))
            continue
        if garment.status == GarmentStatus.PROCESSING:
            skipped.append(BatchSkipOut(id=garment_id, reason="正在处理中"))
            continue
        enqueued.append(garment_id)

    if enqueued:
        stamped = now_utc()
        # 显式 Core UPDATE：见 #15，单例 session 的 identity map 会吞掉「值没变」的
        # 属性赋值，导致状态写不回库。这里走语句级更新，不碰 ORM 属性。
        await db.execute(
            update(Garment)
            .where(Garment.id.in_(enqueued), Garment.owner_id == user.id)
            .values(status=GarmentStatus.PENDING, ai_suggestion=None, updated_at=stamped)
        )
        await db.commit()

    return GarmentBatchProcessOut(enqueued=enqueued, skipped=skipped)


async def _batch_garments(
    db: AsyncSession, owner_id: int, ids: list[int]
) -> tuple[dict[int, Garment], list[BatchSkipOut]]:
    """按 id 批量取衣物（带图片 + 标签），不存在的记进 skipped。"""
    unique = list(dict.fromkeys(ids))
    rows = (
        await db.execute(
            select(Garment)
            .where(Garment.id.in_(unique), Garment.owner_id == owner_id)
            .options(selectinload(Garment.images), selectinload(Garment.tag_links))
            .execution_options(populate_existing=True)
        )
    ).scalars().all()
    found = {g.id: g for g in rows}
    skipped = [BatchSkipOut(id=i, reason="衣物不存在") for i in unique if i not in found]
    return found, skipped


@router.post("/batch/tags", response_model=GarmentBatchTagsOut)
async def batch_update_tags(
    payload: GarmentBatchTagsIn, user: CurrentUser, db: DbSession
) -> GarmentBatchTagsOut:
    """批量改标签：给选中的衣物**追加**（或移除 / 覆盖）同一组标签。

    一次一张图打标太慢 —— 拍回来一整批同类型的衣服，勾上、加一次标签就完事。
    标签 id 无效（被删了 / 不是自己的）静默过滤，不整批失败。
    """
    if not payload.garment_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有选择任何衣物")
    if not payload.tag_value_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有选择任何标签")
    if payload.mode not in ("add", "remove", "replace"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"未知的 mode：{payload.mode}")

    found, skipped = await _batch_garments(db, user.id, payload.garment_ids)
    valid = await _valid_tag_ids(db, user.id, payload.tag_value_ids)
    if not valid:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "标签无效或已被删除")

    updated: list[int] = []
    for garment_id, garment in found.items():
        current = [link.tag_value_id for link in garment.tag_links]
        if payload.mode == "add":
            target = list(dict.fromkeys(current + valid))
        elif payload.mode == "remove":
            target = [v for v in current if v not in set(valid)]
        else:
            target = valid
        if set(target) == set(current):
            updated.append(garment_id)  # 没变化也算处理过，别让用户以为漏了
            continue
        await _replace_tags(db, garment, target, user.id)
        updated.append(garment_id)

    await db.commit()
    return GarmentBatchTagsOut(updated=updated, skipped=skipped)


async def _affected_outfits(
    db: AsyncSession, owner_id: int, garment_ids: list[int]
) -> list[AffectedOutfitOut]:
    """哪些搭配用到了这些衣物（含各用了几层）。

    ⚠️ 必须在**删衣物之前**调用：session 是 autobegin 的，删完再查会先 flush
    掉 DELETE 语句，届时 ``outfit_items.garment_id`` 已被外键置 NULL，查出来是空的。
    """
    if not garment_ids:
        return []
    rows = (
        await db.execute(
            select(Outfit.id, Outfit.name, Outfit.created_at, func.count(OutfitItem.id))
            .join(OutfitItem, OutfitItem.outfit_id == Outfit.id)
            .where(OutfitItem.garment_id.in_(garment_ids), Outfit.owner_id == owner_id)
            .group_by(Outfit.id, Outfit.name, Outfit.created_at)
            .order_by(Outfit.created_at.desc())
        )
    ).all()
    return [AffectedOutfitOut(id=oid, name=name, layers=count) for oid, name, _ts, count in rows]


async def _delete_outfits_cascade(
    db: AsyncSession, owner_id: int, outfit_ids: list[int]
) -> tuple[int, list[str | None]]:
    """删掉这些搭配，连带它们的图与文件。返回 (删掉的套数, 待删文件 key)。

    只删 ``cascade_outfits=true`` 时用户明确点名的那几套 —— 保留搭配是更保守的默认。
    """
    if not outfit_ids:
        return 0, []
    outfits = (
        (
            await db.execute(
                select(Outfit)
                .where(Outfit.id.in_(outfit_ids), Outfit.owner_id == owner_id)
                .options(selectinload(Outfit.images))
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    keys: list[str | None] = []
    for outfit in outfits:
        for img in outfit.images:
            keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]
        await db.delete(outfit)
    return len(outfits), keys


@router.post("/batch/delete", response_model=GarmentBatchDeleteOut)
async def batch_delete(
    payload: GarmentBatchIdsIn, user: CurrentUser, db: DbSession
) -> GarmentBatchDeleteOut:
    """批量删除：一次删掉选中的衣物和它们的图片文件。

    不存在的 id 跳过并说明，不整批失败。删文件放在 commit 之后 —— 数据库删干净
    了再动磁盘，避免留下「库里没有、盘上还在」的孤儿。

    引用了这些衣物的搭配**默认保留**（那几层退化成「素材已删除」）；要让它们一起走，
    请求体里带 ``cascade_outfits=true``。
    """
    if not payload.garment_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "没有选择任何衣物")

    found, skipped = await _batch_garments(db, user.id, payload.garment_ids)

    # 顺序要紧：先查引用，再删（见 _affected_outfits 的坑）
    affected = await _affected_outfits(db, user.id, list(found.keys()))
    orphaned = sum(a.layers for a in affected)

    keys: list[str | None] = []
    deleted: list[int] = []
    deleted_images = 0
    for garment_id, garment in found.items():
        for img in garment.images:
            keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]
        deleted_images += len(garment.images)
        deleted.append(garment_id)
        await db.delete(garment)

    deleted_outfits = 0
    if payload.cascade_outfits and affected:
        deleted_outfits, outfit_keys = await _delete_outfits_cascade(
            db, user.id, [a.id for a in affected]
        )
        keys += outfit_keys

    await db.commit()
    delete_keys(keys)
    return GarmentBatchDeleteOut(
        deleted=deleted,
        deleted_images=deleted_images,
        skipped=skipped,
        affected_outfits=affected,
        orphaned_layers=0 if payload.cascade_outfits else orphaned,
        deleted_outfits=deleted_outfits,
    )


@router.post("/batch/upload", response_model=GarmentBatchUploadOut, status_code=status.HTTP_201_CREATED)
async def batch_upload(
    user: CurrentUser,
    db: DbSession,
    files: Annotated[list[UploadFile], File(description="JPEG / PNG / WebP，一张一件衣物")],
    name: Annotated[str | None, Form(description="默认名称；不传就用当前时间")] = None,
    enqueue: Annotated[bool, Form(description="建完直接丢进 AI 流水线")] = False,
    start_index: Annotated[
        int, Form(ge=0, description="起始序号；前端分批上传时传已建好的件数，保证命名连续")
    ] = 0,
) -> GarmentBatchUploadOut:
    """批量上传：**一张图一件衣物**，省掉「建衣物 → 传图 → 建下一件」的循环。

    - 名称默认取当前时间（``name`` 由前端传本地时间，避免服务器时区对不上）；
      一批多张时自动加 ``(2)`` ``(3)`` 后缀，免得一整墙同名。
    - ``start_index``：前端按请求体大小把一大堆图**分批**发（网关有单请求上限，
      一次塞 30 张会 413）。每批都要从上一批的件数接着编号，否则第二批又从
      ``(2)`` 开始，出现重名。传 0 就是「从头开始」，等价于老行为。
    - 单个文件超限 / 格式不对只是**跳过这一个**，其余照常建 —— 拍了 30 张不会
      因为一张糊了全军覆没。
    """
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未选择文件")

    # 配额按**整批**先算：宁可整批拒绝，也不要「存进去 5 件再报错」——
    # 那种半成功状态既难解释（哪些进了？）又会留下已经落盘的文件。
    # 用 files 的个数当上界（后面可能有坏图被跳过），说清楚「这次要新增 N 个」。
    await quota_service.ensure(db, user, "garment", adding=len(files))

    base = (name or "").strip() or datetime.now().strftime("%Y-%m-%d %H:%M")
    limit = get_settings().max_upload_bytes
    created: list[Garment] = []
    skipped: list[BatchFileSkipOut] = []

    for upload in files:
        label = upload.filename or "未命名文件"
        data = await upload.read()
        if not data:
            skipped.append(BatchFileSkipOut(file=label, reason="空文件"))
            continue
        if len(data) > limit:
            skipped.append(
                BatchFileSkipOut(file=label, reason=f"超过 {limit // (1024 * 1024)} MB 上限")
            )
            continue
        try:
            meta = store_image(user.id, data, upload.content_type)
        except MediaError as exc:
            skipped.append(BatchFileSkipOut(file=label, reason=str(exc)))
            continue

        garment = Garment(
            owner_id=user.id,
            # 序号 = 本批之前已建的件数 + 本批内偏移。seq 0 → 原名，1 → (2)…
            # 分批时 start_index 接上，命名才连续（见 docstring）。
            name=base
            if start_index + len(created) == 0
            else f"{base} ({start_index + len(created) + 1})",
            status=GarmentStatus.PENDING if enqueue else GarmentStatus.READY,
        )
        db.add(garment)
        await db.flush()
        db.add(
            GarmentImage(
                owner_id=user.id,
                garment_id=garment.id,
                kind="original",
                sort_order=0,
                storage_key=str(meta["storage_key"]),
                thumb_small_key=str(meta["thumb_small_key"]),
                thumb_medium_key=str(meta["thumb_medium_key"]),
                width=int(meta["width"]),  # type: ignore[arg-type]
                height=int(meta["height"]),  # type: ignore[arg-type]
                bytes=int(meta["bytes"]),  # type: ignore[arg-type]
                content_type=str(meta["content_type"]),
            )
        )
        created.append(garment)

    await db.commit()

    lookup = await _tag_lookup(db, user.id)
    # 一次性把建好的衣物连图片一起捞回来（别一件一件查，200 张会明显卡）
    fresh = (
        await db.execute(
            select(Garment)
            .where(Garment.id.in_([g.id for g in created]))
            .options(selectinload(Garment.images), selectinload(Garment.tag_links))
            .execution_options(populate_existing=True)
        )
    ).scalars().all() if created else []
    by_id = {g.id: g for g in fresh}
    out = [_serialize(by_id[g.id], lookup) for g in created if g.id in by_id]
    return GarmentBatchUploadOut(created=out, skipped=skipped)


@router.get("/{garment_id}", response_model=GarmentOut)
async def get_garment(garment_id: int, user: CurrentUser, db: DbSession) -> GarmentOut:
    garment = await _load_garment(db, user.id, garment_id)
    return _serialize(garment, await _tag_lookup(db, user.id))


# ---------- 增删改 ----------


@router.post("", response_model=GarmentOut, status_code=status.HTTP_201_CREATED)
async def create_garment(payload: GarmentCreate, user: CurrentUser, db: DbSession) -> GarmentOut:
    await quota_service.ensure(db, user, "garment")
    garment = Garment(owner_id=user.id, name=payload.name.strip(), notes=payload.notes)
    db.add(garment)
    await db.flush()
    await _replace_tags(db, garment, payload.tag_value_ids, user.id)
    await db.commit()

    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


@router.patch("/{garment_id}", response_model=GarmentOut)
async def update_garment(
    garment_id: int, payload: GarmentUpdate, user: CurrentUser, db: DbSession
) -> GarmentOut:
    garment = await _load_garment(db, user.id, garment_id)

    if payload.name is not None:
        garment.name = payload.name.strip()
    if payload.notes is not None:
        garment.notes = payload.notes
    if payload.tag_value_ids is not None:
        await _replace_tags(db, garment, payload.tag_value_ids, user.id)

    await db.commit()
    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


# ---------- 批次 3：AI 流水线 ----------


def _suggestion_dict(garment: Garment) -> dict:
    """取衣物上的 AI 建议原始字典；没有或坏了就返回空字典。"""
    if not garment.ai_suggestion:
        return {}
    try:
        data = json.loads(garment.ai_suggestion)
    except (ValueError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


@router.post("/{garment_id}/process", response_model=GarmentOut)
async def enqueue_processing(garment_id: int, user: CurrentUser, db: DbSession) -> GarmentOut:
    """把衣物丢回流水线（Worker 会重新捞它）。

    新建衣物走完上传后由前端调用；失败 / 想重跑的衣物也用它。
    ``processing`` 状态不接受重复入队 —— Worker 正在跑，插一脚只会算两遍。
    """
    garment = await _load_garment(db, user.id, garment_id)
    if garment.status == GarmentStatus.PROCESSING:
        raise HTTPException(status.HTTP_409_CONFLICT, "该衣物正在处理中")

    garment.status = GarmentStatus.PENDING
    garment.ai_suggestion = None
    await db.commit()
    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


@router.post("/{garment_id}/review", response_model=GarmentOut)
async def review_garment(
    garment_id: int,
    payload: GarmentReview | None,
    user: CurrentUser,
    db: DbSession,
) -> GarmentOut:
    """复核确认：采纳 AI 建议，或用传进来的值覆盖它。

    - **完全不带 body** 或字段省略 → 沿用建议值；建议里也没有 → 保持原样
    - 传了值 → 以用户为准
    - 确认后清空 ``ai_suggestion``，状态落到 ``ready``
    """
    payload = payload or GarmentReview()
    garment = await _load_garment(db, user.id, garment_id)
    suggestion = _suggestion_dict(garment)

    if payload.name is not None:
        garment.name = payload.name.strip()
    elif suggestion.get("name"):
        garment.name = str(suggestion["name"]).strip()

    if payload.notes is not None:
        garment.notes = payload.notes

    if payload.tag_value_ids is not None:
        await _replace_tags(db, garment, payload.tag_value_ids, user.id)
    elif suggestion.get("tag_value_ids"):
        # 建议是异步产出的，等用户复核时里面的标签可能已经被删了 —— 静默跳过，
        # 不要让一条过期建议把整件衣物卡在 needs_review
        await _replace_tags(
            db,
            garment,
            await _valid_tag_ids(db, user.id, list(suggestion["tag_value_ids"])),
            user.id,
        )

    garment.status = GarmentStatus.READY
    garment.ai_suggestion = None
    garment.processed_at = now_naive_utc()
    await db.commit()

    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


@router.delete("/{garment_id}", response_model=GarmentDeleteOut)
async def delete_garment(
    garment_id: int,
    user: CurrentUser,
    db: DbSession,
    cascade_outfits: Annotated[
        bool, Query(description="连同引用了这件衣物的搭配一起删除")
    ] = False,
) -> GarmentDeleteOut:
    """删除衣物，同时清掉它的图片文件。

    批次 4 引入搭配后，删一件衣物会牵动用到它的搭配：``outfit_items.garment_id``
    是 **ON DELETE SET NULL**，所以搭配**不会被连带删掉**，只是那几层退化成
    「素材已删除」（``name_snapshot`` 还留着衣物名，缺了哪件看得出来）。
    要让它们一起走就显式传 ``cascade_outfits=true`` —— 默认不做，删衣橱不该顺手
    把用户摆好的搭配也带走。

    返回里带上受影响的搭配清单，前端删完能如实告知「有 N 套搭配的 M 层变成了空位」。
    """
    garment = await _load_garment(db, user.id, garment_id)
    keys: list[str | None] = []
    for img in garment.images:
        keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]

    # 顺序要紧：先查引用，再删（见 _affected_outfits 的坑）
    affected = await _affected_outfits(db, user.id, [garment_id])
    orphaned = sum(a.layers for a in affected)

    deleted_outfits = 0
    if cascade_outfits and affected:
        deleted_outfits, outfit_keys = await _delete_outfits_cascade(
            db, user.id, [a.id for a in affected]
        )
        keys += outfit_keys

    deleted_images = len(garment.images)
    await db.delete(garment)
    await db.commit()

    delete_keys(keys)
    return GarmentDeleteOut(
        deleted_images=deleted_images,
        affected_outfits=affected,
        orphaned_layers=0 if cascade_outfits else orphaned,
        deleted_outfits=deleted_outfits,
    )


# ---------- 图片 ----------


@router.post(
    "/{garment_id}/images", response_model=GarmentOut, status_code=status.HTTP_201_CREATED
)
async def upload_images(
    garment_id: int,
    user: CurrentUser,
    db: DbSession,
    files: Annotated[list[UploadFile], File(description="JPEG / PNG / WebP")],
) -> GarmentOut:
    garment = await _load_garment(db, user.id, garment_id)
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未选择文件")

    limit = get_settings().max_upload_bytes
    next_order = max((img.sort_order for img in garment.images), default=-1) + 1

    def _keys_of(metas: list[dict[str, object]]) -> list[str]:
        fields = ("storage_key", "thumb_small_key", "thumb_medium_key")
        return [str(meta[f]) for meta in metas for f in fields]

    stored: list[dict[str, object]] = []
    for upload in files:
        data = await upload.read()
        if not data:
            continue
        if len(data) > limit:
            delete_keys(_keys_of(stored))
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{upload.filename or '文件'} 超过 {limit // (1024 * 1024)} MB 上限",
            )
        try:
            stored.append(store_image(user.id, data, upload.content_type))
        except MediaError as exc:
            # 已落盘的部分一并回收，避免留下孤儿文件
            delete_keys(_keys_of(stored))
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    for meta in stored:
        garment.images.append(
            GarmentImage(
                owner_id=user.id,
                kind="original",
                sort_order=next_order,
                storage_key=str(meta["storage_key"]),
                thumb_small_key=str(meta["thumb_small_key"]),
                thumb_medium_key=str(meta["thumb_medium_key"]),
                width=int(meta["width"]),  # type: ignore[arg-type]
                height=int(meta["height"]),  # type: ignore[arg-type]
                bytes=int(meta["bytes"]),  # type: ignore[arg-type]
                content_type=str(meta["content_type"]),
            )
        )
        next_order += 1

    await db.commit()
    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


@router.post("/{garment_id}/images/{image_id}/primary", response_model=GarmentOut)
async def set_primary_image(
    garment_id: int, image_id: int, user: CurrentUser, db: DbSession
) -> GarmentOut:
    """把某张图设为封面：把它排到最前，其余保持相对顺序。"""
    garment = await _load_garment(db, user.id, garment_id)
    target = next((img for img in garment.images if img.id == image_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")

    ordered = [target, *(img for img in garment.images if img.id != image_id)]
    for index, img in enumerate(ordered):
        img.sort_order = index

    await db.commit()
    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


@router.delete("/{garment_id}/images/{image_id}", response_model=GarmentOut)
async def delete_image(
    garment_id: int, image_id: int, user: CurrentUser, db: DbSession
) -> GarmentOut:
    garment = await _load_garment(db, user.id, garment_id)
    target = next((img for img in garment.images if img.id == image_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")

    keys = [target.storage_key, target.thumb_small_key, target.thumb_medium_key]
    garment.images.remove(target)
    await db.commit()

    delete_keys(keys)
    return _serialize(await _load_garment(db, user.id, garment.id), await _tag_lookup(db, user.id))


# ---------- 抠图二次修整 ----------


def _read_image(rel_key: str) -> Image.Image | None:
    """读一张已存媒体中的图；丢了或坏了返回 None（由路由决定怎么报）。"""
    try:
        path = resolve_key(rel_key)
        if not path.is_file():
            return None
        with Image.open(path) as img:
            img.load()
            return img.copy()
    except Exception:  # noqa: BLE001 - 文件损坏也算读不了
        return None


def _pick_original_image(garment: Garment) -> GarmentImage | None:
    ordered = sorted(garment.images, key=lambda i: (i.sort_order, i.id))
    return next((img for img in ordered if img.kind == "original"), None)


def _replace_image_record(img: GarmentImage, owner_id: int, rgba: Image.Image) -> list[str]:
    """用新的 RGBA 图替换某条图片记录的内容，返回旧的三个 key 供调用方删文件。

    抠图结果一定是 PNG（RGBA 才带透明通道），所以这里固定按 png 存。
    """
    buf = io.BytesIO()
    rgba.save(buf, format="PNG", optimize=True)
    meta = store_image(owner_id, buf.getvalue(), "image/png")

    old = [img.storage_key, img.thumb_small_key, img.thumb_medium_key]
    img.storage_key = str(meta["storage_key"])
    img.thumb_small_key = str(meta["thumb_small_key"])
    img.thumb_medium_key = str(meta["thumb_medium_key"])
    img.width = int(meta["width"])  # type: ignore[arg-type]
    img.height = int(meta["height"])  # type: ignore[arg-type]
    img.bytes = int(meta["bytes"])  # type: ignore[arg-type]
    img.content_type = str(meta["content_type"])
    return old


@router.post("/{garment_id}/images/{image_id}/matte-box", response_model=GarmentOut)
async def matte_box(
    garment_id: int,
    image_id: int,
    payload: MatteBoxIn,
    user: CurrentUser,
    db: DbSession,
) -> GarmentOut:
    """框选区域重新抠一次图（二次抠图）。

    适用于模型一次没看清的地方：crop 出来再跑，等效于给模型拉近了看。
    算不动的情况（没装 rembg / Worker 没起）会返回 503 并说明原因 ——
    具体走本机还是代理给 Worker，见 :mod:`app.services.refine`。
    """
    garment = await _load_garment(db, user.id, garment_id)
    cutout = next((i for i in garment.images if i.id == image_id), None)
    if cutout is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")
    if cutout.kind != "cutout":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能对抠图结果做局部重抠")

    original = _pick_original_image(garment)
    if original is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "找不到原图，无法局部重抠")

    original_img = _read_image(original.storage_key)
    cutout_img = _read_image(cutout.storage_key)
    if original_img is None or cutout_img is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "图片文件缺失或已损坏")

    outcome = await remat_box(
        original=original_img,
        cutout=cutout_img,
        original_key=original.storage_key,
        cutout_key=cutout.storage_key,
        box=payload.model_dump(),
        mode=payload.mode,
    )
    if not outcome.ok:
        if outcome.status >= 500:
            log.warning("局部重抠不可用 garment=%s image=%s：%s", garment_id, image_id, outcome.error)
        raise HTTPException(outcome.status, outcome.error)
    if outcome.png is None:  # ok 就一定有 png，这里只是给类型检查一个交代
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "局部重抠没有返回结果")

    merged = Image.open(io.BytesIO(outcome.png)).convert("RGBA")
    old_keys = _replace_image_record(cutout, user.id, merged)
    await db.commit()
    delete_keys(old_keys)
    return _serialize(await _load_garment(db, user.id, garment_id), await _tag_lookup(db, user.id))


@router.post("/{garment_id}/images/{image_id}/refine", response_model=GarmentOut)
async def refine_cutout(
    garment_id: int,
    image_id: int,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="画好的 RGBA PNG")],
) -> GarmentOut:
    """保存前端画笔编辑后的抠图结果（整张替换）。

    画笔操作在浏览器里即时完成，这里只负责接收最终的 PNG 换掉旧文件。
    """
    garment = await _load_garment(db, user.id, garment_id)
    cutout = next((i for i in garment.images if i.id == image_id), None)
    if cutout is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")
    if cutout.kind != "cutout":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能修改抠图结果")

    data = await file.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "未上传文件")
    settings = get_settings()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"图片超过 {settings.max_upload_bytes // (1024 * 1024)} MB 上限",
        )
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            submitted = img.convert("RGBA")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "无法解析该图片文件") from exc

    current = _read_image(cutout.storage_key)
    if current is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "原抠图文件缺失，无法保存修改")
    if submitted.size != (current.width, current.height):
        # 前端画布可能被缩放过，这里拉回原尺寸再落盘
        submitted = submitted.resize((current.width, current.height), Image.Resampling.LANCZOS)

    old_keys = _replace_image_record(cutout, user.id, submitted)
    await db.commit()
    delete_keys(old_keys)
    return _serialize(await _load_garment(db, user.id, garment_id), await _tag_lookup(db, user.id))
