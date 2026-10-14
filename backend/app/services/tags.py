"""标签体系的领域逻辑。

默认类别在**用户创建时**播种一次，不做懒加载 —— 否则用户把默认类别全部删掉后，
下次访问又会被塞回来。历史用户由 alembic 0002 的数据迁移补齐。

设计取向（与用户确认过的原则）
--------------------------------
**上线初始化只建「最基础的那一个分类轴」，其余留给用户自己加。**

- 衣物：只建 ``大类``（并预置服装品类取值）。
  ``小类 / 颜色 / 材质`` **一律不建** —— 用户需要时自己在「管理标签」里加。
- 搭配：只建 ``季节``（并预置四季）。``风格 / 色系`` 同样不建。

理由：分类骨架各家差异很大（有人按场合、有人按季节、有人按品牌），一次性塞四个
空类别进筛选栏只会添乱；而「大类」是任何衣橱都绕不开的第一层，且取值近乎通用，
预置下来是帮忙。

参考：国内电子衣橱类 App（小可衣橱 / 电子衣橱 / 无忧衣橱）的大类划分基本收敛到
「上装 / 下装 / 裙装 / 外套 / 鞋 / 包 / 配饰」这一套。
⚠️ 国标 GB/T 23560-2009《服装分类代码》不适用 —— 它按**生产工艺**分
（机织 / 针织 / 毛皮皮革 / 特种 / 防护装备），面向生产与统计，不是给人整理衣橱用的。

⚠️ 未决（待与用户确认）：``大类`` 与 ``小类`` 目前是**平级的两个独立标签类别**，
没有父子关系，因此选了大类不会收窄小类的可选范围。若要做级联，需要在
``tag_values`` 上加自引用外键，属于数据模型改动。
"""

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.garment import GarmentTag
from app.models.tag import TagCategory, TagScope, TagValue

#: 各 scope 的默认类别骨架（仅首次创建用户时写入）
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    TagScope.garment.value: ["大类"],
    TagScope.outfit.value: ["季节"],
}

#: 各 scope 下**预置取值**的类别。没列到的类别建出来但是空的，等用户自己填。
DEFAULT_VALUES: dict[str, dict[str, list[str]]] = {
    TagScope.garment.value: {
        "大类": ["上装", "下装", "连衣裙", "外套", "鞋履", "包袋", "配饰", "内衣家居"],
    },
    TagScope.outfit.value: {
        "季节": ["春", "夏", "秋", "冬"],
    },
}


async def seed_default_categories(db: AsyncSession, owner_id: int) -> tuple[int, int]:
    """为用户补齐缺失的默认类别与预置取值，返回 (类别数, 取值数)。幂等。

    只补「该 scope 下一条类别都没有」的情况；只要用户已经建过类别（哪怕是自定义的），
    就不再干预，避免把用户删掉的默认项又加回来。
    """
    created_cats = 0
    created_values = 0

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

        presets = DEFAULT_VALUES.get(scope, {})
        for index, name in enumerate(names):
            category = TagCategory(owner_id=owner_id, scope=scope, name=name, sort_order=index)
            db.add(category)
            await db.flush()  # 拿自增 id 给下面的取值用
            created_cats += 1

            for v_index, value in enumerate(presets.get(name, [])):
                db.add(
                    TagValue(
                        owner_id=owner_id,
                        category_id=category.id,
                        value=value,
                        sort_order=v_index,
                    )
                )
                created_values += 1

    return created_cats, created_values


async def reset_tags(db: AsyncSession, owner_id: int) -> dict[str, int]:
    """清空该用户的标签体系并按当前默认值重建。

    衣物本身不动，只解除标签关联。用在「默认分类方案改了、想把环境拉回新默认」的场景。

    返回各步骤影响的行数，便于调用方打印。
    """
    value_ids = select(TagValue.id).where(TagValue.owner_id == owner_id)
    untagged = (
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id.in_(value_ids)))
    ).rowcount
    removed_values = (
        await db.execute(delete(TagValue).where(TagValue.owner_id == owner_id))
    ).rowcount
    removed_cats = (
        await db.execute(delete(TagCategory).where(TagCategory.owner_id == owner_id))
    ).rowcount
    await db.flush()

    new_cats, new_values = await seed_default_categories(db, owner_id)
    return {
        "untagged": untagged or 0,
        "removed_values": removed_values or 0,
        "removed_categories": removed_cats or 0,
        "created_categories": new_cats,
        "created_values": new_values,
    }
