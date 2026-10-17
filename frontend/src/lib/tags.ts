/**
 * 标签体系的纯函数工具。
 *
 * 服务端的标签是**两层结构**（类别 → 取值），外加一层「取值挂取值」用来做
 * 「大类 → 小类」的级联。这里把级联关系算清楚，让各个界面（筛选栏 / 编辑器 /
 * 标签管理）共用同一套规则，不要各写一份。
 */

import type { TagCategory, TagValue } from "../api/wardrobe";

/** 找类别。 */
export function findCategory(categories: TagCategory[], id: number | null): TagCategory | null {
  if (id === null) return null;
  return categories.find((c) => c.id === id) ?? null;
}

/**
 * 该类别参与级联时的**父类别**；顶层类别（大类 / 颜色 / 材质…）返回 null。
 *
 * 父类别只认服务端返回的 `parent_category_id`，前端不猜名字。
 */
export function parentCategory(
  cat: TagCategory,
  categories: TagCategory[],
): TagCategory | null {
  return findCategory(categories, cat.parent_category_id ?? null);
}

/** 该类别下的取值，按父取值分组。顶层类别只有一个「无分组」的组。 */
export type ValueGroup = {
  /** null = 未归类（顶层类别的全部取值，或子类别里没挂父值的取值） */
  parent: TagValue | null;
  values: TagValue[];
};

export function groupValues(cat: TagCategory, parentCat: TagCategory | null): ValueGroup[] {
  if (parentCat === null) {
    return cat.values.length ? [{ parent: null, values: cat.values }] : [];
  }
  const groups: ValueGroup[] = [];
  for (const pv of parentCat.values) {
    const values = cat.values.filter((v) => v.parent_value_id === pv.id);
    if (values.length) groups.push({ parent: pv, values });
  }
  const orphans = cat.values.filter(
    (v) => v.parent_value_id === null || !parentCat.values.some((p) => p.id === v.parent_value_id),
  );
  if (orphans.length) groups.push({ parent: null, values: orphans });
  return groups;
}

/**
 * 勾选 / 取消一个取值，并维护级联一致性：
 *
 * - 选中子值 → 自动带上它的父值（「风衣」必然是「外套」，不该让用户点两次）
 * - 取消父值 → 连带取消挂在它下面的子值（否则会留下看不见却生效的筛选条件）
 *
 * 返回新集合；没有变化时返回原集合，方便 `setState` 短路。
 */
export function toggleCascaded(
  selected: Set<number>,
  valueId: number,
  categories: TagCategory[],
): Set<number> {
  const owner = categories.find((c) => c.values.some((v) => v.id === valueId));
  if (!owner) return selected;

  const next = new Set(selected);

  if (next.has(valueId)) {
    next.delete(valueId);
    // 取消的是父值 → 把挂在它下面的子值一并撤掉
    for (const cat of categories) {
      if (parentCategory(cat, categories)?.id !== owner.id) continue;
      for (const v of cat.values) {
        if (v.parent_value_id === valueId) next.delete(v.id);
      }
    }
    return next;
  }

  next.add(valueId);
  const value = owner.values.find((v) => v.id === valueId);
  if (value?.parent_value_id != null) next.add(value.parent_value_id);
  return next;
}

/**
 * 一个筛选**维度**的选中状态：若干条「或条件」，每条是一组要同时满足的标签 id。
 *
 * 例：分类维度选了「上衣:衬衫」和「裤子:牛仔裤」→
 * `[[上衣, 衬衫], [裤子, 牛仔裤]]`（两组之间是 OR，组内是 AND）。
 */
export type FacetGroups = number[][];

/**
 * 把多个维度摊平成后端要的 `tag_groups`（组内 AND、组间 OR）。
 *
 * 规则：**维度内 OR、维度间 AND**。笛卡尔积即可把「维度间 AND」摊平 ——
 * `(A ∪ B) ∩ (C ∪ D)` = `(A∩C) ∪ (A∩D) ∪ (B∩C) ∪ (B∩D)`。
 * 默认的单选模式下每个维度只有 1 条，积不会膨胀。
 */
export function buildTagGroups(facets: FacetGroups[]): number[][] {
  let groups: number[][] = [[]];
  for (const facet of facets) {
    if (facet.length === 0) continue;
    groups = groups.flatMap((g) => facet.map((f) => [...g, ...f]));
  }
  return groups.filter((g) => g.length > 0);
}

/**
 * 把「扁平的一组选中取值」按**类别**切成维度，供 {@link buildTagGroups} 摊平。
 *
 * 界面（勾选式弹窗）里只有一池子勾中的标签 id，维度得在这里补回来：
 *
 * - 一个类别 = 一个维度（同一类别里选中的取值互为「或」，类别之间取「且」）；
 * - 认不出来的 id（类别被删了、或来自别的 scope）直接忽略，不发出去白筛一场。
 *
 * 注意：不做「大类 + 小类算同一个维度」的合并 —— 服务端把分类拆成了两个类别，
 * 但它们各自是一套独立的标签（衣物可以只打大类、不打小类），当两个维度取交集
 * 才是对的；真合并了反而会把只打了大类的衣物筛掉。
 */
export function buildFacetGroups(
  categories: TagCategory[],
  selected: Iterable<number>,
): FacetGroups[] {
  const wanted = new Set(selected);
  if (wanted.size === 0) return [];

  const facets: FacetGroups[] = [];
  for (const cat of categories) {
    const hits = cat.values.filter((v) => wanted.has(v.id)).map((v) => [v.id]);
    if (hits.length > 0) facets.push(hits);
  }
  return facets;
}
