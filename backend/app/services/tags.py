"""标签体系的领域逻辑。

默认类别在**用户创建时**播种一次，不做懒加载 —— 否则用户把默认类别全部删掉后，
下次访问又会被塞回来。alembic 0003 只负责把既有用户的「小类」挂到「大类」下；
**新增的类别轴不会自动补**（补了就等于推翻「删了别再塞回来」），要拉回新默认得显式跑
``python -m app.cli reseed-tags --username <u>``（会清空并重建该用户的标签体系）。

设计取向
--------
**大类 + 小类整套预置，其余维度只建骨架。** 见 :data:`DEFAULT_VALUES` 与
:data:`DEFAULT_CASCADE_VALUES`：

===================  ==========================  ==========================================
scope                类别                         预置取值
===================  ==========================  ==========================================
``garment``          大类 / 小类 / 颜色 / 材质     大类 8 个、小类 81 个（挂在大类下）、颜色一套
``outfit``           季节 / 风格 / 色系            季节 4 + 风格 10 + 色系 8
===================  ==========================  ==========================================

- **大类 / 小类**：整套预置（用户指定的分类树）。小类按「上装款式 / 裤型 / 裙型 /
  鞋型 / 形态 / 背法形态 / 物件」切分，并通过 ``parent_value_id`` 挂到对应大类取值下，
  于是选了「大类=上衣」，筛选里的小类只会列出 T恤 / 衬衫 / 针织衫…这 12 个。
  各大类小类数量不等（上衣 12、连衣裙 9，其余各 10）—— 以用户给的分类树为准，
  不强凑成每类 10 个。
- **材质**：只建类别，取值留空。材质五花八门，预置只会变成噪音。
- **风格 / 色系**：与季节正交，不参与级联。这两个维度相对收敛，预置一套常用词 ——
  AI 推荐搭配标签时得有候选可挑（见 :func:`app.api.routes.outfits.suggest_outfit`）。
- **颜色**：预置一套标准词表。颜色命名混乱（「杏色 / 米色 / 裸色」到底哪个），
  给一套标准词表能省掉用户反复改名。

⚠️ 国标 GB/T 23560-2009《服装分类代码》不适用 —— 它按**生产工艺**分
（机织 / 针织 / 毛皮皮革 / 特种 / 防护装备），面向生产与统计，不是给人整理衣橱用的。

改默认方案后，用 ``python -m app.cli reseed-tags --username <u>`` 把已有用户拉回新默认
（会清空该用户的标签体系并解除衣物上的标签关联）。
"""

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.garment import GarmentTag
from app.models.outfit import OutfitTag
from app.models.tag import TagCategory, TagScope, TagValue

#: 各 scope 的默认类别骨架（仅首次创建用户时写入），列表顺序即 sort_order
DEFAULT_CATEGORIES: dict[str, list[str]] = {
    TagScope.garment.value: ["大类", "小类", "颜色", "材质"],
    TagScope.outfit.value: ["季节", "风格", "色系"],
}

#: 子类别 → 父类别。只有列在这里的类别才参与级联；其余都是彼此正交的独立维度。
DEFAULT_CASCADE: dict[str, dict[str, str]] = {
    TagScope.garment.value: {"小类": "大类"},
    TagScope.outfit.value: {},
}

#: 各 scope 下**预置取值**的类别。没列到的类别建出来但是空的，等用户自己填。
DEFAULT_VALUES: dict[str, dict[str, list[str]]] = {
    TagScope.garment.value: {
        "大类": ["上衣", "裤子", "半身裙", "连衣裙", "鞋子", "袜子", "包包", "配饰"],
        # 色名顺序：无彩色 → 大地色 → 有彩色 → 特殊。前端按名字渲染色块，见 COLOR_HEX。
        "颜色": [
            "黑",
            "白",
            "灰",
            "米白",
            "杏",
            "卡其",
            "驼",
            "棕",
            "红",
            "粉",
            "橙",
            "黄",
            "绿",
            "蓝",
            "藏青",
            "紫",
            "牛仔蓝",
            "银",
            "金",
            "多色",
        ],
    },
    TagScope.outfit.value: {
        "季节": ["春", "夏", "秋", "冬"],
        # 风格 / 色系原来只建空类别，AI 没东西可挑 —— 补一套常用词，用户可自行增删
        "风格": ["通勤", "休闲", "甜美", "复古", "运动", "学院", "极简", "街头", "度假", "约会"],
        "色系": ["黑白灰", "大地色", "蓝白", "粉紫", "红橙", "黄绿", "多彩", "金属色"],
    },
}

#: 子类别的预置取值，按「父类别的取值」分组：scope → 子类别名 → 父取值 → [子取值]。
#: 建出来的子取值会把 ``parent_value_id`` 指向那个父取值，级联筛选才生效。
#: 父取值必须已经在 :data:`DEFAULT_VALUES` 里，否则挂不上（静默降级为未归类）。
DEFAULT_CASCADE_VALUES: dict[str, dict[str, dict[str, list[str]]]] = {
    TagScope.garment.value: {
        "小类": {
            "上衣": [
                "T恤", "衬衫", "针织衫", "卫衣", "吊带", "马甲",
                "西装", "风衣", "大衣", "羽绒服", "外套", "内搭",
            ],
            "裤子": ["直筒裤", "阔腿裤", "烟管裤", "小脚裤", "牛仔裤", "西裤", "卫裤", "短裤", "工装裤", "裙裤"],
            "半身裙": ["A字裙", "百褶裙", "直筒裙", "伞裙", "包臀裙", "牛仔裙", "纱裙", "鱼尾裙", "开叉裙", "荷叶裙"],
            "连衣裙": ["衬衫裙", "针织裙", "吊带裙", "小黑裙", "娃娃裙", "收腰裙", "廓形裙", "礼服裙", "法式裙"],
            "鞋子": ["运动鞋", "小白鞋", "乐福鞋", "芭蕾鞋", "高跟鞋", "凉鞋", "穆勒鞋", "短靴", "长靴", "帆布鞋"],
            "袜子": ["船袜", "短袜", "中筒袜", "长筒袜", "连裤袜", "堆堆袜", "运动袜", "羊毛袜", "隐形袜", "五指袜"],
            "包包": ["托特包", "腋下包", "斜挎包", "单肩包", "小方包", "链条包", "双肩包", "手拿包", "电脑包", "帆布包"],
            "配饰": ["帽子", "发饰", "围巾", "丝巾", "耳环", "项链", "戒指", "手链", "腰带", "墨镜"],
        }
    },
}


def describe_defaults() -> list[tuple[str, str, int]]:
    """给 CLI 用的默认方案摘要：(scope, 类别名, 预置取值个数)。"""
    out: list[tuple[str, str, int]] = []
    for scope, names in DEFAULT_CATEGORIES.items():
        presets = DEFAULT_VALUES.get(scope, {})
        cascades = DEFAULT_CASCADE_VALUES.get(scope, {})
        for name in names:
            count = len(presets.get(name, []))
            count += sum(len(v) for v in cascades.get(name, {}).values())
            out.append((scope, name, count))
    return out


async def seed_default_categories(
    db: AsyncSession, owner_id: int, only_scope: str | None = None, *, force: bool = False
) -> tuple[int, int]:
    """为用户补齐缺失的默认类别与预置取值，返回 (类别数, 取值数)。幂等。

    只补「该 scope 下一条类别都没有」的情况；只要用户已经建过类别（哪怕是自定义的），
    就不再干预，避免把用户删掉的默认项又加回来。

    ``only_scope`` 只处理某一个 scope；``force=True`` 跳过「已经有了就不动」的判断 ——
    配合 :func:`reset_tags` 的 ``scope`` 参数用：单重建搭配侧的标签而不碰衣物侧。
    """
    created_cats = 0
    created_values = 0

    for scope, names in DEFAULT_CATEGORIES.items():
        if only_scope is not None and scope != only_scope:
            continue
        if not force:
            existing = (
                await db.execute(
                    select(TagCategory.id).where(
                        TagCategory.owner_id == owner_id, TagCategory.scope == scope
                    )
                )
            ).first()
            if existing is not None:
                continue

        cascade = DEFAULT_CASCADE.get(scope, {})
        presets = DEFAULT_VALUES.get(scope, {})
        cascaded = DEFAULT_CASCADE_VALUES.get(scope, {})
        by_name: dict[str, TagCategory] = {}
        #: 类别名 → {取值名: 取值 id}，第三遍挂父子取值时靠它查父取值
        value_ids: dict[str, dict[str, int]] = {}

        # 第一遍：建类别。父类别一定排在子类别前面（DEFAULT_CATEGORIES 的顺序保证），
        # 但这里不依赖顺序 —— 先全部建出来，再统一挂父子关系。
        for index, name in enumerate(names):
            category = TagCategory(owner_id=owner_id, scope=scope, name=name, sort_order=index)
            db.add(category)
            await db.flush()  # 拿自增 id
            by_name[name] = category
            created_cats += 1

        # 第二遍：挂级联 + 灌顶层类别的预置取值（大类 / 颜色 / 季节…）
        for name in names:
            category = by_name[name]
            parent_name = cascade.get(name)
            if parent_name is not None:
                category.parent_category_id = by_name[parent_name].id

            mapping = value_ids.setdefault(name, {})
            for v_index, value in enumerate(presets.get(name, [])):
                tag_value = TagValue(
                    owner_id=owner_id,
                    category_id=category.id,
                    value=value,
                    sort_order=v_index,
                    is_seed=True,
                )
                db.add(tag_value)
                await db.flush()
                mapping[value] = tag_value.id
                created_values += 1

        # 第三遍：子类别的预置取值，按父取值挂上去（小类 → 大类取值）
        for child_name, groups in cascaded.items():
            if child_name not in by_name:
                continue
            category = by_name[child_name]
            parent_map = value_ids.get(cascade.get(child_name, ""), {})
            mapping = value_ids.setdefault(child_name, {})
            sort_order = 0
            for parent_value, children in groups.items():
                parent_id = parent_map.get(parent_value)
                for value in children:
                    tag_value = TagValue(
                        owner_id=owner_id,
                        category_id=category.id,
                        value=value,
                        sort_order=sort_order,
                        parent_value_id=parent_id,
                        is_seed=True,
                    )
                    db.add(tag_value)
                    await db.flush()
                    mapping[value] = tag_value.id
                    created_values += 1
                    sort_order += 1

    return created_cats, created_values


async def reset_tags(db: AsyncSession, owner_id: int, scope: str | None = None) -> dict[str, int]:
    """清空该用户的标签体系并按当前默认值重建。

    衣物本身不动，只解除标签关联。用在「默认分类方案改了、想把环境拉回新默认」的场景。

    ``scope`` 只重建一侧（``garment`` / ``outfit``）—— 例如给老用户补上搭配侧的
    「风格 / 色系」取值，而不动他已经整理好的衣物标签。

    返回各步骤影响的行数，便于调用方打印。

    ⚠️ 删除顺序：先解关联 → 再把自引用外键全部置 NULL → 最后删行。
    SQLite 的 ``ON DELETE SET NULL`` 只在 ``PRAGMA foreign_keys=ON`` 时生效，
    而批量 DELETE 里的级联行为不好预期，显式置 NULL 最省心。
    """
    cat_ids = select(TagCategory.id).where(TagCategory.owner_id == owner_id)
    if scope:
        cat_ids = cat_ids.where(TagCategory.scope == scope)
    value_ids = select(TagValue.id).where(TagValue.category_id.in_(cat_ids))
    untagged = (
        await db.execute(delete(GarmentTag).where(GarmentTag.tag_value_id.in_(value_ids)))
    ).rowcount or 0
    untagged_outfits = (
        await db.execute(delete(OutfitTag).where(OutfitTag.tag_value_id.in_(value_ids)))
    ).rowcount or 0

    # 自引用外键先断开，避免删除顺序带来的 SET NULL / 约束报错
    await db.execute(
        update(TagValue)
        .where(TagValue.owner_id == owner_id)
        .values(parent_value_id=None)
    )
    await db.execute(
        update(TagCategory)
        .where(TagCategory.owner_id == owner_id)
        .values(parent_category_id=None)
    )

    removed_values = (
        await db.execute(delete(TagValue).where(TagValue.category_id.in_(cat_ids)))
    ).rowcount
    cat_stmt = delete(TagCategory).where(TagCategory.owner_id == owner_id)
    if scope:
        cat_stmt = cat_stmt.where(TagCategory.scope == scope)
    removed_cats = (await db.execute(cat_stmt)).rowcount
    await db.flush()

    new_cats, new_values = await seed_default_categories(
        db, owner_id, only_scope=scope, force=bool(scope)
    )
    return {
        "untagged": untagged,
        "untagged_outfits": untagged_outfits,
        "removed_values": removed_values or 0,
        "removed_categories": removed_cats or 0,
        "created_categories": new_cats,
        "created_values": new_values,
    }
