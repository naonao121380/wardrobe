import { useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { wardrobeApi, type TagCategory, type TagScope, type TagValue } from "../api/wardrobe";
import { confirmDialog } from "../lib/confirm";
import { parentCategory } from "../lib/tags";
import Dialog from "./Dialog";

type Props = {
  scope: TagScope;
  onClose: () => void;
  /** 标签体系变更后回调，让父级刷新筛选栏与列表。 */
  onChanged: () => void;
};

/** 标签类别 / 取值管理器：增删改类别与取值，并维护「大类 → 小类」的级联归属。 */
export default function TagManager({ scope, onClose, onChanged }: Props) {
  const [cats, setCats] = useState<TagCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [newCat, setNewCat] = useState("");
  const [newCatParent, setNewCatParent] = useState<string>("");
  const [busy, setBusy] = useState(false);
  /** 导入 / 导出这类一次性操作的结果，用一行话报出来。 */
  const [notice, setNotice] = useState<string | null>(null);
  /** 导入时那些「没能按原样落下来」的项（父级认不出、超两层被降级…）。 */
  const [warnings, setWarnings] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement | null>(null);

  async function reload() {
    // 注意：不要在这里 setLoading(true) —— 那会把整个列表卸载成「加载中…」，
    // 正在进行的输入状态全丢，视觉上像操作失败。loading 只管首次进入。
    try {
      setCats(await wardrobeApi.listCategories(scope));
      setErr(null);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "加载标签失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  async function guard(fn: () => Promise<unknown>) {
    setBusy(true);
    setErr(null);
    try {
      await fn();
      await reload();
      onChanged();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  // 只有顶层类别能当父类别 —— 服务端只允许两层，前端不给出会必然失败的选项
  const topLevel = cats.filter((c) => c.parent_category_id === null);

  const addCategory = () => {
    const name = newCat.trim();
    if (!name) return;
    void guard(async () => {
      await wardrobeApi.createCategory(scope, name, newCatParent === "" ? null : Number(newCatParent));
      setNewCat("");
      setNewCatParent("");
    });
  };

  /** 导出成 JSON 文件。文件名带作用域与日期，一个目录里放几份也分得清。 */
  async function exportConfig() {
    setBusy(true);
    setErr(null);
    setNotice(null);
    setWarnings([]);
    try {
      const data = await wardrobeApi.exportTagConfig(scope);
      const valueCount = data.categories.reduce((n, c) => n + c.values.length, 0);
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      const d = new Date();
      const stamp = `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(
        d.getDate(),
      ).padStart(2, "0")}`;
      a.href = url;
      a.download = `wardrobe-tags-${scope}-${stamp}.json`;
      a.click();
      // 立刻 revoke 会把部分浏览器的下载掐掉，推到下一轮事件循环
      setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice(`已导出 ${data.categories.length} 个类别、${valueCount} 个取值。`);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "导出失败");
    } finally {
      setBusy(false);
    }
  }

  /** 导入：**只补不删**，同名的类别与取值一律跳过，不覆盖本地已有的东西。 */
  async function importFrom(file: File | null | undefined) {
    if (!file) return;
    setBusy(true);
    setErr(null);
    setNotice(null);
    setWarnings([]);
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        throw new Error("这不是合法的 JSON 文件");
      }
      const res = await wardrobeApi.importTagConfig(
        parsed as Parameters<typeof wardrobeApi.importTagConfig>[0],
        scope,
      );
      await reload();
      onChanged();
      setWarnings(res.warnings);
      setNotice(
        `导入完成：新增 ${res.categories_created} 个类别、${res.values_created} 个取值；` +
          `跳过 ${res.categories_skipped} 个已有类别、${res.values_skipped} 个已有取值。`,
      );
    } catch (e) {
      setErr(e instanceof ApiError || e instanceof Error ? e.message : "导入失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={scope === "garment" ? "管理衣物标签" : "管理搭配标签"}
      onClose={onClose}
      width={680}
      footer={
        <div className="row-end">
          <button
            type="button"
            className="btn"
            disabled={busy}
            title="把当前整套标签配置导出成 JSON，换账号或换机器时可以再导回来"
            onClick={() => void exportConfig()}
          >
            导出配置
          </button>
          <button
            type="button"
            className="btn"
            disabled={busy}
            title="从 JSON 文件导入标签配置：只新增，不会覆盖或删除已有的标签"
            onClick={() => fileRef.current?.click()}
          >
            导入配置
          </button>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            完成
          </button>
        </div>
      }
    >
      <input
        ref={fileRef}
        type="file"
        accept="application/json,.json"
        hidden
        onChange={(e) => {
          const file = e.currentTarget.files?.[0];
          // 清空 value，否则连着选同一个文件不会再触发 change
          e.currentTarget.value = "";
          void importFrom(file);
        }}
      />

      {err && <div className="alert alert-error">{err}</div>}

      {notice && (
        <div className="alert alert-warn">
          <div>{notice}</div>
          {warnings.length > 0 && (
            <ul className="tiny" style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {warnings.map((w, i) => (
                <li key={i}>{w}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="tagmgr-add">
        <input
          value={newCat}
          placeholder="新类别名，如「版型」"
          onChange={(e) => setNewCat(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addCategory()}
        />
        <select
          value={newCatParent}
          onChange={(e) => setNewCatParent(e.target.value)}
          title="挂到某个顶层类别下，它的取值就能按父值分组（如「小类」挂在「大类」下）"
        >
          <option value="">顶层类别</option>
          {topLevel.map((c) => (
            <option key={c.id} value={c.id}>
              挂在「{c.name}」下
            </option>
          ))}
        </select>
        <button type="button" className="btn btn-primary" disabled={busy || !newCat.trim()} onClick={addCategory}>
          添加类别
        </button>
      </div>

      <p className="tiny">
        标签配置可导出成 JSON，换账号 / 换机器时用「导入配置」一键搬过去。
        导入取<strong>并集</strong>：同名的类别与取值直接跳过，不覆盖、不改名、不删除已有标签 ——
        可以放心反复导。
      </p>

      {loading ? (
        <div className="empty">加载中…</div>
      ) : cats.length === 0 ? (
        <div className="empty">还没有任何类别，先添加一个吧。</div>
      ) : (
        <div className="tagmgr-list">
          {cats.map((cat) => (
            <CategoryRow
              key={cat.id}
              cat={cat}
              categories={cats}
              busy={busy}
              onRename={(name) => guard(() => wardrobeApi.renameCategory(cat.id, name))}
              onSetParent={(parentId) => guard(() => wardrobeApi.setCategoryParent(cat.id, parentId))}
              onDelete={async () => {
                // 整个类别都没被任何衣物用过 → 直接删，不弹窗打扰
                if (cat.usage_count > 0) {
                  const parent = parentCategory(cat, cats);
                  const ok = await confirmDialog({
                    title: "删除这个类别？",
                    message: (
                      <>
                        <p>
                          「{cat.name}」的 {cat.values.length} 个取值会一起删掉，其中{" "}
                          {cat.usage_count} 处正在被衣物使用，删除后会自动解除打标。
                        </p>
                        {parent && (
                          <p className="tiny">取值不会丢，只会变成「未归类」。</p>
                        )}
                      </>
                    ),
                    confirmLabel: "删除类别",
                    danger: true,
                  });
                  if (!ok) return;
                }
                void guard(() => wardrobeApi.deleteCategory(cat.id));
              }}
              onAddValue={(v, parentValueId) =>
                guard(() => wardrobeApi.createValue(cat.id, v, parentValueId))
              }
              onMoveValue={(id, parentValueId) =>
                guard(() => wardrobeApi.setValueParent(id, parentValueId))
              }
              onRenameValue={(id, v) => guard(() => wardrobeApi.renameValue(id, v))}
              onDeleteValue={async (value) => {
                // 没有任何衣物命中 → 直接删；命中了才确认，并说清楚影响面
                if (value.usage_count > 0) {
                  const ok = await confirmDialog({
                    title: "删除这个取值？",
                    message: (
                      <p>
                        「{value.value}」有 {value.usage_count} 件衣物正在用它，删除后会自动解除打标。
                      </p>
                    ),
                    confirmLabel: "删除取值",
                    danger: true,
                  });
                  if (!ok) return;
                }
                void guard(() => wardrobeApi.deleteValue(value.id));
              }}
            />
          ))}
        </div>
      )}
    </Dialog>
  );
}

type Group = { parent: TagValue | null; values: TagValue[] };

/**
 * 管理器里的分组：**每个父取值都给一个组**（哪怕是空的，好往里加），外加一个「未归类」。
 * 和筛选栏不同 —— 筛选栏只列有值的组，管理器要让每个位置都能新增。
 */
function managerGroups(cat: TagCategory, parent: TagCategory | null): Group[] {
  if (parent === null) return [{ parent: null, values: cat.values }];
  const known = new Set(parent.values.map((p) => p.id));
  return [
    ...parent.values.map((pv) => ({
      parent: pv,
      values: cat.values.filter((v) => v.parent_value_id === pv.id),
    })),
    {
      parent: null,
      values: cat.values.filter((v) => v.parent_value_id === null || !known.has(v.parent_value_id)),
    },
  ];
}

function CategoryRow({
  cat,
  categories,
  busy,
  onRename,
  onSetParent,
  onDelete,
  onAddValue,
  onMoveValue,
  onRenameValue,
  onDeleteValue,
}: {
  cat: TagCategory;
  categories: TagCategory[];
  busy: boolean;
  onRename: (name: string) => void;
  onSetParent: (parentCategoryId: number | null) => void;
  onDelete: () => void;
  onAddValue: (value: string, parentValueId: number | null) => void;
  onMoveValue: (valueId: number, parentValueId: number | null) => void;
  onRenameValue: (id: number, value: string) => void;
  onDeleteValue: (value: TagValue) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(cat.name);
  const [addingIn, setAddingIn] = useState<string | null>(null);
  const [newValue, setNewValue] = useState("");
  const [movingId, setMovingId] = useState<number | null>(null);

  useEffect(() => setDraft(cat.name), [cat.name]);

  const parent = parentCategory(cat, categories);
  const groups = managerGroups(cat, parent);
  const canReparent = cat.values.length === 0;

  function submitValue(parentValueId: number | null) {
    const v = newValue.trim();
    // 失焦 / 回车都走这里：空值只关闭输入框，非空一律提交
    if (!v) {
      setAddingIn(null);
      return;
    }
    onAddValue(v, parentValueId);
    setNewValue("");
    setAddingIn(null);
  }

  function renderChip(v: TagValue) {
    if (movingId === v.id && parent) {
      return (
        <select
          key={v.id}
          className="chip-input"
          autoFocus
          defaultValue={v.parent_value_id ?? ""}
          onChange={(e) => {
            const next = e.target.value === "" ? null : Number(e.target.value);
            setMovingId(null);
            if (next !== v.parent_value_id) onMoveValue(v.id, next);
          }}
          onKeyDown={(e) => e.key === "Escape" && (e.stopPropagation(), setMovingId(null))}
        >
          <option value="">未归类</option>
          {parent.values.map((pv) => (
            <option key={pv.id} value={pv.id}>
              {pv.value}
            </option>
          ))}
        </select>
      );
    }

    return (
      <span className="chip" key={v.id}>
        {v.value}
        <button
          type="button"
          className="chip-x"
          title="重命名"
          onClick={() => {
            const next = prompt(`把「${v.value}」改成：`, v.value);
            if (next && next.trim() && next.trim() !== v.value) onRenameValue(v.id, next.trim());
          }}
        >
          ✎
        </button>
        {parent && (
          <button
            type="button"
            className="chip-x"
            title="改归属"
            onClick={() => setMovingId(v.id)}
          >
            ⇄
          </button>
        )}
        <button
          type="button"
          className="chip-x"
          title={(v.usage_count ?? 0) > 0 ? `删除（${v.usage_count} 件在用）` : "删除"}
          onClick={() => onDeleteValue(v)}
        >
          ✕
        </button>
      </span>
    );
  }

  return (
    <section className="tagcat">
      <header className="tagcat-head">
        {editing ? (
          <>
            <input
              className="tagcat-input"
              value={draft}
              autoFocus
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && draft.trim()) {
                  onRename(draft.trim());
                  setEditing(false);
                }
                if (e.key === "Escape") {
                  // 只取消改名，不要冒泡把整个管理弹窗关掉
                  e.stopPropagation();
                  setDraft(cat.name);
                  setEditing(false);
                }
              }}
            />
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={busy || !draft.trim()}
              onClick={() => {
                onRename(draft.trim());
                setEditing(false);
              }}
            >
              保存
            </button>
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => {
                setDraft(cat.name);
                setEditing(false);
              }}
            >
              取消
            </button>
          </>
        ) : (
          <>
            <strong>{cat.name}</strong>
            <span className="tiny">{cat.values.length} 个取值</span>
            <select
              className="tagcat-parent"
              value={cat.parent_category_id ?? ""}
              disabled={busy}
              title={
                canReparent
                  ? "挂到某个顶层类别下，取值就能按父值分组"
                  : "该类别已有取值，不能再改层级（会变成三层）"
              }
              onChange={(e) => {
                const next = e.target.value === "" ? null : Number(e.target.value);
                if (next !== (cat.parent_category_id ?? null)) onSetParent(next);
              }}
            >
              <option value="">顶层类别</option>
              {categories
                .filter((c) => c.id !== cat.id && c.parent_category_id === null)
                .map((c) => (
                  <option key={c.id} value={c.id} disabled={!canReparent}>
                    ↳ 挂在「{c.name}」下
                  </option>
                ))}
            </select>
            <div className="row-end">
              <button type="button" className="btn btn-sm" onClick={() => setEditing(true)}>
                改名
              </button>
              <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={onDelete}>
                删除
              </button>
            </div>
          </>
        )}
      </header>

      {parent === null ? (
        <div className="chips">
          {cat.values.map(renderChip)}
          {addingIn === "flat" ? (
            <input
              className="chip-input"
              autoFocus
              value={newValue}
              placeholder="输入取值，回车确认"
              onChange={(e) => setNewValue(e.target.value)}
              onBlur={() => submitValue(null)}
              onKeyDown={(e) => {
                if (e.key === "Enter") submitValue(null);
                if (e.key === "Escape") {
                  // 只取消输入，不要冒泡把整个管理弹窗关掉
                  e.stopPropagation();
                  setNewValue("");
                  setAddingIn(null);
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="chip chip-add"
              onClick={() => {
                setNewValue("");
                setAddingIn("flat");
              }}
            >
              + 取值
            </button>
          )}
        </div>
      ) : (
        <div className="tagcat-groups">
          {groups.map((g) => {
            const key = g.parent ? `p${g.parent.id}` : "orphan";
            return (
              <div className="taggroup" key={key}>
                <span className="taggroup-name">
                  {g.parent ? g.parent.value : "未归类"}
                  <em className="tiny">{g.values.length}</em>
                </span>
                <div className="chips">
                  {g.values.map(renderChip)}
                  {addingIn === key ? (
                    <input
                      className="chip-input"
                      autoFocus
                      value={newValue}
                      placeholder="输入取值，回车确认"
                      onChange={(e) => setNewValue(e.target.value)}
                      onBlur={() => submitValue(g.parent ? g.parent.id : null)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") submitValue(g.parent ? g.parent.id : null);
                        if (e.key === "Escape") {
                          // 只取消输入，不要冒泡把整个管理弹窗关掉
                          e.stopPropagation();
                          setNewValue("");
                          setAddingIn(null);
                        }
                      }}
                    />
                  ) : (
                    <button
                      type="button"
                      className="chip chip-add"
                      onClick={() => {
                        setNewValue("");
                        setAddingIn(key);
                      }}
                    >
                      + 取值
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
