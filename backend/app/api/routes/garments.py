"""衣物单品路由：CRUD、标签绑定、图片上传。

列表接口返回的是**带签名的缩略图 URL**，前端可直接塞进 ``<img src>``。
"""

from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.core.media import MediaError, delete_keys, sign_url, store_image
from app.models.garment import Garment, GarmentImage, GarmentTag
from app.models.tag import TagCategory, TagValue
from app.schemas.garment import (
    GarmentCreate,
    GarmentImageOut,
    GarmentListOut,
    GarmentOut,
    GarmentTagOut,
    GarmentUpdate,
)

router = APIRouter(prefix="/garments", tags=["garments"])

SCOPE = "garment"


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
    )


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
    tag_value_ids: Annotated[list[int] | None, Query(description="多选标签，取交集")] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=48, ge=1, le=200),
) -> GarmentListOut:
    stmt = select(Garment).where(Garment.owner_id == user.id)

    if q and q.strip():
        stmt = stmt.where(Garment.name.ilike(f"%{q.strip()}%"))

    if tag_value_ids:
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


@router.get("/{garment_id}", response_model=GarmentOut)
async def get_garment(garment_id: int, user: CurrentUser, db: DbSession) -> GarmentOut:
    garment = await _load_garment(db, user.id, garment_id)
    return _serialize(garment, await _tag_lookup(db, user.id))


# ---------- 增删改 ----------


@router.post("", response_model=GarmentOut, status_code=status.HTTP_201_CREATED)
async def create_garment(payload: GarmentCreate, user: CurrentUser, db: DbSession) -> GarmentOut:
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


@router.delete("/{garment_id}")
async def delete_garment(garment_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    """删除衣物，同时清掉它的图片文件。

    注意：批次 4 引入搭配后，这里还要提示「有哪些搭配引用了它」——见评估报告 §8.1。
    """
    garment = await _load_garment(db, user.id, garment_id)
    keys: list[str | None] = []
    for img in garment.images:
        keys += [img.storage_key, img.thumb_small_key, img.thumb_medium_key]

    await db.delete(garment)
    await db.commit()

    delete_keys(keys)
    return {"deleted_images": len(garment.images)}


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
