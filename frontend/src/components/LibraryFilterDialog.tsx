import { useMemo, useState } from "react";

import type { TagCategory, TagValue } from "../api/wardrobe";
import Dialog from "./Dialog";

type Props = {
  /** 衣物范围的标签类别（`listCategories("garment")`）。 */
  categories: TagCategory[];
  /** 已经选中的取值 id；打开时接着上次的来。 */
  value: number[];
  onApply: (ids: number[]) => void;
  onClose: () => void;
};

type Dim = { cat: TagCategory; values: TagValue[] };

/**
 * 素材库的标签筛选弹窗。
 *
 * 勾选是**多选**：同一组里选多个是「或」，不同组之间是「且」（由 `buildFacetGroups`
 * 摊平成后端要的 `tag_groups`）。改完点「应用」才生效 —— 边选边筛会让背后的素材墙
 * 一直跳，反而看不清自己选了什么。
 *
 * 只列**有衣物在用的**标签（类别与取值都按 `usage_count` 过滤）：把用不到的取值摆出来
 * 等于摆一堆必然筛出空结果的按钮。历史遗留的同名类别（旧分类树）也因此不会冒出来。
 */
export default function LibraryFilterDialog({ categories, value, onApply, onClose }: Props) {
  const [selected, setSelected] = useState(() => new Set(value));

  const dims = useMemo<Dim[]>(
    () =>
      categories
        .filter((c) => c.usage_count > 0)
        .map((cat) => ({ cat, values: cat.values.filter((v) => v.usage_count > 0) }))
        .filter((d) => d.values.length > 0),
    [categories],
  );

  const toggle = (id: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  return (
    <Dialog
      title="筛选素材"
      onClose={onClose}
      width={600}
      footer={
        <>
          <span className="tiny">已选 {selected.size} 项</span>
          <div className="row-end">
            <button
              type="button"
              className="btn btn-sm"
              disabled={selected.size === 0}
              onClick={() => setSelected(new Set())}
            >
              清空
            </button>
            <button type="button" className="btn" onClick={onClose}>
              取消
            </button>
            <button type="button" className="btn btn-primary" onClick={() => onApply([...selected])}>
              应用
            </button>
          </div>
        </>
      }
    >
      {dims.length === 0 ? (
        <p className="tiny">
          还没有衣物被打上标签，没得筛 —— 先在衣物页给衣物打标（「批量 → 加标签」），
          或在「管理标签」里补几个类别。
        </p>
      ) : (
        <>
          <p className="tiny libfilter-tip">
            同一组里选多个是「或」，不同组之间是「且」；只列出有衣物在用的标签。
          </p>
          {dims.map((d) => (
            <div className="filter-section" key={d.cat.id}>
              <span className="filter-section-name">{d.cat.name}</span>
              <div className="chips">
                {d.values.map((v) => (
                  <button
                    type="button"
                    key={v.id}
                    className={`chip chip-toggle${selected.has(v.id) ? " on" : ""}`}
                    onClick={() => toggle(v.id)}
                  >
                    {v.value}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </>
      )}
    </Dialog>
  );
}
