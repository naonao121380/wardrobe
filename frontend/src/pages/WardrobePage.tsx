import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { wardrobeApi, type Garment, type TagCategory } from "../api/wardrobe";
import GarmentEditor from "../components/GarmentEditor";
import TagManager from "../components/TagManager";

const PAGE_SIZE = 48;

export default function WardrobePage() {
  const [categories, setCategories] = useState<TagCategory[]>([]);
  const [items, setItems] = useState<Garment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [activeTags, setActiveTags] = useState<Set<number>>(new Set());

  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [editing, setEditing] = useState<Garment | null>(null);
  const [creating, setCreating] = useState(false);
  const [managingTags, setManagingTags] = useState(false);

  const reqId = useRef(0);

  const loadCategories = useCallback(async () => {
    try {
      setCategories(await wardrobeApi.listCategories("garment"));
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "加载标签失败");
    }
  }, []);

  useEffect(() => {
    void loadCategories();
  }, [loadCategories]);

  // 搜索防抖，避免每敲一个字都打一次接口
  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQuery(query);
      setPage(1); // 换了搜索词就回到第一页，否则可能停在越界页码上
    }, 250);
    return () => clearTimeout(t);
  }, [query]);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    wardrobeApi
      .listGarments({
        q: debouncedQuery || undefined,
        tagValueIds: [...activeTags],
        page,
        pageSize: PAGE_SIZE,
      })
      .then((data) => {
        if (id !== reqId.current) return; // 丢弃过期响应，避免筛选快速切换时串数据
        setItems(data.items);
        setTotal(data.total);
        setErr(null);
      })
      .catch((e) => {
        if (id !== reqId.current) return;
        setErr(e instanceof ApiError ? e.message : "加载衣物失败");
      })
      .finally(() => {
        if (id === reqId.current) setLoading(false);
      });
  }, [debouncedQuery, activeTags, page]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtering = activeTags.size > 0 || debouncedQuery.trim().length > 0;

  function toggleTag(valueId: number) {
    setActiveTags((prev) => {
      const next = new Set(prev);
      if (next.has(valueId)) next.delete(valueId);
      else next.add(valueId);
      return next;
    });
    setPage(1);
  }

  function clearFilters() {
    setActiveTags(new Set());
    setQuery("");
    setPage(1);
  }

  function applySaved(g: Garment, isNew: boolean) {
    setItems((prev) => {
      const idx = prev.findIndex((x) => x.id === g.id);
      if (idx === -1) return [g, ...prev];
      const next = prev.slice();
      next[idx] = g;
      return next;
    });
    if (isNew) setTotal((t) => t + 1);
  }

  async function remove(g: Garment) {
    if (!confirm(`删除「${g.name}」？它的 ${g.images.length} 张图片也会一并删除。`)) return;
    try {
      await wardrobeApi.deleteGarment(g.id);
      setItems((prev) => prev.filter((x) => x.id !== g.id));
      setTotal((t) => Math.max(0, t - 1));
    } catch (e) {
      alert(e instanceof ApiError ? e.message : "删除失败");
    }
  }

  return (
    <div className="wardrobe">
      <div className="toolbar">
        <div className="search">
          <input
            value={query}
            placeholder="搜索名称…"
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <div className="row-end">
          <span className="tiny">
            共 {total} 件{filtering ? "（已筛选）" : ""}
          </span>
          <button type="button" className="btn btn-sm" onClick={() => setManagingTags(true)}>
            管理标签
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            + 添加衣物
          </button>
        </div>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {categories.length > 0 && (
        <div className="filterbar">
          {categories.map((cat) => (
            <div className="filterbar-row" key={cat.id}>
              <span className="filterbar-name">{cat.name}</span>
              <div className="chips">
                {cat.values.length === 0 ? (
                  <span className="tiny">（无取值）</span>
                ) : (
                  cat.values.map((v) => (
                    <button
                      type="button"
                      key={v.id}
                      className={`chip chip-toggle${activeTags.has(v.id) ? " on" : ""}`}
                      onClick={() => toggleTag(v.id)}
                    >
                      {v.value}
                    </button>
                  ))
                )}
              </div>
            </div>
          ))}
          {filtering && (
            <div className="filterbar-foot">
              <button type="button" className="btn btn-sm" onClick={clearFilters}>
                清空筛选
              </button>
            </div>
          )}
        </div>
      )}

      {loading ? (
        <div className="empty">加载中…</div>
      ) : items.length === 0 ? (
        <div className="card placeholder">
          <h3>{filtering ? "没有匹配的衣物" : "衣橱还是空的"}</h3>
          <p>
            {filtering
              ? "换个关键词，或清空筛选条件试试。"
              : "点右上角「添加衣物」上传第一件，图片会自动生成缩略图。"}
          </p>
        </div>
      ) : (
        <div className="wall">
          {items.map((g) => (
            <article className="gcard" key={g.id}>
              <button type="button" className="gcard-thumb" onClick={() => setEditing(g)}>
                {g.cover_url ? (
                  <img src={g.cover_url} alt={g.name} loading="lazy" />
                ) : (
                  <span className="gcard-noimg">无图</span>
                )}
                {g.images.length > 1 && <span className="gcard-count">{g.images.length} 张</span>}
              </button>
              <div className="gcard-body">
                <h4 title={g.name}>{g.name}</h4>
                <div className="gcard-tags">
                  {g.tags.length === 0 ? (
                    <span className="tiny">未打标</span>
                  ) : (
                    g.tags.map((t) => (
                      <span className="pill" key={t.value_id} title={t.category_name}>
                        {t.value}
                      </span>
                    ))
                  )}
                </div>
              </div>
              <div className="gcard-actions">
                <button type="button" className="link-btn" onClick={() => setEditing(g)}>
                  编辑
                </button>
                <button type="button" className="link-btn danger" onClick={() => void remove(g)}>
                  删除
                </button>
              </div>
            </article>
          ))}
        </div>
      )}

      {pages > 1 && (
        <div className="pager">
          <button type="button" className="btn btn-sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
            上一页
          </button>
          <span className="tiny">
            第 {page} / {pages} 页
          </span>
          <button
            type="button"
            className="btn btn-sm"
            disabled={page >= pages}
            onClick={() => setPage((p) => p + 1)}
          >
            下一页
          </button>
        </div>
      )}

      {(creating || editing) && (
        <GarmentEditor
          garment={editing}
          categories={categories}
          onClose={() => {
            setCreating(false);
            setEditing(null);
          }}
          onSaved={applySaved}
          onManageTags={() => setManagingTags(true)}
        />
      )}

      {managingTags && (
        <TagManager
          scope="garment"
          onClose={() => setManagingTags(false)}
          onChanged={() => {
            void loadCategories();
            // 类别/取值被删改后，已选中的标签可能已不存在 —— 直接清空筛选更省心
            setActiveTags(new Set());
          }}
        />
      )}
    </div>
  );
}
