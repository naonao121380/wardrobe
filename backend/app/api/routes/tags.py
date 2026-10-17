"""标签体系路由。

单品（``garment``）与套装搭配（``outfit``）是两套完全独立的体系：
同一张表、同一个 ``owner_id``，靠 ``scope`` 隔开，互不可见。

层级：类别 → 取值是两层；但支持「取值挂取值」做「大类 → 小类」的级联。
``TagCategory.parent_category_id`` 声明子类别，``TagValue.parent_value_id`` 挂具体父值。
只允许两层，不做任意深度的树 —— 衣橱分类再深就是给自己找麻烦。
"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.deps import CurrentUser, DbSession
from app.core.time import now_utc
from app.models.garment import Garment, GarmentTag
from app.models.tag import TagCategory, TagValue
from app.schemas.tag import (
    ScopeLiteral,
    TagCategoryConfig,
    TagCategoryCreate,
    TagCategoryOut,
    TagCategoryUpdate,
    TagConfigImportResult,
    TagConfigIn,
    TagConfigOut,
    TagValueConfig,
    TagValueCreate,
    TagValueOut,
    TagValueUpdate,
)
from app.services import quota as quota_service

router = APIRouter(prefix="/tags", tags=["tags"])


async def _load_categories(db: AsyncSession, owner_id: int, scope: str) -> list[TagCategory]:
    return list(
        (
            await db.execute(
                select(TagCategory)
                .where(TagCategory.owner_id == owner_id, TagCategory.scope == scope)
                .options(selectinload(TagCategory.values))
                .order_by(TagCategory.sort_order, TagCategory.id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _usage_counts(db: AsyncSession, owner_id: int) -> dict[int, int]:
    """每个取值当前被多少件**自己的**衣物引用。

    只有 garment 作用域存在关联表；outfit 还没有，调用方不查即可。
    """
    rows = (
        await db.execute(
            select(GarmentTag.tag_value_id, func.count(GarmentTag.id))
            .join(Garment, Garment.id == GarmentTag.garment_id)
            .where(Garment.owner_id == owner_id)
            .group_by(GarmentTag.tag_value_id)
        )
    ).all()
    return {row[0]: row[1] for row in rows}


def _category_out(cat: TagCategory, usage: dict[int, int] | None = None) -> TagCategoryOut:
    """序列化类别并附上引用计数（ORM 上没这个字段，只能手工组装）。"""
    usage = usage or {}
    values = [
        TagValueOut(
            id=v.id,
            value=v.value,
            sort_order=v.sort_order,
            parent_value_id=v.parent_value_id,
            usage_count=usage.get(v.id, 0),
        )
        for v in cat.values
    ]
    return TagCategoryOut(
        id=cat.id,
        scope=cat.scope,
        name=cat.name,
        sort_order=cat.sort_order,
        parent_category_id=cat.parent_category_id,
        values=values,
        usage_count=sum(v.usage_count for v in values),
    )


async def _get_category(db: AsyncSession, owner_id: int, category_id: int) -> TagCategory:
    cat = (
        await db.execute(
            select(TagCategory)
            .where(TagCategory.id == category_id, TagCategory.owner_id == owner_id)
            .options(selectinload(TagCategory.values))
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if cat is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "标签类别不存在")
    return cat


async def _validate_parent_category(
    db: AsyncSession, owner_id: int, scope: str, parent_category_id: int | None
) -> None:
    """父类别必须存在、同 scope，且自身是顶层类别（只允许两层）。"""
    if parent_category_id is None:
        return
    parent = await _get_category(db, owner_id, parent_category_id)
    if parent.scope != scope:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "父类别与本类别不在同一作用域")
    if parent.parent_category_id is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只支持两层：父类别本身必须是顶层类别")


async def _validate_parent_value(
    db: AsyncSession, owner_id: int, category: TagCategory, parent_value_id: int | None
) -> None:
    """子类别的取值必须挂在**父类别**下的某个取值上；顶层类别的取值不允许挂父值。"""
    if parent_value_id is None:
        return
    if category.parent_category_id is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"「{category.name}」是顶层类别，它的取值不能挂在别的取值下"
        )
    parent_value = (
        await db.execute(
            select(TagValue).where(
                TagValue.id == parent_value_id, TagValue.owner_id == owner_id
            )
        )
    ).scalar_one_or_none()
    if parent_value is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "父取值不存在")
    if parent_value.category_id != category.parent_category_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "父取值不属于该子类别的父类别")


# ---------- 类别 ----------


@router.get("/categories", response_model=list[TagCategoryOut])
async def list_categories(
    user: CurrentUser, db: DbSession, scope: ScopeLiteral = "garment"
) -> list[TagCategoryOut]:
    cats = await _load_categories(db, user.id, scope)
    usage = await _usage_counts(db, user.id) if scope == "garment" else {}
    return [_category_out(c, usage) for c in cats]


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

    await _validate_parent_category(db, user.id, payload.scope, payload.parent_category_id)

    cat = TagCategory(
        owner_id=user.id,
        scope=payload.scope,
        name=name,
        sort_order=payload.sort_order,
        parent_category_id=payload.parent_category_id,
    )
    db.add(cat)
    await db.commit()
    # 新建的类别还没有取值，引用计数必然是 0
    return _category_out(await _get_category(db, user.id, cat.id))


@router.patch("/categories/{category_id}", response_model=TagCategoryOut)
async def update_category(
    category_id: int, payload: TagCategoryUpdate, user: CurrentUser, db: DbSession
) -> TagCategoryOut:
    cat = await _get_category(db, user.id, category_id)
    fields = payload.model_dump(exclude_unset=True)

    if "name" in fields and fields["name"] is not None:
        cat.name = fields["name"].strip()
    if "sort_order" in fields and fields["sort_order"] is not None:
        cat.sort_order = fields["sort_order"]
    if "parent_category_id" in fields:
        parent_id = fields["parent_category_id"]
        if parent_id == cat.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "类别不能把自己当父类别")
        await _validate_parent_category(db, user.id, cat.scope, parent_id)
        # 已有取值挂在本类别下时，不允许再给自己加父级（会变成三层）
        if parent_id is not None and cat.values:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "该类别下已有取值，不能再挂到别的类别下（会产生三层）",
            )
        cat.parent_category_id = parent_id

    await db.commit()
    cat = await _get_category(db, user.id, category_id)
    usage = await _usage_counts(db, user.id) if cat.scope == "garment" else {}
    return _category_out(cat, usage)


@router.delete("/categories/{category_id}")
async def delete_category(category_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    cat = await _get_category(db, user.id, category_id)
    value_ids = [v.id for v in cat.values]
    # 显式清理关联，不依赖数据库级联（SQLite 只在开了 pragma 时才强制外键）
    if value_ids:
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id.in_(value_ids)))
        # 子类别的取值挂在本类别取值下 → 降级为「未归类」，而不是连带删除
        await db.execute(
            TagValue.__table__.update()
            .where(TagValue.parent_value_id.in_(value_ids))
            .values(parent_value_id=None)
        )
    # 挂在它下面的子类别（如「小类」）降级为顶层类别，取值一个不丢
    demoted = (
        await db.execute(
            TagCategory.__table__.update()
            .where(TagCategory.parent_category_id == cat.id)
            .values(parent_category_id=None)
        )
    ).rowcount
    await db.delete(cat)
    await db.commit()
    return {"deleted_values": len(value_ids), "demoted_categories": demoted or 0}


# ---------- 取值 ----------


@router.post(
    "/categories/{category_id}/values",
    response_model=TagValueOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_value(
    category_id: int, payload: TagValueCreate, user: CurrentUser, db: DbSession
) -> TagValueOut:
    # 「可添加标签数量」按**取值**数算（类别是骨架，播种时就有一批，不占名额）
    await quota_service.ensure(db, user, "tag")
    cat = await _get_category(db, user.id, category_id)
    value = payload.value.strip()
    if any(v.value == value for v in cat.values):
        raise HTTPException(status.HTTP_409_CONFLICT, "该取值已存在")

    await _validate_parent_value(db, user.id, cat, payload.parent_value_id)

    item = TagValue(
        owner_id=user.id,
        category_id=cat.id,
        value=value,
        sort_order=payload.sort_order,
        parent_value_id=payload.parent_value_id,
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
    fields = payload.model_dump(exclude_unset=True)

    if "value" in fields and fields["value"] is not None:
        item.value = fields["value"].strip()
    if "sort_order" in fields and fields["sort_order"] is not None:
        item.sort_order = fields["sort_order"]
    if "parent_value_id" in fields:
        parent_id = fields["parent_value_id"]
        if parent_id == item.id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "取值不能把自己当父取值")
        cat = await _get_category(db, user.id, item.category_id)
        await _validate_parent_value(db, user.id, cat, parent_id)
        item.parent_value_id = parent_id

    await db.commit()
    await db.refresh(item)
    out = TagValueOut.model_validate(item)
    out.usage_count = (await _usage_counts(db, user.id)).get(item.id, 0)
    return out


@router.delete("/values/{value_id}")
async def delete_value(value_id: int, user: CurrentUser, db: DbSession) -> dict[str, int]:
    item = await _get_value(db, user.id, value_id)
    affected = (
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id == item.id))
    ).rowcount
    # 挂在它下面的子取值降级为未归类
    await db.execute(
        TagValue.__table__.update()
        .where(TagValue.parent_value_id == item.id)
        .values(parent_value_id=None)
    )
    await db.delete(item)
    await db.commit()
    return {"untagged": affected or 0}


# ---------- 配置导入 / 导出 ----------
#
# 场景：用户在 A 机器上把标签体系配好了（自建类别、补了几十个取值、还搭了级联），
# 换到 B 机器 / 换个账号不想从头再点一遍。
#
# 两条硬规则：
#   1. 文件里**只有名字、没有 id** —— id 换个库就全变了，名字才是能落地的锚点；
#   2. 导入是**只补不删**的并集 —— 不覆盖、不改名、不删除本地已有的东西。
#      配置合并这种事，宁可少做一步，也别替用户做主。

_CONFIG_KIND = "wardrobe.tag-config"


@router.get("/export", response_model=TagConfigOut)
async def export_config(
    user: CurrentUser, db: DbSession, scope: ScopeLiteral = "garment"
) -> TagConfigOut:
    """把整套标签配置导成一份可移植的 JSON。"""
    cats = await _load_categories(db, user.id, scope)

    cat_name = {c.id: c.name for c in cats}
    # 取值 id → 文本**跨类别**建表：子类别的取值挂的是父类别里的取值，
    # 只在本类别里找必然找不到。
    value_text = {v.id: v.value for c in cats for v in c.values}

    return TagConfigOut(
        scope=scope,
        exported_at=now_utc(),
        categories=[
            TagCategoryConfig(
                name=c.name,
                sort_order=c.sort_order,
                parent=cat_name.get(c.parent_category_id) if c.parent_category_id else None,
                values=[
                    TagValueConfig(
                        value=v.value,
                        sort_order=v.sort_order,
                        parent=value_text.get(v.parent_value_id) if v.parent_value_id else None,
                    )
                    for v in c.values
                ],
            )
            for c in cats
        ],
    )


@router.post("/import", response_model=TagConfigImportResult)
async def import_config(
    payload: TagConfigIn,
    user: CurrentUser,
    db: DbSession,
    scope: ScopeLiteral | None = None,
) -> TagConfigImportResult:
    """把一份配置文件**并**进当前账号的标签体系。

    走三遍，顺序不能换：
      1) 先算清楚「真正要新增多少个取值」，一次性过配额 —— 批量要么全进要么全不进；
      2) 建类别：顶层先来，子类别后挂（父级得已存在，且只允许两层）；
      3) 建取值：先不带父值，最后统一挂级联（父值必须已经落库才有 id）。
    """
    if payload.kind not in (None, _CONFIG_KIND):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "这不是衣橱的标签配置文件")
    if payload.version is not None and payload.version > 1:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"配置版本 {payload.version} 比当前程序新，先升级程序再导入",
        )

    target: str = scope or payload.scope or "garment"
    warnings: list[str] = []

    # --- 0) 文件内部去重：同名类别合并取值，而不是报错打断整次导入 ---
    merged: list[TagCategoryConfig] = []
    seen: dict[str, TagCategoryConfig] = {}
    for cfg in payload.categories:
        name = cfg.name.strip()
        if not name:
            warnings.append("跳过了一个没有名字的类别")
            continue
        if name in seen:
            seen[name].values.extend(cfg.values)
            warnings.append(f"文件里「{name}」出现了多次，取值已合并")
            continue
        cfg.name = name
        seen[name] = cfg
        merged.append(cfg)

    if not merged:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件里没有可导入的类别")

    cats = await _load_categories(db, user.id, target)
    by_name: dict[str, TagCategory] = {c.name: c for c in cats}
    id_to_name: dict[int, str] = {c.id: c.name for c in cats}
    known_values: dict[str, set[str]] = {c.name: {v.value for v in c.values} for c in cats}
    value_obj: dict[str, dict[str, TagValue]] = {
        c.name: {v.value: v for v in c.values} for c in cats
    }

    cats_created = 0
    cats_skipped = 0
    values_created = 0
    values_skipped = 0

    # --- 1) 先算量，再过配额 ---
    to_create: list[tuple[str, TagValueConfig]] = []
    for cfg in merged:
        known = known_values.setdefault(cfg.name, set())
        existing = by_name.get(cfg.name)
        if existing is not None:
            cats_skipped += 1
            if cfg.parent and existing.parent_category_id is None:
                warnings.append(f"「{cfg.name}」本地已存在且为顶层类别，父级未改动")
        for vcfg in cfg.values:
            text = vcfg.value.strip()
            if not text:
                continue
            if text in known:
                values_skipped += 1
                continue
            known.add(text)  # 同一类别里重复出现只算一次
            to_create.append((cfg.name, vcfg))

    if to_create:
        # 按整批算：宁可一条都不进，也不要「进了一半再报错」留下半成品
        await quota_service.ensure(db, user, "tag", adding=len(to_create))

    # --- 2) 建类别：顶层先来，子类别后挂 ---
    new_cfgs = [cfg for cfg in merged if cfg.name not in by_name]

    def _new_category(cfg: TagCategoryConfig, parent_id: int | None) -> TagCategory:
        obj = TagCategory(
            owner_id=user.id,
            scope=target,
            name=cfg.name,
            sort_order=cfg.sort_order,
            parent_category_id=parent_id,
        )
        db.add(obj)
        return obj

    for cfg in (c for c in new_cfgs if c.parent is None):
        by_name[cfg.name] = _new_category(cfg, None)
        cats_created += 1

    await db.flush()
    for name, obj in by_name.items():
        if obj.id is not None:
            id_to_name[obj.id] = name

    for cfg in (c for c in new_cfgs if c.parent is not None):
        parent = by_name.get(cfg.parent) if cfg.parent else None
        if parent is None:
            warnings.append(f"父类别「{cfg.parent}」不存在，「{cfg.name}」已按顶层类别导入")
            obj = _new_category(cfg, None)
        elif parent.parent_category_id is not None:
            warnings.append(
                f"只支持两层：「{cfg.parent}」本身是子类别，「{cfg.name}」已按顶层类别导入"
            )
            obj = _new_category(cfg, None)
        else:
            obj = _new_category(cfg, parent.id)
        by_name[cfg.name] = obj
        cats_created += 1

    await db.flush()
    for name, obj in by_name.items():
        if obj.id is not None:
            id_to_name[obj.id] = name

    # --- 3) 建取值，最后统一挂级联 ---
    pending: list[tuple[str, TagValue, TagValueConfig]] = []
    for cat_name, vcfg in to_create:
        cat = by_name[cat_name]
        text = vcfg.value.strip()
        item = TagValue(
            owner_id=user.id,
            category_id=cat.id,
            value=text,
            sort_order=vcfg.sort_order,
        )
        db.add(item)
        value_obj.setdefault(cat_name, {})[text] = item
        values_created += 1
        if vcfg.parent:
            pending.append((cat_name, item, vcfg))

    await db.flush()

    for cat_name, item, vcfg in pending:
        cat = by_name[cat_name]
        if cat.parent_category_id is None:
            warnings.append(
                f"「{cat_name}」是顶层类别，取值「{item.value}」不能挂在别的取值下，已按未归类导入"
            )
            continue
        parent_cat = id_to_name.get(cat.parent_category_id)
        parent_value = value_obj.get(parent_cat or "", {}).get(vcfg.parent or "")
        if parent_value is None or parent_value.id is None:
            warnings.append(
                f"「{cat_name}」下取值「{item.value}」的父值「{vcfg.parent}」找不到，已按未归类导入"
            )
            continue
        item.parent_value_id = parent_value.id

    await db.commit()

    return TagConfigImportResult(
        scope=target,  # type: ignore[arg-type]
        categories_created=cats_created,
        categories_skipped=cats_skipped,
        values_created=values_created,
        values_skipped=values_skipped,
        warnings=warnings,
    )
