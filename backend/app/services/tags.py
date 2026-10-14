"""标签体系的领域逻辑。

默认类别在**用户创建时**播种一次，不做懒加载 —— 否则用户把默认类别全部删掉后，
下次访问又会被塞回来。历史用户由 alembic 0002 的数据迁移补齐。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tag import TagCategory, TagScope

#: 各 scope 的默认类别（仅首次创建用户时写入）
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    TagScope.garment.value: ["大类", "小类", "颜色", "材质"],
    TagScope.outfit.value: ["季节", "风格", "色系"],
}


async def seed_default_categories(db: AsyncSession, owner_id: int) -> int:
    """为用户补齐缺失的默认类别，返回新建条数。幂等。

    只补「该 scope 下一条类别都没有」的情况；只要用户已经建过类别（哪怕是自定义的），
    就不再干预，避免把用户删掉的默认项又加回来。
    """
    created = 0
    for scope, names in DEFAULT_CATEGORIES.items():
        existing = (
            await db.execute(
                select(TagCategory.id).where(
                    TagCategory.owner_id == owner_id, TagCategory.scope == scope
                )
            )
        ).first()
        if existing is not None:
            continue
        for index, name in enumerate(names):
            db.add(TagCategory(owner_id=owner_id, scope=scope, name=name, sort_order=index))
            created += 1
    return created
