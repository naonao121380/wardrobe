import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { wardrobeApi, type TagCategory, type TagScope } from "../api/wardrobe";
import Dialog from "./Dialog";

type Props = {
  scope: TagScope;
  onClose: () => void;
  /** 标签体系变更后回调，让父级刷新筛选栏与列表。 */
  onChanged: () => void;
};

/** 标签类别 / 取值管理器：增删改类别与取值。 */
export default function TagManager({ scope, onClose, onChanged }: Props) {
  const [cats, setCats] = useState<TagCategory[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [newCat, setNewCat] = useState("");
  const [busy, setBusy] = useState(false);

  async function reload() {
    setLoading(true);
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

  const addCategory = () => {
    const name = newCat.trim();
    if (!name) return;
    void guard(async () => {
      await wardrobeApi.createCategory(scope, name);
      setNewCat("");
    });
  };

  return (
    <Dialog
      title={scope === "garment" ? "管理衣物标签" : "管理搭配标签"}
      onClose={onClose}
      width={620}
      footer={
        <div className="row-end">
          <button type="button" className="btn" onClick={onClose}>
            完成
          </button>
        </div>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}

      <div className="tagmgr-add">
        <input
          value={newCat}
          placeholder="新类别名，如「版型」"
          onChange={(e) => setNewCat(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && addCategory()}
        />
        <button type="button" className="btn btn-primary" disabled={busy || !newCat.trim()} onClick={addCategory}>
          添加类别
        </button>
      </div>

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
              busy={busy}
              onRename={(name) => guard(() => wardrobeApi.renameCategory(cat.id, name))}
              onDelete={() => {
                if (!confirm(`删除类别「${cat.name}」？它的 ${cat.values.length} 个取值会一起删掉，已打标的衣物会自动解除。`))
                  return;
                void guard(() => wardrobeApi.deleteCategory(cat.id));
              }}
              onAddValue={(v) => guard(() => wardrobeApi.createValue(cat.id, v))}
              onRenameValue={(id, v) => guard(() => wardrobeApi.renameValue(id, v))}
              onDeleteValue={(id, v) => {
                if (!confirm(`删除取值「${v}」？已打标的衣物会自动解除。`)) return;
                void guard(() => wardrobeApi.deleteValue(id));
              }}
            />
          ))}
        </div>
      )}
    </Dialog>
  );
}

function CategoryRow({
  cat,
  busy,
  onRename,
  onDelete,
  onAddValue,
  onRenameValue,
  onDeleteValue,
}: {
  cat: TagCategory;
  busy: boolean;
  onRename: (name: string) => void;
  onDelete: () => void;
  onAddValue: (value: string) => void;
  onRenameValue: (id: number, value: string) => void;
  onDeleteValue: (id: number, value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(cat.name);
  const [adding, setAdding] = useState(false);
  const [newValue, setNewValue] = useState("");

  useEffect(() => setDraft(cat.name), [cat.name]);

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

      <div className="chips">
        {cat.values.map((v) => (
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
            <button
              type="button"
              className="chip-x"
              title="删除"
              onClick={() => onDeleteValue(v.id, v.value)}
            >
              ✕
            </button>
          </span>
        ))}

        {adding ? (
          <input
            className="chip-input"
            autoFocus
            value={newValue}
            placeholder="取值，回车确认"
            onChange={(e) => setNewValue(e.target.value)}
            onBlur={() => setAdding(false)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && newValue.trim()) {
                onAddValue(newValue.trim());
                setNewValue("");
              }
              if (e.key === "Escape") setAdding(false);
            }}
          />
        ) : (
          <button type="button" className="chip chip-add" onClick={() => setAdding(true)}>
            + 取值
          </button>
        )}
      </div>
    </section>
  );
}
