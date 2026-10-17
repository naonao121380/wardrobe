import { useMemo, useState } from "react";

import type { TagCategory } from "../api/wardrobe";
import { toggleCascaded } from "../lib/tags";
import Dialog from "./Dialog";
import { TagPickRow } from "./GarmentEditor";

type Props = {
  /** 要改标签的衣物数量，只用于文案 */
  count: number;
  categories: TagCategory[];
  onClose: () => void;
  onApply: (valueIds: number[], mode: "add" | "remove" | "replace") => Promise<void>;
};

const MODES: { key: "add" | "remove" | "replace"; label: string; hint: string }[] = [
  { key: "add", label: "追加标签", hint: "给选中的衣物加上这些标签，原有的保留" },
  { key: "remove", label: "移除标签", hint: "从选中的衣物上去掉这些标签" },
  { key: "replace", label: "覆盖标签", hint: "选中的衣物标签统一改成这些（会清掉原有的）" },
];

/**
 * 批量打标：勾好一批衣物后一次性给它们加上同一组标签。
 *
 * 走的是**追加**语义（默认），因为批量场景几乎都是「这堆都是上衣、都是黑色」——
 * 覆盖会把手打的标签冲掉，所以单独做成可选项，且文案里写明后果。
 */
export default function BatchTagDialog({ count, categories, onClose, onApply }: Props) {
  const [mode, setMode] = useState<"add" | "remove" | "replace">("add");
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const current = useMemo(
    () => MODES.find((m) => m.key === mode) ?? MODES[0],
    [mode],
  );

  function toggle(valueId: number) {
    // 和单件编辑保持一致：选小类自动带上它的大类
    setSelected((prev) => toggleCascaded(prev, valueId, categories));
  }

  async function apply() {
    if (selected.size === 0) {
      setErr("还没选标签");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      await onApply([...selected], mode);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "批量打标失败");
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={`批量${mode === "remove" ? "移除" : "新增"}标签（${count} 件）`}
      onClose={onClose}
      width={640}
      footer={
        <div className="row-between">
          <span className="tiny">已选 {selected.size} 个标签</span>
          <div className="row-end">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className={mode === "remove" ? "btn btn-danger" : "btn btn-primary"}
              onClick={() => void apply()}
              disabled={busy}
            >
              {busy ? "提交中…" : `应用到 ${count} 件`}
            </button>
          </div>
        </div>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}

      <div className="field">
        <label>方式</label>
        <div className="chips">
          {MODES.map((m) => (
            <button
              type="button"
              key={m.key}
              className={`chip chip-toggle${mode === m.key ? " on" : ""}`}
              onClick={() => setMode(m.key)}
            >
              {m.label}
            </button>
          ))}
        </div>
        <p className="tiny">{current.hint}</p>
      </div>

      <div className="field">
        <label>标签</label>
        {categories.length === 0 ? (
          <p className="tiny">还没有标签类别，先在「管理标签」里建。</p>
        ) : (
          <div className="tagpick">
            {categories.map((cat) => (
              <TagPickRow
                key={cat.id}
                cat={cat}
                categories={categories}
                selected={selected}
                onToggle={toggle}
              />
            ))}
          </div>
        )}
      </div>
    </Dialog>
  );
}
