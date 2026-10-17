import { useCallback, useEffect, useMemo, useState } from "react";

import { isWornImage, wardrobeApi, type Outfit, type OutfitImage, type TagCategory } from "../api/wardrobe";
import Lightbox from "../components/Lightbox";
import OutfitEditor from "../components/OutfitEditor";
import TagManager from "../components/TagManager";
import Thumb from "../components/Thumb";
import { confirmDialog } from "../lib/confirm";

/** 卡片的图集：搭配图在前、上身图在后（和卡片上的显示顺序一致）。 */
function galleryOf(o: Outfit): OutfitImage[] {
  const canvas = o.images.find((i) => i.kind === "result");
  const worn = o.images.filter((i) => isWornImage(i.kind));
  return [canvas, ...worn].filter((x): x is OutfitImage => Boolean(x));
}

export default function OutfitsPage() {
  const [items, setItems] = useState<Outfit[]>([]);
  const [total, setTotal] = useState(0);
  const [q, setQ] = useState("");
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [categories, setCategories] = useState<TagCategory[]>([]);
  const [editing, setEditing] = useState<Outfit | null | "new">(null);
  const [managingTags, setManagingTags] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 卡片上临时切去看上身图的那几套（不入库，刷新就回默认的搭配图） */
  const [peekWorn, setPeekWorn] = useState<Record<number, boolean>>({});
  const [viewer, setViewer] = useState<{ outfit: Outfit; index: number } | null>(null);

  /**
   * 筛选面板折叠。和衣物页同一套交互：手机上默认收起（一屏能先看到搭配墙），
   * 桌面展开；用户手动切过之后记住选择。
   *
   * key 单独一份 —— 两个页面的筛选栏宽高都不一样，共用一个开关会互相惊喜。
   */
  const [filtersOpen, setFiltersOpen] = useState(() => {
    const saved = localStorage.getItem("wardrobe.filtersOpen.outfit");
    if (saved !== null) return saved === "1";
    return window.innerWidth > 760;
  });

  function toggleFilters() {
    setFiltersOpen((v) => {
      localStorage.setItem("wardrobe.filtersOpen.outfit", v ? "0" : "1");
      return !v;
    });
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await wardrobeApi.listOutfits({
        q: q.trim() || undefined,
        tagValueIds: tagIds.length ? tagIds : undefined,
        pageSize: 60,
      });
      setItems(res.items);
      setTotal(res.total);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, [q, tagIds]);

  const toggleTag = (id: number) =>
    setTagIds((prev) => (prev.includes(id) ? prev.filter((t) => t !== id) : [...prev, id]));

  useEffect(() => {
    const t = setTimeout(load, 200);
    return () => clearTimeout(t);
  }, [load]);

  const reloadCategories = () => {
    wardrobeApi.listCategories("outfit").then(setCategories).catch(() => setCategories([]));
  };

  useEffect(() => {
    reloadCategories();
  }, []);

  // 季节 / 风格 / 色系里，只有季节是预置的；筛选栏全部列出来，空的照常显示
  const filterCats = useMemo(() => categories, [categories]);

  const remove = async (outfit: Outfit) => {
    const ok = await confirmDialog({
      title: "删除这套搭配？",
      message: `「${outfit.name}」下面的图（搭配图、上身图）也会一并删除，此操作无法撤销。`,
      confirmLabel: "删除搭配",
      danger: true,
    });
    if (!ok) return;
    await wardrobeApi.deleteOutfit(outfit.id);
    load();
  };

  return (
    <div className="wardrobe">
      <div className="toolbar">
        <div className="row-end" style={{ gap: 8, flexWrap: "wrap" }}>
          <input
            className="input"
            placeholder="搜搭配名"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            style={{ maxWidth: 220 }}
          />
          <span className="tiny">{total} 套</span>
          <button type="button" className="btn" onClick={() => setManagingTags(true)}>
            管理标签
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}>
            新建搭配
          </button>
        </div>
      </div>

      {categories.length > 0 && (
        <div className="filter-panel">
          <div className="filter-head">
            <button
              type="button"
              className="filter-toggle"
              aria-expanded={filtersOpen}
              title={filtersOpen ? "收起筛选" : "展开筛选"}
              onClick={toggleFilters}
            >
              <span className="filter-title">筛选</span>
              <span className={`filter-chev${filtersOpen ? " open" : ""}`} aria-hidden="true">
                ▾
              </span>
            </button>
            <span className="filter-count">
              {tagIds.length > 0 ? `已选 ${tagIds.length} 项` : "未筛选"}
            </span>
            {tagIds.length > 0 && (
              <button
                type="button"
                className="btn btn-sm filter-clear"
                onClick={() => setTagIds([])}
              >
                清空
              </button>
            )}
          </div>

          {filtersOpen && (
            <>
              {categories.map((cat) => (
                <div className="filter-section" key={cat.id}>
                  <span className="filter-section-name">{cat.name}</span>
                  <div className="chips">
                    {cat.values.map((v) => (
                      <button
                        type="button"
                        key={v.id}
                        className={`chip chip-toggle${tagIds.includes(v.id) ? " on" : ""}`}
                        onClick={() => toggleTag(v.id)}
                      >
                        {v.value}
                      </button>
                    ))}
                    {cat.values.length === 0 && <span className="tiny">（无取值）</span>}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {error && <p className="tiny" style={{ color: "var(--danger)" }}>{error}</p>}

      {loading && items.length === 0 ? (
        <p className="tiny">加载中…</p>
      ) : items.length === 0 ? (
        <div className="card placeholder">
          <h3>还没有搭配</h3>
          <p>
            点「新建搭配」进入画布：从素材库里挑衣服摆上去，拖动摆位、拉手柄缩放旋转、
            在右侧调图层顺序，保存时会生成一张成图。
          </p>
        </div>
      ) : (
        <div className="wall">
          {items.map((o) => {
            const worn = o.images.filter((i) => isWornImage(i.kind));
            // 默认还是搭配图（封面）；有上身图时可以在卡片上切过去瞄一眼
            const peeking = Boolean(peekWorn[o.id]) && worn.length > 0;
            const cover = peeking ? worn[0].thumb_medium_url : o.cover_url;
            const gallery = galleryOf(o);
            return (
              <div className="gcard ocard" key={o.id}>
                {/* 成图已按内容裁过边，用 contain + 画布底色铺满，别再裁第二刀 */}
                <div
                  className={`ocard-cover${peeking ? " worn" : ""}`}
                  style={{ background: peeking ? "#1d1f22" : o.background || "var(--surface-2)" }}
                >
                  <button
                    type="button"
                    className="ocard-open"
                    title="点开看大图"
                    onClick={() =>
                      setViewer({
                        outfit: o,
                        index: Math.max(
                          0,
                          gallery.findIndex((img) => img.thumb_medium_url === cover),
                        ),
                      })
                    }
                  >
                    {cover ? (
                      <Thumb src={cover} alt={o.name} fallback="未生成成图" />
                    ) : (
                      <span className="gcard-noimg">未生成成图</span>
                    )}
                  </button>
                  {worn.length > 0 && (
                    <button
                      type="button"
                      className="ocard-switch"
                      title="在搭配图和上身图之间切换"
                      onClick={() =>
                        setPeekWorn((prev) => ({ ...prev, [o.id]: !prev[o.id] }))
                      }
                    >
                      {peeking ? "看搭配图" : `看上身图${worn.length > 1 ? ` ${worn.length}` : ""}`}
                    </button>
                  )}
                </div>
                <div className="gcard-body">
                  <h4 title={o.name}>{o.name}</h4>
                  <div className="gcard-tags">
                    {o.tags.map((t) => (
                      <span className="pill" key={t.value_id}>
                        {t.value}
                      </span>
                    ))}
                    {o.items.length > 0 && (
                      <span className="pill pill-soft">{o.items.length} 层</span>
                    )}
                  </div>
                </div>
                <div className="gcard-mini">
                  <button type="button" onClick={() => setEditing(o)}>
                    编辑
                  </button>
                  <button type="button" onClick={() => remove(o)}>
                    删除
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {editing !== null && (
        <OutfitEditor
          outfit={editing === "new" ? null : editing}
          categories={filterCats}
          onClose={() => {
            setEditing(null);
            // 关掉也要刷：编辑器里可能传了上身图、甚至「新建搭配」时已经悄悄落过库，
            // 卡片墙不刷就会一直显示旧样子（上身图删光了还挂着「看上身图」）
            load();
          }}
          onSaved={() => {
            setEditing(null);
            // 编辑器里可能就地补过标签取值，刷新一遍筛选栏才看得到新值
            reloadCategories();
            load();
          }}
        />
      )}

      {viewer && (
        <Lightbox
          items={galleryOf(viewer.outfit).map((img) => ({
            key: `oi-${img.id}`,
            url: img.original_url,
            label: img.kind === "result" ? "搭配图" : "上身图",
            caption: `${img.width}×${img.height}`,
          }))}
          index={viewer.index}
          onClose={() => setViewer(null)}
          onIndexChange={(index) => setViewer((cur) => (cur ? { ...cur, index } : cur))}
        />
      )}

      {managingTags && (
        <TagManager
          scope="outfit"
          onClose={() => setManagingTags(false)}
          onChanged={() => {
            reloadCategories();
            load();
          }}
        />
      )}
    </div>
  );
}
