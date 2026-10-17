import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

import { ApiError } from "../api/client";
import {
  wardrobeApi,
  type BatchUploadResult,
  type Garment,
  type TagCategory,
} from "../api/wardrobe";
import BatchTagDialog from "../components/BatchTagDialog";
import BatchUploadDialog from "../components/BatchUploadDialog";
import ConfirmDeleteDialog, { type DeleteSummary } from "../components/ConfirmDeleteDialog";
import GarmentEditor from "../components/GarmentEditor";
import Lightbox from "../components/Lightbox";
import ReviewDialog from "../components/ReviewDialog";
import TagManager from "../components/TagManager";
import Thumb from "../components/Thumb";
import { buildTagGroups, type FacetGroups } from "../lib/tags";
import { ATTENTION_STATUSES, needsAttention, statusMeta } from "../lib/pipeline";

const PAGE_SIZE = 48;
/**
 * 批量任务跑起来之后，只轮询**队列计数**（不刷列表）。
 *
 * 列表刷新会重置滚动位置、闪一下整墙卡片 —— 一直刷很难受。这里只盯着
 * pending + processing 归零，归零了再刷一次列表。
 */
const JOB_POLL_MS = 2500;
/** 跟踪上限：Worker 没开或卡死时不至于无限轮询。 */
const JOB_TIMEOUT_MS = 20 * 60 * 1000;

/**
 * 分类维度的一个分支。
 *
 * `majorId` 是大类取值 id；`null` 表示「未归类」那一档 —— 有些小类取值还没挂到
 * 任何大类下，它们单独成一组，查询时只带小类自己。
 * `minors` 是这个大类下选中的小类取值；空集合 = 只按大类筛。
 */
type Branch = { majorId: number | null; minors: Set<number> };

export default function WardrobePage() {
  const [categories, setCategories] = useState<TagCategory[]>([]);
  const [items, setItems] = useState<Garment[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);

  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");

  // 筛选状态是按「维度」分开存的，不是一锅 Set —— 维度内 OR、维度间 AND 的语义
  // 只有结构化之后才表达得清楚。
  const [branches, setBranches] = useState<Branch[]>([]);
  const [facetSel, setFacetSel] = useState<Map<number, Set<number>>>(new Map());
  const [multi, setMulti] = useState(false);

  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [editing, setEditing] = useState<Garment | null>(null);
  const [creating, setCreating] = useState(false);
  const [managingTags, setManagingTags] = useState(false);
  const [reviewing, setReviewing] = useState<Garment | null>(null);
  const [bulkUploading, setBulkUploading] = useState(false);
  /** 批量打标：非 null 时打开标签选择弹窗。 */
  const [bulkTagging, setBulkTagging] = useState(false);

  /**
   * 只看「还需要人管」的：待复核 / 失败 / 排队 / 处理中。
   * 状态落在 URL 上（`?attention=1`）—— 概览页的「待处理」卡片直接链过来，
   * 刷新 / 分享链接也能保持这个筛选。
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const attentionOnly = searchParams.get("attention") === "1";
  const [tick, setTick] = useState(0);
  /** 筛选面板折叠：手机上默认收起，一屏能直接看到卡片墙；记住用户的选择。 */
  const [filtersOpen, setFiltersOpen] = useState(() => {
    const saved = localStorage.getItem("wardrobe.filtersOpen");
    if (saved !== null) return saved === "1";
    return window.innerWidth > 760;
  });

  function toggleFilters() {
    setFiltersOpen((v) => {
      localStorage.setItem("wardrobe.filtersOpen", v ? "0" : "1");
      return !v;
    });
  }

  /** 批量选择模式：勾中要交给 AI 的衣物。 */
  const [selectMode, setSelectMode] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  /**
   * 正在跟踪的批量任务。非 null 时才会轮询队列计数 —— 任务跑完或用户点了
   * 「后台运行」就置空，页面立刻安静下来。
   */
  const [job, setJob] = useState<{ total: number; pending: number; processing: number } | null>(
    null,
  );
  const [toast, setToast] = useState<string | null>(null);
  /** 待删除的衣物（非 null 时弹确认框，里面会先查有哪些搭配引用）。 */
  const [deleting, setDeleting] = useState<Garment[] | null>(null);
  /** 点缩略图看大图：非 null 时打开图片查看器。 */
  const [viewing, setViewing] = useState<{ garment: Garment; index: number } | null>(null);

  const reqId = useRef(0);
  const jobStartedAt = useRef(0);

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

  /** 大类 / 小类这一对（靠 parent_category_id 认，不猜名字）。 */
  const classify = useMemo(() => {
    const minor = categories.find((c) => c.parent_category_id != null) ?? null;
    const major =
      minor !== null
        ? categories.find((c) => c.id === minor.parent_category_id) ?? null
        : null;
    return { major, minor };
  }, [categories]);

  /** 除「分类」以外的独立维度：颜色 / 材质… */
  const otherCats = useMemo(() => {
    const skip = new Set<number>();
    if (classify.major) skip.add(classify.major.id);
    if (classify.minor) skip.add(classify.minor.id);
    return categories.filter((c) => c.values.length > 0 && !skip.has(c.id));
  }, [categories, classify]);

  /** 分类维度里，每个大类下的小类取值；`orphans` 是还没挂父值的那一组。 */
  const minorGroups = useMemo(() => {
    const byParent = new Map<number, number[]>();
    const orphans: number[] = [];
    for (const v of classify.minor?.values ?? []) {
      if (v.parent_value_id === null) {
        orphans.push(v.id);
      } else {
        byParent.set(v.parent_value_id, [...(byParent.get(v.parent_value_id) ?? []), v.id]);
      }
    }
    return { byParent, orphans };
  }, [classify]);

  /** 摊平成后端 tag_groups：维度内 OR、维度间 AND。 */
  const tagGroups = useMemo(() => {
    const classifyFacet: FacetGroups = [];
    for (const b of branches) {
      if (b.majorId === null) {
        for (const m of b.minors) classifyFacet.push([m]);
        continue;
      }
      if (b.minors.size === 0) classifyFacet.push([b.majorId]);
      else for (const m of b.minors) classifyFacet.push([b.majorId, m]);
    }
    const facets: FacetGroups[] = [classifyFacet];
    for (const set of facetSel.values()) {
      if (set.size > 0) facets.push([...set].map((id) => [id]));
    }
    return buildTagGroups(facets);
  }, [branches, facetSel]);

  const selectedCount = useMemo(() => {
    let n = 0;
    for (const b of branches) n += (b.majorId === null ? 0 : 1) + b.minors.size;
    for (const s of facetSel.values()) n += s.size;
    return n;
  }, [branches, facetSel]);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    wardrobeApi
      .listGarments({
        q: debouncedQuery || undefined,
        tagGroups,
        statuses: attentionOnly ? ATTENTION_STATUSES : undefined,
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
  }, [debouncedQuery, tagGroups, page, attentionOnly, tick]);

  /** 提示条自动消失 */
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 5000);
    return () => clearTimeout(t);
  }, [toast]);

  // ---------- 批量任务跟踪（异步，不刷列表）----------
  //
  // Worker 是独立进程，跑完不会通知前端。这里只轮询队列计数，归零了才刷一次列表。
  const jobActive = job !== null;
  useEffect(() => {
    if (!jobActive) return;
    let cancelled = false;

    async function probe() {
      if (Date.now() - jobStartedAt.current > JOB_TIMEOUT_MS) {
        if (!cancelled) {
          setJob(null);
          setToast("跟踪超时了 —— Worker 可能没启动或卡住了，可手动点刷新确认");
        }
        return;
      }
      try {
        const s = await wardrobeApi.queueStatus();
        if (cancelled) return;
        const running = s.pending + s.processing;
        setJob((prev) => (prev ? { ...prev, pending: s.pending, processing: s.processing } : prev));
        if (running === 0) {
          setJob(null);
          setTick((v) => v + 1); // 只在真正跑完时刷一次
          setToast(
            s.failed > 0
              ? `处理完成：${s.needs_review} 件待复核，${s.failed} 件失败`
              : `处理完成：${s.needs_review} 件待复核`,
          );
        }
      } catch {
        if (!cancelled) {
          setJob(null);
          setToast("查询队列状态失败，已停止跟踪");
        }
      }
    }

    void probe();
    const timer = setInterval(() => void probe(), JOB_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [jobActive]);

  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const filtering = tagGroups.length > 0 || debouncedQuery.trim().length > 0;

  // ---------- 分类维度 ----------

  function toggleMajor(majorId: number) {
    setBranches((prev) => {
      if (prev.some((b) => b.majorId === majorId)) {
        return prev.filter((b) => b.majorId !== majorId);
      }
      // 大类本身**总是**可以叠加（不同大类之间是或逻辑），不受单选开关限制。
      // 单选开关管的是「同一个大类下能选几个小类」。
      return [...prev, { majorId, minors: new Set<number>() }];
    });
    setPage(1);
  }

  function toggleMinor(majorId: number | null, minorId: number) {
    setBranches((prev) => {
      const existing = prev.find((b) => b.majorId === majorId);
      if (!existing) return [...prev, { majorId, minors: new Set([minorId]) }];
      const minors = new Set(existing.minors);
      if (minors.has(minorId)) {
        minors.delete(minorId);
      } else if (multi) {
        minors.add(minorId);
      } else {
        minors.clear();
        minors.add(minorId);
      }
      return prev.map((b) => (b.majorId === majorId ? { majorId, minors } : b));
    });
    setPage(1);
  }

  // ---------- 其它维度（颜色 / 材质…）----------

  function toggleFacet(categoryId: number, valueId: number) {
    setFacetSel((prev) => {
      const next = new Map(prev);
      const cur = new Set(next.get(categoryId) ?? []);
      if (cur.has(valueId)) {
        cur.delete(valueId);
      } else if (multi) {
        cur.add(valueId);
      } else {
        cur.clear();
        cur.add(valueId);
      }
      if (cur.size > 0) next.set(categoryId, cur);
      else next.delete(categoryId);
      return next;
    });
    setPage(1);
  }

  function changeMulti(next: boolean) {
    setMulti(next);
    if (next) return;
    // 切回单选：分类只留第一个分支（且最多一个小类），其它维度各只留一个
    setBranches((prev) => {
      const first = prev[0];
      if (!first) return prev;
      return [{ majorId: first.majorId, minors: new Set([...first.minors].slice(0, 1)) }];
    });
    setFacetSel((prev) => {
      const pruned = new Map<number, Set<number>>();
      for (const [catId, set] of prev) {
        const first = [...set][0];
        if (first !== undefined) pruned.set(catId, new Set([first]));
      }
      return pruned;
    });
    setPage(1);
  }

  function clearFilters() {
    setBranches([]);
    setFacetSel(new Map());
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

  /** 打开删除确认框 —— 真正的删除动作在 ConfirmDeleteDialog 里（它要先查搭配引用）。 */
  function remove(g: Garment) {
    setDeleting([g]);
  }

  /** 删除完成：更新列表、汇总一句人话。 */
  function onDeleted(summary: DeleteSummary) {
    const gone = new Set(summary.deletedIds);
    setItems((prev) => prev.filter((g) => !gone.has(g.id)));
    setTotal((t) => Math.max(0, t - summary.deletedIds.length));
    setSelected(new Set());
    setDeleting(null);

    const parts = [`已删除 ${summary.deletedIds.length} 件（${summary.deletedImages} 张图片）`];
    if (summary.deletedOutfits > 0) {
      parts.push(`连同 ${summary.deletedOutfits} 套搭配`);
    } else if (summary.orphanedLayers > 0) {
      parts.push(`${summary.affected.length} 套搭配里 ${summary.orphanedLayers} 层变成空位`);
    }
    if (summary.skipped.length > 0) parts.push(`跳过 ${summary.skipped.length} 件`);
    setToast(parts.join("，"));
  }

  // ---------- 批量选择 ----------

  function toggleSelect(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function exitSelectMode() {
    setSelectMode(false);
    setSelected(new Set());
  }

  const pageIds = useMemo(() => items.map((g) => g.id), [items]);
  const pageAllSelected = pageIds.length > 0 && pageIds.every((id) => selected.has(id));

  function toggleSelectPage() {
    setSelected((prev) => {
      const next = new Set(prev);
      if (pageAllSelected) for (const id of pageIds) next.delete(id);
      else for (const id of pageIds) next.add(id);
      return next;
    });
  }

  /** 一键选出所有还没抠图打标的衣物（一张 cutout 都没有的）。 */
  async function selectUnprocessed() {
    setBulkBusy(true);
    try {
      const data = await wardrobeApi.listGarments({ unprocessed: true, pageSize: 200 });
      const ids = data.items.map((g) => g.id);
      setSelected(new Set(ids));
      if (ids.length === 0) setToast("没有还没抠图打标的衣物");
      else if (data.total > ids.length)
        setToast(`已选出 ${ids.length} 件（共 ${data.total} 件未处理，一次最多选 200 件）`);
      else setToast(`已选出 ${ids.length} 件还没抠图打标的衣物`);
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "查询失败");
    } finally {
      setBulkBusy(false);
    }
  }

  /** 把选中的衣物一次性交给 Worker。入队后转为后台跟踪，不阻塞操作。 */
  async function runBatch() {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBulkBusy(true);
    try {
      const res = await wardrobeApi.batchProcess(ids);
      if (res.enqueued.length === 0) {
        setToast(res.skipped[0]?.reason ?? "没有可处理的衣物");
        return;
      }
      jobStartedAt.current = Date.now();
      setJob({
        total: res.enqueued.length,
        pending: res.enqueued.length,
        processing: 0,
      });
      setTick((v) => v + 1); // 先把卡片上的「排队中」刷出来
      if (res.skipped.length > 0) {
        setToast(
          `已入队 ${res.enqueued.length} 件，跳过 ${res.skipped.length} 件（${res.skipped[0].reason}）`,
        );
      }
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "批量入队失败");
    } finally {
      setBulkBusy(false);
    }
  }

  /** 批量删除选中的衣物（连带图片文件）—— 交给确认框统一处理。 */
  function removeSelected() {
    if (selected.size === 0) return;
    const targets = items.filter((g) => selected.has(g.id));
    if (targets.length === 0) return;
    setDeleting(targets);
  }

  /** 批量打标：把选中的标签应用到已勾选的衣物上。 */
  async function applyBulkTags(valueIds: number[], mode: "add" | "remove" | "replace") {
    const ids = [...selected];
    if (ids.length === 0) return;
    setBulkBusy(true);
    try {
      const res = await wardrobeApi.batchTags(ids, valueIds, mode);
      setBulkTagging(false);
      setTick((v) => v + 1); // 标签变了，刷一次列表才看得到
      setToast(
        `已${mode === "remove" ? "移除" : "更新"} ${res.updated.length} 件衣物的标签` +
          (res.skipped.length ? `，跳过 ${res.skipped.length} 件` : ""),
      );
    } catch (e) {
      setToast(e instanceof ApiError ? e.message : "批量打标失败");
    } finally {
      setBulkBusy(false);
    }
  }

  /** 批量上传完成：刷新列表，并且（若勾了）开始跟踪 AI 任务。 */
  function onBulkUploaded(res: BatchUploadResult, enqueued: boolean) {
    setBulkUploading(false);
    setTick((v) => v + 1);
    const skipped =
      res.skipped.length > 0 ? `，跳过 ${res.skipped.length} 个（${res.skipped[0].reason}）` : "";
    if (enqueued && res.created.length > 0) {
      jobStartedAt.current = Date.now();
      setJob({ total: res.created.length, pending: res.created.length, processing: 0 });
      setToast(`已上传 ${res.created.length} 件，AI 正在处理${skipped}`);
    } else {
      setToast(`已上传 ${res.created.length} 件${skipped}`);
    }
  }

  const minorById = useMemo(() => {
    const m = new Map<number, string>();
    for (const v of classify.minor?.values ?? []) m.set(v.id, v.value);
    return m;
  }, [classify]);

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
          <button
            type="button"
            className={`btn btn-sm${attentionOnly ? " btn-on" : ""}`}
            title="只看待复核 / 失败 / 处理中的衣物"
            onClick={() => {
              setSearchParams(
                (prev) => {
                  const next = new URLSearchParams(prev);
                  if (attentionOnly) next.delete("attention");
                  else next.set("attention", "1");
                  return next;
                },
                { replace: true },
              );
              setPage(1);
            }}
          >
            待复核
          </button>
          <button
            type="button"
            className={`btn btn-sm${selectMode ? " btn-on" : ""}`}
            title="勾选多件衣物，一次性交给 AI 抠图打标"
            onClick={() => (selectMode ? exitSelectMode() : setSelectMode(true))}
          >
            {selectMode ? "退出批量" : "批量"}
          </button>
          <button
            type="button"
            className="btn btn-sm"
            title="Worker 是独立进程，处理中不会主动通知前端"
            onClick={() => setTick((v) => v + 1)}
          >
            刷新
          </button>
          <button type="button" className="btn btn-sm" onClick={() => setManagingTags(true)}>
            管理标签
          </button>
          <button
            type="button"
            className="btn btn-sm"
            title="一次选多张照片，一张建一件衣物"
            onClick={() => setBulkUploading(true)}
          >
            批量上传
          </button>
          <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}>
            + 添加衣物
          </button>
        </div>
      </div>

      {err && <div className="alert alert-error">{err}</div>}

      {(classify.major || otherCats.length > 0) && (
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
            <label className="switch" title="默认单选：同一组里换一个会替换上一个；勾选后可叠加">
              <input type="checkbox" checked={multi} onChange={(e) => changeMulti(e.target.checked)} />
              <span className="switch-box" aria-hidden="true" />
              <span>多选</span>
            </label>
            <span className="filter-count">
              {selectedCount > 0 ? `已选 ${selectedCount} 项` : "未筛选"}
            </span>
            {filtering && (
              <button type="button" className="btn btn-sm filter-clear" onClick={clearFilters}>
                清空
              </button>
            )}
          </div>

          {filtersOpen && (
            <>
              {classify.major && (
                <div className="filter-section">
              <span className="filter-section-name">分类</span>
              <div className="filter-branches">
                {classify.major.values.map((majorValue) => {
                  const branch = branches.find((b) => b.majorId === majorValue.id);
                  const minors = minorGroups.byParent.get(majorValue.id) ?? [];
                  return (
                    <div
                      className={`filter-branch${branch ? " on" : ""}`}
                      key={majorValue.id}
                    >
                      <button
                        type="button"
                        className={`chip chip-major${branch ? " on" : ""}`}
                        onClick={() => toggleMajor(majorValue.id)}
                      >
                        {majorValue.value}
                      </button>
                      {minors.length > 0 && (
                        <div className="filter-branch-minors">
                          {minors.map((id) => (
                            <button
                              type="button"
                              key={id}
                              className={`chip chip-minor${branch?.minors.has(id) ? " on" : ""}`}
                              onClick={() => toggleMinor(majorValue.id, id)}
                            >
                              {minorById.get(id)}
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
                {minorGroups.orphans.length > 0 && (
                  <div
                    className={`filter-branch${
                      branches.some((b) => b.majorId === null) ? " on" : ""
                    }`}
                  >
                    <span className="filter-branch-label">未归类</span>
                    <div className="filter-branch-minors">
                      {minorGroups.orphans.map((id) => (
                        <button
                          type="button"
                          key={id}
                          className={`chip chip-minor${
                            branches.find((b) => b.majorId === null)?.minors.has(id) ? " on" : ""
                          }`}
                          onClick={() => toggleMinor(null, id)}
                        >
                          {minorById.get(id)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {otherCats.map((cat) => {
            const selected = facetSel.get(cat.id) ?? new Set<number>();
            return (
              <div className="filter-section" key={cat.id}>
                <span className="filter-section-name">{cat.name}</span>
                <div className="chips">
                  {cat.values.map((v) => (
                    <button
                      type="button"
                      key={v.id}
                      className={`chip chip-toggle${selected.has(v.id) ? " on" : ""}`}
                      onClick={() => toggleFacet(cat.id, v.id)}
                    >
                      {v.value}
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
            </>
          )}
        </div>
      )}

      {selectMode && (
        <div className="bulkbar">
          <label className="bulk-check">
            <input type="checkbox" checked={pageAllSelected} onChange={toggleSelectPage} />
            全选本页
          </label>
          <button
            type="button"
            className="btn btn-sm"
            disabled={bulkBusy}
            title="把还没有抠图结果的衣物全部勾上（可能包含其他页的）"
            onClick={() => void selectUnprocessed()}
          >
            选出未抠图打标
          </button>
          <button
            type="button"
            className="btn btn-sm"
            disabled={bulkBusy || selected.size === 0}
            title="给选中的衣物批量加 / 删 / 覆盖标签"
            onClick={() => setBulkTagging(true)}
          >
            加标签
          </button>
          <span className="tiny">已选 {selected.size} 件</span>
          <div className="row-end">
            <button
              type="button"
              className="btn btn-sm"
              disabled={bulkBusy || selected.size === 0}
              onClick={() => setSelected(new Set())}
            >
              清空
            </button>
            <button
              type="button"
              className="btn btn-sm btn-danger"
              disabled={bulkBusy || selected.size === 0}
              title="删除选中的衣物和它们的图片"
              onClick={() => removeSelected()}
            >
              删除选中
            </button>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              disabled={bulkBusy || selected.size === 0}
              title="交给后台 Worker 抠图 + 打标，完成后自动刷新一次"
              onClick={() => void runBatch()}
            >
              {bulkBusy ? "提交中…" : `一键抠图打标（${selected.size}）`}
            </button>
            <button type="button" className="btn btn-sm" onClick={exitSelectMode}>
              退出
            </button>
          </div>
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
            <article
              className={`gcard${selected.has(g.id) ? " selected" : ""}${
                needsAttention(g.status) ? " gcard-attn" : ""
              }`}
              key={g.id}
            >
              {selectMode && (
                <button
                  type="button"
                  className={`gcard-check${selected.has(g.id) ? " on" : ""}`}
                  aria-label={selected.has(g.id) ? "取消选择" : "选择这件"}
                  onClick={() => toggleSelect(g.id)}
                >
                  ✓
                </button>
              )}
              <button
                type="button"
                className="gcard-thumb"
                onClick={() => (selectMode ? toggleSelect(g.id) : setEditing(g))}
              >
                {g.cover_url ? (
                  <Thumb src={g.cover_url} alt={g.name} fallback="图片丢失" />
                ) : (
                  <span className="gcard-noimg">无图</span>
                )}
                {g.images.length > 1 && <span className="gcard-count">{g.images.length} 张</span>}
                {g.status !== "ready" && (
                  <span
                    className={`badge badge-${statusMeta(g.status).tone}`}
                    title={statusMeta(g.status).hint}
                  >
                    {statusMeta(g.status).label}
                  </span>
                )}
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
                {needsAttention(g.status) && (
                  <button
                    type="button"
                    className="btn btn-primary btn-block gcard-cta"
                    title={statusMeta(g.status).hint || "AI 出了结果，等你确认"}
                    onClick={() => setReviewing(g)}
                  >
                    {g.status === "failed" ? "查看失败原因" : "复核 AI 结果"}
                  </button>
                )}
                <div className="gcard-mini">
                  <button
                    type="button"
                    className="link-btn"
                    title="看原图 / 抠图的大图"
                    disabled={g.images.length === 0}
                    onClick={() =>
                      setViewing({
                        garment: g,
                        index: Math.max(
                          0,
                          g.images.findIndex((i) => i.kind === "cutout"),
                        ),
                      })
                    }
                  >
                    看大图
                  </button>
                  <button type="button" className="link-btn" onClick={() => setEditing(g)}>
                    编辑
                  </button>
                  <button type="button" className="link-btn danger" onClick={() => remove(g)}>
                    删除
                  </button>
                </div>
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

      {bulkTagging && (
        <BatchTagDialog
          count={selected.size}
          categories={categories}
          onClose={() => setBulkTagging(false)}
          onApply={applyBulkTags}
        />
      )}

      {bulkUploading && (
        <BatchUploadDialog
          onClose={() => setBulkUploading(false)}
          onDone={onBulkUploaded}
        />
      )}

      {reviewing && (
        <ReviewDialog
          garment={reviewing}
          categories={categories}
          onClose={() => setReviewing(null)}
          onDone={(g) => {
            applySaved(g, false);
            setReviewing(null);
          }}
          onUpdated={(g) => applySaved(g, false)}
        />
      )}

      {job && (
        <div className="jobbar" role="status">
          <span className="jobbar-dot" aria-hidden="true" />
          <span>
            AI 处理中：{job.pending} 件排队 · {job.processing} 件处理中
            {job.total > 0 && ` · 本批共 ${job.total} 件`}
          </span>
          <button type="button" className="link-btn" onClick={() => setJob(null)}>
            后台运行，不再跟踪
          </button>
        </div>
      )}

      {toast && <div className="toast">{toast}</div>}

      {managingTags && (
        <TagManager
          scope="garment"
          onClose={() => setManagingTags(false)}
          onChanged={() => {
            void loadCategories();
            // 类别/取值被删改后，已选中的标签可能已不存在 —— 直接清空筛选更省心
            setBranches([]);
            setFacetSel(new Map());
          }}
        />
      )}

      {deleting && (
        <ConfirmDeleteDialog
          targets={deleting}
          onClose={() => setDeleting(null)}
          onDone={onDeleted}
        />
      )}

      {viewing && viewing.garment.images.length > 0 && (
        <Lightbox
          items={viewing.garment.images.map((img) => ({
            key: `gi-${img.id}`,
            url: img.original_url,
            label: img.kind === "cutout" ? "抠图" : "原图",
            caption: `${img.width}×${img.height}`,
            transparent: img.kind === "cutout",
          }))}
          index={viewing.index}
          onClose={() => setViewing(null)}
          onIndexChange={(index) => setViewing((cur) => (cur ? { ...cur, index } : cur))}
        />
      )}
    </div>
  );
}
