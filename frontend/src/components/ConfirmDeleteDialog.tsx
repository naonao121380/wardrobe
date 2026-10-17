import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import {
  wardrobeApi,
  type AffectedOutfit,
  type Garment,
  type OutfitRef,
} from "../api/wardrobe";
import Dialog from "./Dialog";

/** 删除结果的统一形状（单件 / 批量两个接口返回的字段一致）。 */
export type DeleteSummary = {
  deletedIds: number[];
  deletedImages: number;
  affected: AffectedOutfit[];
  orphanedLayers: number;
  deletedOutfits: number;
  skipped: { id: number; reason: string }[];
};

type Props = {
  /** 要删的衣物（1 件或多件）。 */
  targets: Garment[];
  onClose: () => void;
  onDone: (summary: DeleteSummary) => void;
};

/** 名字最多列几个，多了折成「等 N 件」。 */
const NAME_LIMIT = 6;
/** 引用的搭配最多列几套。 */
const OUTFIT_LIMIT = 5;

/**
 * 删除衣物的确认弹窗。
 *
 * 比 `window.confirm` 多做一件事：**先查有哪些搭配用到了这些衣物**。
 * 搭配是用户一层层摆出来的，清理衣橱不该顺手把它们带走 —— 所以默认只删衣物，
 * 被引用的那几层在搭配里退化成「素材已删除」（层上还留着衣物名，缺了什么看得出来）；
 * 真想连搭配一起删，得用户主动勾选。
 */
export default function ConfirmDeleteDialog({ targets, onClose, onDone }: Props) {
  const [refs, setRefs] = useState<OutfitRef[] | null>(null);
  const [cascade, setCascade] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ids = targets.map((g) => g.id);
  const imageCount = targets.reduce((n, g) => n + g.images.length, 0);

  // 打开时查一次「谁引用了它们」。查不到（网络抖）不拦着删 —— 只是退化成没有提示。
  useEffect(() => {
    let alive = true;
    wardrobeApi
      .outfitsReferencing(ids)
      .then((r) => alive && setRefs(r))
      .catch(() => alive && setRefs([]));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- ids 由 targets 派生，随 targets 变
  }, [targets]);

  const names = targets.map((g) => g.name);
  const nameText =
    names.length <= NAME_LIMIT
      ? names.join("、")
      : `${names.slice(0, NAME_LIMIT).join("、")} 等 ${names.length} 件`;
  const layerCount = (refs ?? []).reduce((n, r) => n + r.layers, 0);

  async function confirm() {
    setBusy(true);
    setErr(null);
    try {
      if (targets.length === 1) {
        const res = await wardrobeApi.deleteGarment(targets[0].id, cascade);
        onDone({
          deletedIds: [targets[0].id],
          deletedImages: res.deleted_images,
          affected: res.affected_outfits,
          orphanedLayers: res.orphaned_layers,
          deletedOutfits: res.deleted_outfits,
          skipped: [],
        });
      } else {
        const res = await wardrobeApi.batchDelete(ids, cascade);
        onDone({
          deletedIds: res.deleted,
          deletedImages: res.deleted_images,
          affected: res.affected_outfits,
          orphanedLayers: res.orphaned_layers,
          deletedOutfits: res.deleted_outfits,
          skipped: res.skipped,
        });
      }
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "删除失败");
      setBusy(false);
    }
  }

  const hasRefs = refs !== null && refs.length > 0;

  return (
    <Dialog
      title={targets.length === 1 ? "删除这件衣物？" : `删除这 ${targets.length} 件衣物？`}
      onClose={busy ? () => undefined : onClose}
      width={480}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="btn btn-danger"
            onClick={() => void confirm()}
            disabled={busy}
          >
            {busy ? "删除中…" : hasRefs && cascade ? "删除衣物和搭配" : "确认删除"}
          </button>
        </>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}

      <p className="del-names" title={names.join("、")}>
        {nameText}
      </p>
      <p className="tiny del-sub">
        {imageCount > 0 ? `${imageCount} 张图片会一并删除，` : ""}此操作无法撤销。
      </p>

      {refs === null ? (
        <p className="tiny del-check">正在检查有没有搭配用到…</p>
      ) : !hasRefs ? (
        <p className="del-clear">没有被任何搭配引用，可以放心删除。</p>
      ) : (
        <div className="alert alert-warn del-refs">
          <strong>
            有 {refs.length} 套搭配用到{targets.length === 1 ? "这件衣物" : "这些衣物"}
          </strong>
          <span className="tiny">（共 {layerCount} 层）</span>
          <ul className="del-ref-list">
            {refs.slice(0, OUTFIT_LIMIT).map((r) => (
              <li key={r.id}>
                <span className="del-ref-name" title={r.name}>
                  {r.name}
                </span>
                <span className="tiny">{r.layers} 层</span>
              </li>
            ))}
            {refs.length > OUTFIT_LIMIT && (
              <li className="tiny">…还有 {refs.length - OUTFIT_LIMIT} 套</li>
            )}
          </ul>

          <label className="switch del-cascade">
            <input
              type="checkbox"
              checked={cascade}
              onChange={(e) => setCascade(e.target.checked)}
            />
            <span className="switch-box" aria-hidden="true" />
            <span>连同这 {refs.length} 套搭配一起删除</span>
          </label>

          <p className="tiny del-hint">
            {cascade
              ? "这些搭配和它们的成图会一起删掉。"
              : `不勾选则只删衣物：搭配里对应的 ${layerCount} 层会变成「素材已删除」，搭配本身保留。`}
          </p>
        </div>
      )}
    </Dialog>
  );
}
