"""搭配路由：CRUD、画布图层、成图上传。

图层保存用**整份替换**：前端每次保存把整个图层数组提交上来，服务端删掉旧的重建。
摆搭配是个整体动作（挪一层可能牵动好几层的层序），逐层 diff 既容易写错也省不下什么 ——
一层搭配最多几十层，一次删一次建最省心。

**数组顺序即层序**：提交数组的下标就是 ``z``（下标越大越靠上），请求体里的 ``z``
字段只用于回显，不参与写入。
"""

import asyncio
import io
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from PIL import Image
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession
from app.core.media import MediaError, delete_keys, resolve_key, sign_url, store_image
from app.models.garment import Garment, GarmentImage
from app.models.outfit import Outfit, OutfitImage, OutfitItem, OutfitTag
from app.models.tag import TagCategory, TagValue
from app.schemas.outfit import (
    OutfitAiStatusOut,
    OutfitCreate,
    OutfitImageOut,
    OutfitItemIn,
    OutfitItemOut,
    OutfitListOut,
    OutfitOut,
    OutfitRefOut,
    OutfitSuggestionOut,
    OutfitTagOut,
    OutfitUpdate,
)
from app.services import quota as quota_service
from app.services.llm import tagger_available, tagger_for_user
from app.worker.providers import (
    CandidateTag,
    call_tagger,
)

router = APIRouter(prefix="/outfits", tags=["outfits"])

log = logging.getLogger(__name__)

#: 搭配侧标签的作用域。与衣物侧**完全隔离**：把「黑」（衣物颜色）挂到搭配上会被拒。
SCOPE = "outfit"

#: 图的类别。``worn`` = 上身图（可选、可多张）；``upload`` 是早期别名，写库时归一成 ``worn``。
KIND_RESULT = "result"
KIND_WORN = "worn"
LEGACY_KINDS = {"upload": KIND_WORN}

#: 一套搭配最多几张上身图。再多卡片上也翻不动，顺便挡住无限上传。
MAX_WORN_IMAGES = 12

#: AI 推荐的硬超时。模型抽风时不能把整个 API 进程拖住。
SUGGEST_TIMEOUT = 120.0


def _is_worn(kind: str) -> bool:
    """是不是上身图（含早期 ``upload`` 别名）。"""
    return kind == KIND_WORN or kind in LEGACY_KINDS


# ---------- 内部工具 ----------


async def _load_outfit(db: AsyncSession, owner_id: int, outfit_id: int) -> Outfit:
    """按 id 取搭配，强制刷新关系集合（同 garments._load_garment 的坑）。"""
    outfit = (
        await db.execute(
            select(Outfit)
            .where(Outfit.id == outfit_id, Outfit.owner_id == owner_id)
            .options(
                selectinload(Outfit.images),
                selectinload(Outfit.items),
                selectinload(Outfit.tag_links),
            )
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if outfit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "搭配不存在")
    return outfit


async def _tag_lookup(db: AsyncSession, owner_id: int) -> dict[int, tuple[int, str, str]]:
    rows = (
        await db.execute(
            select(TagValue.id, TagValue.value, TagCategory.id, TagCategory.name)
            .join(TagCategory, TagCategory.id == TagValue.category_id)
            .where(TagValue.owner_id == owner_id, TagCategory.scope == SCOPE)
        )
    ).all()
    return {row[0]: (row[2], row[3], row[1]) for row in rows}


async def _replace_tags(
    db: AsyncSession, outfit: Outfit, tag_value_ids: list[int], owner_id: int
) -> None:
    """整份替换搭配标签；跨作用域 / 不存在的取值直接 400，别静默吞掉。"""
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
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无效的搭配标签取值：{invalid}")

    await db.execute(delete(OutfitTag).where(OutfitTag.outfit_id == outfit.id))
    await db.flush()
    for value_id in unique_ids:
        db.add(OutfitTag(outfit_id=outfit.id, tag_value_id=value_id))


async def _replace_items(
    db: AsyncSession, outfit: Outfit, owner_id: int, items_in: list[OutfitItemIn]
) -> None:
    """整份替换图层。

    顺手做的事：
    - 校验引用的衣物 / 图片确实属于本人（跨用户引用一律 400）
    - 没传 ``name_snapshot`` 时用衣物当前名字补上，将来衣物删了图层还认得出
    """
    image_ids = [i.garment_image_id for i in items_in if i.garment_image_id]
    garment_ids = [i.garment_id for i in items_in if i.garment_id]

    # 先查衣物：跨用户引用时报错信息应该先提「衣物不对」，那才是用户看得懂的层面
    names: dict[int, str] = {}
    if garment_ids:
        rows = (
            await db.execute(
                select(Garment.id, Garment.name).where(
                    Garment.id.in_(garment_ids), Garment.owner_id == owner_id
                )
            )
        ).all()
        names = dict(rows)
        missing_garments = {g for g in garment_ids if g not in names}
        if missing_garments:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无效的衣物：{sorted(missing_garments)}")

    owned_images: set[int] = set()
    if image_ids:
        owned_images = set(
            (
                await db.execute(
                    select(GarmentImage.id).where(
                        GarmentImage.id.in_(image_ids), GarmentImage.owner_id == owner_id
                    )
                )
            )
            .scalars()
            .all()
        )
        missing_images = {i for i in image_ids if i not in owned_images}
        if missing_images:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"无效的素材图片：{sorted(missing_images)}")

    await db.execute(delete(OutfitItem).where(OutfitItem.outfit_id == outfit.id))
    await db.flush()

    for z, item in enumerate(items_in):
        snapshot = item.name_snapshot
        if not snapshot and item.garment_id:
            snapshot = (names.get(item.garment_id) or "")[:128] or None
        db.add(
            OutfitItem(
                outfit_id=outfit.id,
                owner_id=owner_id,
                garment_id=item.garment_id,
                garment_image_id=item.garment_image_id,
                name_snapshot=snapshot,
                x=item.x,
                y=item.y,
                w_ratio=item.w_ratio,
                rotation=item.rotation,
                z=z,
                flip_h=item.flip_h,
                opacity=item.opacity,
            )
        )
    await db.flush()


async def _serialize(db: AsyncSession, outfit: Outfit, lookup: dict) -> OutfitOut:
    images = [
        OutfitImageOut(
            id=img.id,
            kind=img.kind,
            width=img.width,
            height=img.height,
            sort_order=img.sort_order,
            thumb_small_url=sign_url(img.thumb_small_key),
            thumb_medium_url=sign_url(img.thumb_medium_key),
            original_url=sign_url(img.storage_key),
        )
        for img in sorted(outfit.images, key=lambda i: (i.sort_order, i.id))
    ]

    # 素材图与衣物名各查一次，别在循环里查库
    image_ids = [i.garment_image_id for i in outfit.items if i.garment_image_id]
    images_by_id: dict[int, GarmentImage] = {}
    if image_ids:
        rows = (
            await db.execute(
                select(GarmentImage).where(
                    GarmentImage.id.in_(image_ids), GarmentImage.owner_id == outfit.owner_id
                )
            )
        ).scalars().all()
        images_by_id = {img.id: img for img in rows}

    garment_ids = [i.garment_id for i in outfit.items if i.garment_id]
    names: dict[int, str] = {}
    if garment_ids:
        rows = (
            await db.execute(
                select(Garment.id, Garment.name).where(Garment.id.in_(garment_ids))
            )
        ).all()
        names = dict(rows)

    items: list[OutfitItemOut] = []
    for item in sorted(outfit.items, key=lambda i: (i.z, i.id)):
        source = images_by_id.get(item.garment_image_id) if item.garment_image_id else None
        items.append(
            OutfitItemOut(
                id=item.id,
                garment_id=item.garment_id,
                garment_image_id=item.garment_image_id,
                name_snapshot=item.name_snapshot,
                x=item.x,
                y=item.y,
                w_ratio=item.w_ratio,
                rotation=item.rotation,
                z=item.z,
                flip_h=item.flip_h,
                opacity=item.opacity,
                image_url=sign_url(source.storage_key) if source else None,
                width=source.width if source else 0,
                height=source.height if source else 0,
                missing=source is None,
                garment_name=names.get(item.garment_id) if item.garment_id else None,
            )
        )

    value_ids = [link.tag_value_id for link in outfit.tag_links]
    tags: list[OutfitTagOut] = []
    for value_id in value_ids:
        info = lookup.get(value_id)
        if info is None:
            continue
        category_id, category_name, value = info
        tags.append(
            OutfitTagOut(
                value_id=value_id, value=value, category_id=category_id, category_name=category_name
            )
        )
    tags.sort(key=lambda t: (t.category_name, t.value))

    return OutfitOut(
        id=outfit.id,
        name=outfit.name,
        notes=outfit.notes,
        background=outfit.background,
        created_at=outfit.created_at,
        updated_at=outfit.updated_at,
        cover_url=images[0].thumb_medium_url if images else None,
        images=images,
        items=items,
        tag_value_ids=value_ids,
        tags=tags,
    )


# ---------- 列表 / 详情 ----------


@router.get("", response_model=OutfitListOut)
async def list_outfits(
    user: CurrentUser,
    db: DbSession,
    q: str | None = Query(default=None, max_length=128, description="按名称模糊搜索"),
    tag_value_ids: Annotated[list[int] | None, Query(description="多选搭配标签，取交集")] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=48, ge=1, le=200),
) -> OutfitListOut:
    stmt = select(Outfit).where(Outfit.owner_id == user.id)

    if q and q.strip():
        stmt = stmt.where(Outfit.name.ilike(f"%{q.strip()}%"))

    if tag_value_ids:
        wanted = list(dict.fromkeys(tag_value_ids))
        matched = (
            select(OutfitTag.outfit_id)
            .where(OutfitTag.tag_value_id.in_(wanted))
            .group_by(OutfitTag.outfit_id)
            .having(func.count(func.distinct(OutfitTag.tag_value_id)) == len(wanted))
        )
        stmt = stmt.where(Outfit.id.in_(matched))

    total = (await db.execute(select(func.count()).select_from(stmt.order_by(None).subquery()))).scalar_one()

    rows = (
        (
            await db.execute(
                stmt.options(
                    selectinload(Outfit.images),
                    selectinload(Outfit.items),
                    selectinload(Outfit.tag_links),
                )
                .order_by(Outfit.created_at.desc(), Outfit.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        )
        .scalars()
        .all()
    )

    lookup = await _tag_lookup(db, user.id)
    return OutfitListOut(
        items=[await _serialize(db, o, lookup) for o in rows],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/referencing", response_model=list[OutfitRefOut])
async def outfits_referencing(
    user: CurrentUser,
    db: DbSession,
    garment_id: int | None = Query(default=None, description="单件（旧调用方式，仍兼容）"),
    garment_ids: list[int] = Query(
        default_factory=list, description="多件一起查 —— 批量删除前一次性提示用"
    ),
) -> list[OutfitRefOut]:
    """哪些搭配用到了这些衣物（含用了几层）。

    删衣物前拿它提示用户；``garment_ids`` 可以一次问一批，省得前端为选中的
    200 件各发一次请求。返回按搭配去重，``layers`` 是**这些衣物在该搭配里**的层数之和。
    """
    ids = list(dict.fromkeys([*garment_ids, *([garment_id] if garment_id is not None else [])]))
    if not ids:
        return []

    rows = (
        await db.execute(
            select(Outfit.id, Outfit.name, func.count(OutfitItem.id))
            .join(OutfitItem, OutfitItem.outfit_id == Outfit.id)
            .where(OutfitItem.garment_id.in_(ids), Outfit.owner_id == user.id)
            .group_by(Outfit.id, Outfit.name)
            .order_by(Outfit.created_at.desc())
        )
    ).all()
    return [OutfitRefOut(id=outfit_id, name=name, layers=count) for outfit_id, name, count in rows]


# ---------- AI 推荐命名 + 标签 ----------


async def _outfit_candidate_tags(db: AsyncSession, owner_id: int) -> list[CandidateTag]:
    """搭配侧全部标签取值，作为模型的候选池（季节 / 风格 / 色系）。"""
    rows = (
        await db.execute(
            select(TagValue.id, TagValue.value, TagCategory.name)
            .join(TagCategory, TagCategory.id == TagValue.category_id)
            .where(TagValue.owner_id == owner_id, TagCategory.scope == SCOPE)
            .order_by(TagCategory.name, TagValue.id)
        )
    ).all()
    return [CandidateTag(value_id=r[0], category_name=r[2], value=r[1]) for r in rows]


async def _recent_outfit_names(
    db: AsyncSession, owner_id: int, exclude_id: int, limit: int = 40
) -> list[str]:
    rows = (
        await db.execute(
            select(Outfit.name)
            .where(Outfit.owner_id == owner_id, Outfit.id != exclude_id)
            .order_by(Outfit.id.desc())
            .limit(limit)
        )
    ).all()
    return [str(name).strip() for (name,) in rows if name and str(name).strip()]


async def _unique_outfit_name(
    db: AsyncSession, owner_id: int, outfit_id: int, name: str | None
) -> str | None:
    """搭配名撞车就加序号 —— 模型避重失败时的兜底（同单品流水线一个道理）。"""
    if not name:
        return name
    name = name.strip()
    rows = (
        await db.execute(
            select(Outfit.name).where(Outfit.owner_id == owner_id, Outfit.id != outfit_id)
        )
    ).all()
    taken = {str(n).strip() for (n,) in rows if n and str(n).strip()}
    if name not in taken:
        return name
    for i in range(2, 1000):
        candidate = f"{name} ({i})"
        if candidate not in taken:
            log.info("AI 给的搭配名撞车，改为 %s", candidate)
            return candidate
    return None


def _open_image(data: bytes) -> Image.Image | None:
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception as exc:  # noqa: BLE001 - 图坏了 / 不是图片，都只是这一次推荐不了
        log.warning("读取图片失败：%s", exc)
        return None


async def _suggestion_image(outfit: Outfit, upload: UploadFile | None) -> Image.Image | None:
    """给模型看的那张图：优先用**当前画布**（前端现场合成后传来）。

    用户在编辑器里挪了几层还没保存，库里的成图是旧的 —— 看着旧图推荐出来的名字
    会和眼前的画面对不上。没有上传就退回库里的图：先搭配图，再上身图
    （画布还是空的、只传了上衣照时，总比没图可看强）。
    """
    if upload is not None:
        data = await upload.read()
        if data:
            return _open_image(data)

    ordered = sorted(outfit.images, key=lambda i: (i.sort_order, i.id))
    source = (
        next((img for img in ordered if img.kind == KIND_RESULT), None)
        or next((img for img in ordered if _is_worn(img.kind)), None)
        or (ordered[0] if ordered else None)
    )
    if source is None:
        return None
    try:
        return _open_image(resolve_key(source.storage_key).read_bytes())
    except Exception as exc:  # noqa: BLE001 - 文件丢了就当没图
        log.warning("读取搭配成图失败 %s：%s", source.storage_key, exc)
        return None


@router.get("/ai/status", response_model=OutfitAiStatusOut)
async def outfit_ai_status(user: CurrentUser) -> OutfitAiStatusOut:
    """**当前用户**的 AI 推荐能不能用。前端据此把按钮置灰并给出原因。

    模型配置是按用户存的（地址 / Key / 模型名都由用户自己填），所以这里必须
    带用户来判断 —— 别人配了不代表我也能用。
    """
    tagger = tagger_for_user(user)
    return OutfitAiStatusOut(provider=tagger.name, available=tagger_available(tagger))


@router.post("/{outfit_id}/ai-suggest", response_model=OutfitSuggestionOut)
async def suggest_outfit(
    outfit_id: int,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[
        UploadFile | None,
        File(description="可选：前端现场合成的画布图；不传就用库里最新的成图"),
    ] = None,
) -> OutfitSuggestionOut:
    """让 AI 给这套搭配起个名、挑几个标签。**只建议，不落库** —— 用户在界面上
    看过再自己保存，和单品那条「AI 建议 → 复核 → 入库」的路子保持一致。
    """
    outfit = await _load_outfit(db, user.id, outfit_id)

    tagger = tagger_for_user(user)
    if not tagger_available(tagger):
        return OutfitSuggestionOut(error="没有配置打标模型，AI 推荐不可用")

    image = await _suggestion_image(outfit, file)
    if image is None:
        return OutfitSuggestionOut(error="还没有成图：先在画布上摆好并保存一次")

    candidates = await _outfit_candidate_tags(db, user.id)
    existing = await _recent_outfit_names(db, user.id, outfit.id)
    try:
        suggestion = await asyncio.wait_for(
            call_tagger(tagger, image, candidates, existing, kind="outfit"),
            timeout=SUGGEST_TIMEOUT,
        )
    except asyncio.TimeoutError:
        return OutfitSuggestionOut(error=f"AI 超过 {SUGGEST_TIMEOUT:.0f} 秒没回话，稍后再试")
    except Exception as exc:  # noqa: BLE001 - provider 内部已经兜过，这里防漏网的
        log.warning("搭配 AI 推荐异常（outfit=%s）：%s", outfit_id, exc)
        return OutfitSuggestionOut(error=f"AI 推荐失败：{exc}")

    if suggestion.error:
        return OutfitSuggestionOut(error=suggestion.error)

    # 模型可能返回候选池之外的 id（换了 provider / 标签刚被删），一律过滤掉
    allowed = {c.value_id for c in candidates}
    value_ids = [v for v in suggestion.tag_value_ids if v in allowed]

    lookup = await _tag_lookup(db, user.id)
    tags = []
    for value_id in value_ids:
        info = lookup.get(value_id)
        if info is None:
            continue
        category_id, category_name, value = info
        tags.append(
            OutfitTagOut(
                value_id=value_id, value=value, category_id=category_id, category_name=category_name
            )
        )

    hint = None
    if not candidates:
        hint = "搭配侧还没有标签（去标签管理给季节 / 风格 / 色系加几个取值），这次只给出了名字"

    return OutfitSuggestionOut(
        name=await _unique_outfit_name(db, user.id, outfit.id, suggestion.name),
        tag_value_ids=[t.value_id for t in tags],
        tags=tags,
        confidence=suggestion.confidence,
        hint=hint,
    )


@router.post("", response_model=OutfitOut, status_code=status.HTTP_201_CREATED)
async def create_outfit(payload: OutfitCreate, user: CurrentUser, db: DbSession) -> OutfitOut:
    await quota_service.ensure(db, user, "outfit")
    outfit = Outfit(
        owner_id=user.id,
        name=payload.name.strip(),
        notes=payload.notes,
        background=(payload.background or "#ffffff").strip(),
    )
    db.add(outfit)
    await db.flush()

    await _replace_tags(db, outfit, payload.tag_value_ids, user.id)
    await _replace_items(db, outfit, user.id, payload.items)
    await db.commit()

    outfit = await _load_outfit(db, user.id, outfit.id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))


@router.get("/{outfit_id}", response_model=OutfitOut)
async def get_outfit(outfit_id: int, user: CurrentUser, db: DbSession) -> OutfitOut:
    outfit = await _load_outfit(db, user.id, outfit_id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))


@router.patch("/{outfit_id}", response_model=OutfitOut)
async def update_outfit(
    outfit_id: int, payload: OutfitUpdate, user: CurrentUser, db: DbSession
) -> OutfitOut:
    """改名称 / 备注 / 标签 / 图层。``items`` 传了就是整份替换，不传则不动画布。"""
    outfit = await _load_outfit(db, user.id, outfit_id)

    if payload.name is not None:
        outfit.name = payload.name.strip()
    if payload.notes is not None:
        outfit.notes = payload.notes
    if payload.background is not None:
        outfit.background = payload.background.strip() or "#ffffff"
    if payload.tag_value_ids is not None:
        await _replace_tags(db, outfit, payload.tag_value_ids, user.id)
    if payload.items is not None:
        await _replace_items(db, outfit, user.id, payload.items)

    await db.commit()
    outfit = await _load_outfit(db, user.id, outfit_id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))


@router.delete("/{outfit_id}")
async def delete_outfit(outfit_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    outfit = await _load_outfit(db, user.id, outfit_id)
    keys: list[str | None] = []
    for img in outfit.images:
        keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]

    await db.delete(outfit)
    await db.commit()

    delete_keys(keys)
    return {"deleted_images": len(outfit.images)}


# ---------- 成图 ----------


@router.post("/{outfit_id}/images", response_model=OutfitOut)
async def add_outfit_image(
    outfit_id: int,
    user: CurrentUser,
    db: DbSession,
    file: Annotated[UploadFile, File(description="图片文件")],
    kind: Annotated[
        str,
        Form(description="result = 编辑器合成的搭配图（只留最新一张）；worn = 上身图（可选，可多张）"),
    ] = KIND_RESULT,
) -> OutfitOut:
    """给搭配加一张图。

    - ``kind=result``：**只留最新的一张** —— 每次保存都合成一张，不清理的话
      十次编辑就攒十张几乎一样的图。
    - ``kind=worn``：**上身图**。可多张、也可以一张都不传，删光了这套搭配照样成立。
      早期的 ``upload`` 别名一并归一到这里。
    """
    kind = LEGACY_KINDS.get(kind, kind)
    if kind not in (KIND_RESULT, KIND_WORN):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "kind 只能是 result 或 worn")

    outfit = await _load_outfit(db, user.id, outfit_id)

    if kind == KIND_WORN:
        # 先数再存文件：被拒的上传不该在磁盘上留下没人认领的图
        current = sum(1 for img in outfit.images if _is_worn(img.kind))
        if current >= MAX_WORN_IMAGES:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"一套搭配最多 {MAX_WORN_IMAGES} 张上身图，先删几张再传",
            )

    data = await file.read()
    try:
        meta = store_image(user.id, data, file.content_type, folder="outfits")
    except MediaError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    stale: list[str | None] = []
    if kind == KIND_RESULT:
        for img in [i for i in outfit.images if i.kind == KIND_RESULT]:
            stale += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]
            await db.delete(img)
        await db.flush()

    # result 固定排最前（sort_order=0）当封面；上身图按上传先后排到它后面
    next_order = max((img.sort_order for img in outfit.images), default=-1) + 1
    image = OutfitImage(
        outfit_id=outfit.id,
        owner_id=user.id,
        kind=kind,
        storage_key=str(meta["storage_key"]),
        thumb_small_key=str(meta["thumb_small_key"]),
        thumb_medium_key=str(meta["thumb_medium_key"]),
        width=int(meta["width"]),  # type: ignore[arg-type]
        height=int(meta["height"]),  # type: ignore[arg-type]
        bytes=int(meta["bytes"]),  # type: ignore[arg-type]
        content_type=str(meta["content_type"]),
        sort_order=0 if kind == KIND_RESULT else next_order,
    )
    db.add(image)
    await db.commit()
    delete_keys(stale)

    outfit = await _load_outfit(db, user.id, outfit_id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))


@router.post("/{outfit_id}/images/{image_id}/primary", response_model=OutfitOut)
async def set_primary_image(
    outfit_id: int, image_id: int, user: CurrentUser, db: DbSession
) -> OutfitOut:
    """把某张成图设为封面：把它的 sort_order 压到最小。"""
    outfit = await _load_outfit(db, user.id, outfit_id)
    target = next((i for i in outfit.images if i.id == image_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")

    smallest = min((i.sort_order for i in outfit.images), default=0)
    if target.sort_order != smallest:
        target.sort_order = smallest - 1
    await db.commit()

    outfit = await _load_outfit(db, user.id, outfit_id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))


@router.delete("/{outfit_id}/images/{image_id}", response_model=OutfitOut)
async def delete_outfit_image(
    outfit_id: int, image_id: int, user: CurrentUser, db: DbSession
) -> OutfitOut:
    outfit = await _load_outfit(db, user.id, outfit_id)
    target = next((i for i in outfit.images if i.id == image_id), None)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "图片不存在")

    keys = [target.storage_key, target.thumb_small_key, target.thumb_medium_key]
    await db.delete(target)
    await db.commit()
    delete_keys(keys)

    outfit = await _load_outfit(db, user.id, outfit_id)
    return await _serialize(db, outfit, await _tag_lookup(db, user.id))
