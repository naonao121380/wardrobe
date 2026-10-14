"""标签体系路由。

单品（``garment``）与套装搭配（``outfit``）是两套完全独立的体系：
同一张表、同一个 ``owner_id``，靠 ``scope`` 隔开，互不可见。
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession
from app.models.garment import GarmentTag
from app.models.tag import TagCategory, TagValue
from app.schemas.tag import (
    ScopeLiteral,
    TagCategoryCreate,
    TagCategoryOut,
    TagCategoryUpdate,
    TagValueCreate,
    TagValueOut,
    TagValueUpdate,
)

router = APIRouter(prefix="/tags", tags=["tags"])


async def _load_categories(db: AsyncSession, owner_id: int, scope: str) -> list[TagCategory]:
    return list(
        (
            await db.execute(
                select(TagCategory)
                .where(TagCategory.owner_id == owner_id, TagCategory.scope == scope)
                .options(selectinload(TagCategory.values))
                .order_by(TagCategory.sort_order, TagCategory.id)
            )
        )
        .scalars()
        .all()
    )


async def _get_category(db: AsyncSession, owner_id: int, category_id: int) -> TagCategory:
    cat = (
        await db.execute(
            select(TagCategory)
            .where(TagCategory.id == category_id, TagCategory.owner_id == owner_id)
            .options(selectinload(TagCategory.values))
        )
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "标签类别不存在")
    return cat


@router.get("/categories", response_model=list[TagCategoryOut])
async def list_categories(
    user: CurrentUser, db: DbSession, scope: ScopeLiteral = "garment"
) -> list[TagCategoryOut]:
    cats = await _load_categories(db, user.id, scope)
    return [TagCategoryOut.model_validate(c) for c in cats]


@router.post("/categories", response_model=TagCategoryOut, status_code=status.HTTP_201_CREATED)
async def create_category(
    payload: TagCategoryCreate, user: CurrentUser, db: DbSession
) -> TagCategoryOut:
    name = payload.name.strip()
    dup = (
        await db.execute(
            select(TagCategory).where(
                TagCategory.owner_id == user.id,
                TagCategory.scope == payload.scope,
                TagCategory.name == name,
            )
        )
    ).scalar_one_or_none()
    if dup is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "同名类别已存在")

    cat = TagCategory(
        owner_id=user.id, scope=payload.scope, name=name, sort_order=payload.sort_order
    )
    db.add(cat)
    await db.commit()
    return TagCategoryOut.model_validate(await _get_category(db, user.id, cat.id))


@router.patch("/categories/{category_id}", response_model=TagCategoryOut)
async def update_category(
    category_id: int, payload: TagCategoryUpdate, user: CurrentUser, db: DbSession
) -> TagCategoryOut:
    cat = await _get_category(db, user.id, category_id)
    if payload.name is not None:
        cat.name = payload.name.strip()
    if payload.sort_order is not None:
        cat.sort_order = payload.sort_order
    await db.commit()
    return TagCategoryOut.model_validate(await _get_category(db, user.id, category_id))


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    cat = await _get_category(db, user.id, category_id)
    value_ids = [v.id for v in cat.values]
    # 显式清理关联，不依赖数据库级联（SQLite 默认不强制外键）
    if value_ids:
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id.in_(value_ids)))
    await db.delete(cat)
    await db.commit()
    return {"deleted_values": len(value_ids)}


@router.post(
    "/categories/{category_id}/values",
    response_model=TagValueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_value(
    category_id: int, payload: TagValueCreate, user: CurrentUser, db: DbSession
) -> TagValueOut:
    cat = await _get_category(db, user.id, category_id)
    value = payload.value.strip()
    if any(v.value == value for v in cat.values):
        raise HTTPException(status.HTTP_409_CONFLICT, "该取值已存在")

    item = TagValue(
        owner_id=user.id, category_id=cat.id, value=value, sort_order=payload.sort_order
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return TagValueOut.model_validate(item)


async def _get_value(db: AsyncSession, owner_id: int, value_id: int) -> TagValue:
    item = (
        await db.execute(
            select(TagValue).where(TagValue.id == value_id, TagValue.owner_id == owner_id)
        )
    ).scalar_one_or_none()
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "标签取值不存在")
    return item


@router.patch("/values/{value_id}", response_model=TagValueOut)
async def update_value(
    value_id: int, payload: TagValueUpdate, user: CurrentUser, db: DbSession
) -> TagValueOut:
    item = await _get_value(db, user.id, value_id)
    if payload.value is not None:
        item.value = payload.value.strip()
    if payload.sort_order is not None:
        item.sort_order = payload.sort_order
    await db.commit()
    await db.refresh(item)
    return TagValueOut.model_validate(item)


@router.delete("/values/{value_id}")
async def delete_value(value_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    item = await _get_value(db, user.id, value_id)
    affected = (
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id == item.id))
    ).rowcount
    await db.delete(item)
    await db.commit()
    return {"untagged": affected or 0}
