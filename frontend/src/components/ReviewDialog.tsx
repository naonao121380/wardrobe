/**
 * 复核弹窗（批次 3）：原图 / 抠图 / 候选标签 三栏。
 *
 * 设计取舍：
 * - **候选标签默认全选，但可改**。AI 只是给个起点，最终以用户为准 —— 不替用户做决定。
 * - 抠图**不自动替换封面**。抠坏了要能退回原图，封面由用户点一下决定。
 * - **「用抠图做封面」是复核里的一步，不是终点** —— 设完封面本弹窗**不关**，
 *   按钮变「已是封面」，接着改名字/标签，最后点「采纳并入库」。早期这里接的是 `onDone`，
 *   一点弹窗就没了：名字/标签没被采纳、状态仍是「待复核」，卡片上还挂着「复核 AI 结果」，
 *   再点又回到同一个 AI 建议页 —— 看着就像「点了封面却被退回 AI 建议」。
 *   衣物编辑弹窗里的「设封面」也是不关窗的，两处保持一致。
 * - 只有一个主按钮。界面上的名字/标签默认就是 AI 的建议值，所以「直接确认」等价于
 *   原来的「全盘采纳」；改过之后同一个按钮提交的就是你的修改。改乱了可以「恢复 AI 建议」。
 */

import { useMemo, useState } from "react";

import { ApiError } from "../api/client";
import {
  wardrobeApi,
  type Garment,
  type GarmentImage,
  type TagCategory,
} from "../api/wardrobe";
import { toggleCascaded } from "../lib/tags";
import { confidenceText, providerLabel, statusMeta } from "../lib/pipeline";
import CutoutEditor from "./CutoutEditor";
import Dialog from "./Dialog";
import Lightbox from "./Lightbox";
import { TagPickRow } from "./GarmentEditor";
import Thumb from "./Thumb";

type Props = {
  garment: Garment;
  categories: TagCategory[];
  onClose: () => void;
  /** 复核完成 / 重新入队后，把最新衣物交回列表。 */
  onDone: (g: Garment) => void;
  /**
   * 中途更新（修整抠图保存后）：只把最新衣物同步给列表，**不关闭本弹窗**。
   * 修整抠图是复核流程里的一步，保存完要回到这里继续看名字和标签，
   * 不能顺手把复核也关了 —— 那样等于打断流程。
   */
  onUpdated?: (g: Garment) => void;
};

export default function ReviewDialog({ garment, categories, onClose, onDone, onUpdated }: Props) {
  const [current, setCurrent] = useState<Garment>(garment);
  const [name, setName] = useState(garment.ai_suggestion?.name || garment.name);
  const [selected, setSelected] = useState<Set<number>>(
    () =>
      new Set(
        garment.ai_suggestion && garment.ai_suggestion.tag_value_ids.length > 0
          ? garment.ai_suggestion.tag_value_ids
          : garment.tag_value_ids,
      ),
  );
  const [showAll, setShowAll] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  /** 「修整抠图保存完」这类非错误的提示：回来后告诉用户刚才那步生效了。 */
  const [notice, setNotice] = useState<string | null>(null);
  /** 大图查看：gallery 的下标，null = 关闭 */
  const [zoom, setZoom] = useState<number | null>(null);
  /** 抠图精修 */
  const [editing, setEditing] = useState(false);

  const suggestion = current.ai_suggestion;
  const images = current.images;
  const original: GarmentImage | undefined = useMemo(
    () => images.find((i) => i.kind === "original") ?? images[0],
    [images],
  );
  const cutout = useMemo(() => images.find((i) => i.kind === "cutout"), [images]);

  /** 大图查看的画廊：原图 + 抠图，点缩略图即可放大对比。 */
  const gallery = useMemo(() => {
    const list: Parameters<typeof Lightbox>[0]["items"] = [];
    if (original) {
      list.push({
        key: String(original.id),
        url: original.original_url,
        label: "原图",
        caption: `${original.width}×${original.height}`,
      });
    }
    if (cutout) {
      list.push({
        key: String(cutout.id),
        url: cutout.original_url,
        label: "抠图结果",
        caption: `${cutout.width}×${cutout.height}`,
        transparent: true,
      });
    }
    return list;
  }, [original, cutout]);

  const meta = statusMeta(current.status);
  const confidence = confidenceText(suggestion);

  /** 界面初始值 = AI 建议（没有建议就沿用衣物现值）——「没改过」时确认即等于全盘采纳。 */
  const baseName = garment.ai_suggestion?.name || garment.name;
  const baseTags = useMemo(
    () =>
      new Set(
        garment.ai_suggestion && garment.ai_suggestion.tag_value_ids.length > 0
          ? garment.ai_suggestion.tag_value_ids
          : garment.tag_value_ids,
      ),
    [garment],
  );
  const dirty = useMemo(() => {
    if (name.trim() !== baseName) return true;
    if (selected.size !== baseTags.size) return true;
    for (const id of selected) if (!baseTags.has(id)) return true;
    return false;
  }, [name, selected, baseName, baseTags]);

  function resetToSuggestion() {
    setName(baseName);
    setSelected(new Set(baseTags));
  }

  async function run(fn: () => Promise<Garment>, done: (g: Garment) => void) {
    setBusy(true);
    setErr(null);
    setNotice(null);
    try {
      const updated = await fn();
      setCurrent(updated);
      done(updated);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  function toggle(valueId: number) {
    setSelected((prev) => toggleCascaded(prev, valueId, categories));
  }

  const hasSuggestionTags = (suggestion?.tags.length ?? 0) > 0;

  return (
    <Dialog
      title={`复核：${current.name}`}
      onClose={onClose}
      width={980}
      footer={
        <div className="review-foot">
          <div className="review-foot-meta">
            <span className="tiny">
              来源：{providerLabel(suggestion?.provider)}
              {confidence ? ` · 置信度 ${confidence}` : ""}
            </span>
            <span className="tiny">
              {dirty ? "已按你的修改调整，将覆盖 AI 建议" : "当前就是 AI 建议，直接确认即可"}
            </span>
          </div>
          <div className="review-foot-actions">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              稍后再说
            </button>
            <button
              type="button"
              className="btn"
              disabled={busy}
              title="丢弃这次结果，让 Worker 重跑一遍"
              onClick={() => void run(() => wardrobeApi.processGarment(current.id), onDone)}
            >
              重新处理
            </button>
            {dirty && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                title="把名称和标签恢复到 AI 给出的值"
                onClick={resetToSuggestion}
              >
                恢复 AI 建议
              </button>
            )}
            <button
              type="button"
              className="btn btn-primary btn-lg review-confirm"
              disabled={busy || !name.trim()}
              onClick={() =>
                void run(
                  () =>
                    wardrobeApi.reviewGarment(current.id, {
                      name: name.trim(),
                      tag_value_ids: [...selected],
                    }),
                  onDone,
                )
              }
            >
              {busy ? "提交中…" : dirty ? "保存修改并入库" : "采纳并入库"}
            </button>
          </div>
        </div>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}
      {notice && !err && <div className="alert alert-warn">{notice}</div>}

      {current.status === "failed" && (
        <div className="alert alert-error">
          处理失败：{suggestion?.error ?? "（没有留下原因）"} —— 可以点「重新处理」再试一次。
        </div>
      )}
      {suggestion?.error && current.status !== "failed" && (
        <div className="alert alert-warn">部分步骤没跑成：{suggestion.error}</div>
      )}

      <div className="review-grid">
        <section className="review-col">
          <h4>原图</h4>
          {original ? (
            <button
              type="button"
              className="imgcell-open"
              title="点击查看大图"
              onClick={() => setZoom(0)}
            >
              <Thumb src={original.thumb_medium_url} alt={current.name} fallback="原图丢失" />
            </button>
          ) : (
            <div className="empty">没有原图</div>
          )}
        </section>

        <section className="review-col">
          <h4>抠图结果</h4>
          {cutout ? (
            <>
              <button
                type="button"
                className="imgcell-open"
                title="点击查看大图"
                onClick={() =>
                  setZoom(gallery.findIndex((g) => g.key === String(cutout.id)))
                }
              >
                <span className="cutout-stage" style={{ display: "block" }}>
                  <Thumb src={cutout.thumb_medium_url} alt="抠图" fallback="抠图丢失" />
                </span>
              </button>
              <div className="row-end">
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busy}
                  title="手工擦掉残留 / 补回被误删的部分，或框一块区域让模型重抠"
                  onClick={() => setEditing(true)}
                >
                  修整抠图
                </button>
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busy || cutout.sort_order === 0}
                  title="把这张抠图排到最前（卡片默认显示它）。复核不会因此结束，改完名字和标签再点「采纳并入库」。"
                  onClick={() =>
                    void run(
                      () => wardrobeApi.setPrimaryImage(current.id, cutout.id),
                      (g) => {
                        // ★ 只同步给列表，**不关本弹窗**（走 onUpdated，不走 onDone）。
                        //   设封面是复核流程里的**一步**，不是终点：早期这里接的是 onDone，
                        //   一点弹窗就没了，名字/标签还没采纳、状态仍是「待复核」——
                        //   于是卡片上还挂着「复核 AI 结果」，再点又回到同一个 AI 建议页。
                        //   这与衣物编辑弹窗里的「设封面」（imageAction，不关窗）也保持一致。
                        onUpdated?.(g);
                        setNotice("已把抠图设为封面 —— 改好名字和标签后点「采纳并入库」完成复核。");
                      },
                    )
                  }
                >
                  {cutout.sort_order === 0 ? "已是封面" : "用抠图做封面"}
                </button>
              </div>
            </>
          ) : (
            <div className="empty">
              {meta.hint || "这一轮没有产出抠图"}
              <p className="tiny">没抠图也能直接确认，衣橱保留原图。</p>
            </div>
          )}
        </section>

        <section className="review-col review-col-tags">
          <h4>名称</h4>
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="给这件衣物起个名" />

          <h4 className="mt">AI 候选标签</h4>
          {hasSuggestionTags ? (
            <div className="chips">
              {suggestion!.tags.map((t) => (
                <button
                  type="button"
                  key={t.value_id}
                  className={`chip chip-toggle${selected.has(t.value_id) ? " on" : ""}`}
                  title={`${t.category_name}`}
                  onClick={() => toggle(t.value_id)}
                >
                  {t.value}
                </button>
              ))}
            </div>
          ) : (
            <p className="tiny">这一轮没给出标签建议，可以自己在下面补。</p>
          )}

          <button
            type="button"
            className="link-btn mt"
            onClick={() => setShowAll((v) => !v)}
          >
            {showAll ? "收起全部标签" : "展开全部标签补选"}
          </button>
          {showAll && (
            <div className="tagpick mt">
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
        </section>
      </div>

      {zoom !== null && zoom < gallery.length && (
        <Lightbox items={gallery} index={zoom} onClose={() => setZoom(null)} onIndexChange={setZoom} />
      )}

      {editing && cutout && (
        <CutoutEditor
          garment={current}
          image={cutout}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            // 只更新内容，不关本弹窗：修完整张图还要回来接着看名字 / 标签。
            setCurrent(updated);
            onUpdated?.(updated);
            setNotice("抠图已更新 —— 继续调整，或直接确认入库。");
          }}
        />
      )}
    </Dialog>
  );
}
