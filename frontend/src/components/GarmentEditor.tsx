import { Fragment, useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  wardrobeApi,
  type Garment,
  type GarmentImage,
  type TagCategory,
} from "../api/wardrobe";
import { nowStamp } from "../lib/datetime";
import { confirmDialog } from "../lib/confirm";
import { groupValues, parentCategory, toggleCascaded } from "../lib/tags";
import Dialog from "./Dialog";
import CutoutEditor from "./CutoutEditor";
import Lightbox from "./Lightbox";
import Thumb from "./Thumb";

type Props = {
  /** null = 新建 */
  garment: Garment | null;
  categories: TagCategory[];
  onClose: () => void;
  onSaved: (g: Garment, isNew: boolean) => void;
  onManageTags: () => void;
};

const MAX_MB = 20;

export default function GarmentEditor({ garment, categories, onClose, onSaved, onManageTags }: Props) {
  const isNew = garment === null;

  // 新建时名称默认给当前时间：批量拍照入库的场景下省一次输入，想改随时能改
  const [name, setName] = useState(garment?.name ?? (isNew ? nowStamp() : ""));
  const [notes, setNotes] = useState(garment?.notes ?? "");
  const [selected, setSelected] = useState<Set<number>>(new Set(garment?.tag_value_ids ?? []));
  const [pending, setPending] = useState<File[]>([]);
  const [images, setImages] = useState<GarmentImage[]>(garment?.images ?? []);
  const [saved, setSaved] = useState<Garment | null>(garment);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  /** 大图查看：存的是 images 下标，null = 关闭 */
  const [zoom, setZoom] = useState<number | null>(null);
  /** 抠图精修：正在编辑的那张 cutout */
  const [editingCutout, setEditingCutout] = useState<GarmentImage | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const pendingUrls = useMemo(() => pending.map((f) => URL.createObjectURL(f)), [pending]);

  useEffect(() => () => pendingUrls.forEach(URL.revokeObjectURL), [pendingUrls]);

  function toggle(valueId: number) {
    // 级联：选了「风衣」自动带上「外套」；取消「外套」连带撤掉它的子值
    setSelected((prev) => toggleCascaded(prev, valueId, categories));
  }

  function pickFiles(list: FileList | null) {
    if (!list) return;
    const accepted: File[] = [];
    for (const f of Array.from(list)) {
      if (f.size > MAX_MB * 1024 * 1024) {
        setErr(`「${f.name}」超过 ${MAX_MB} MB，已跳过`);
        continue;
      }
      accepted.push(f);
    }
    if (accepted.length) setPending((prev) => [...prev, ...accepted]);
  }

  async function persist() {
    const trimmed = name.trim();
    if (!trimmed) {
      setErr("请填写名称");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      let current: Garment;
      if (saved === null) {
        current = await wardrobeApi.createGarment({
          name: trimmed,
          notes: notes.trim() || null,
          tag_value_ids: [...selected],
        });
      } else {
        current = await wardrobeApi.updateGarment(saved.id, {
          name: trimmed,
          notes: notes.trim() || null,
          tag_value_ids: [...selected],
        });
      }

      if (pending.length) {
        setUploading(true);
        current = await wardrobeApi.uploadImages(current.id, pending);
        setPending([]);
        setUploading(false);
      }

      setSaved(current);
      setImages(current.images);
      onSaved(current, isNew);
      onClose();
    } catch (e) {
      setUploading(false);
      setErr(e instanceof ApiError ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  }

  /** 把衣物丢回 AI 流水线（抠图 + 打标），Worker 跑完会变成「待复核」。 */
  async function enqueue() {
    if (saved === null) {
      setErr("先保存衣物，再交给 AI 处理");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const updated = await wardrobeApi.processGarment(saved.id);
      setSaved(updated);
      onSaved(updated, false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "入队失败");
    } finally {
      setBusy(false);
    }
  }

  async function imageAction(fn: () => Promise<Garment>) {
    setBusy(true);
    setErr(null);
    try {
      const updated = await fn();
      setSaved(updated);
      setImages(updated.images);
      onSaved(updated, false);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "操作失败");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      title={isNew ? "添加衣物" : `编辑：${garment?.name}`}
      onClose={onClose}
      width={720}
      footer={
        <div className="row-between">
          <button
            type="button"
            className="btn btn-sm"
            disabled={busy || saved === null || saved.status === "processing"}
            title="交给 Worker 抠图 + 打标，跑完后在列表里点「复核」确认"
            onClick={() => void enqueue()}
          >
            {saved?.status === "processing"
              ? "处理中…"
              : saved?.status === "pending"
                ? "已在队列中"
                : "AI 抠图打标"}
          </button>
          <div className="row-end">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void persist()}
              disabled={busy}
            >
              {busy ? "保存中…" : isNew ? "创建" : "保存"}
            </button>
          </div>
        </div>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}

      <div className="field">
        <label>名称</label>
        <input
          value={name}
          placeholder="如：米白针织开衫"
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
      </div>

      <div className="field">
        <label>备注（可选）</label>
        <textarea
          rows={2}
          value={notes}
          placeholder="买于哪里、尺码、搭配心得…"
          onChange={(e) => setNotes(e.target.value)}
        />
      </div>

      <div className="field">
        <div className="row-between">
          <label>标签</label>
          <button type="button" className="btn btn-sm" onClick={onManageTags}>
            管理标签
          </button>
        </div>
        {categories.length === 0 ? (
          <p className="tiny">还没有标签类别，点「管理标签」新建。</p>
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

      <div className="field">
        <div className="row-between">
          <label>图片（可多张，第一张为封面 · 点击看大图）</label>
          <button type="button" className="btn btn-sm" onClick={() => fileRef.current?.click()}>
            选择文件
          </button>
        </div>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/webp"
          multiple
          hidden
          onChange={(e) => {
            pickFiles(e.target.files);
            e.target.value = "";
          }}
        />

        {images.length === 0 && pending.length === 0 ? (
          <div className="empty">还没有图片。上传后可以点「AI 抠图打标」自动去背景。</div>
        ) : (
          <div className="imgrid">
            {images.map((img, idx) => (
              <figure className="imgcell" key={img.id}>
                <button
                  type="button"
                  className="imgcell-open"
                  onClick={() => setZoom(idx)}
                  title="点击查看大图"
                >
                  <Thumb src={img.thumb_small_url} fallback="图片丢失" />
                </button>
                {idx === 0 && <span className="imgcell-badge">封面</span>}
                {img.kind === "cutout" && (
                  <button
                    type="button"
                    className="link-btn accent"
                    disabled={busy || !saved}
                    onClick={() => setEditingCutout(img)}
                    title="二次抠图 / 手动修整"
                  >
                    修整抠图
                  </button>
                )}
                <figcaption>
                  <button
                    type="button"
                    className="link-btn"
                    disabled={busy || idx === 0}
                    onClick={() => void imageAction(() => wardrobeApi.setPrimaryImage(saved!.id, img.id))}
                  >
                    设封面
                  </button>
                  <button
                    type="button"
                    className="link-btn danger"
                    disabled={busy}
                    onClick={() => {
                      void (async () => {
                        const ok = await confirmDialog({
                          title: "删除这张图片？",
                          message: "图片会从这件衣物上移除，此操作无法撤销。",
                          confirmLabel: "删除",
                          danger: true,
                        });
                        if (!ok) return;
                        void imageAction(() => wardrobeApi.deleteImage(saved!.id, img.id));
                      })();
                    }}
                  >
                    删除
                  </button>
                </figcaption>
              </figure>
            ))}

            {pending.map((f, i) => (
              <figure className="imgcell pending" key={`${f.name}-${i}`}>
                <img src={pendingUrls[i]} alt="" />
                <span className="imgcell-badge">待上传</span>
                <figcaption>
                  <button
                    type="button"
                    className="link-btn danger"
                    disabled={busy}
                    onClick={() => setPending((prev) => prev.filter((_, j) => j !== i))}
                  >
                    移除
                  </button>
                </figcaption>
              </figure>
            ))}
          </div>
        )}

        {uploading && <p className="tiny">上传中…</p>}
      </div>

      {zoom !== null && zoom < images.length && (
        <Lightbox
          items={images.map((img) => ({
            key: String(img.id),
            url: img.original_url,
            label: img.kind === "cutout" ? "抠图结果" : "原图",
            caption: `${img.width}×${img.height}`,
            transparent: img.kind === "cutout",
          }))}
          index={zoom}
          onClose={() => setZoom(null)}
          onIndexChange={setZoom}
          actions={(item, idx) => (
            <div className="row-between">
              <span className="tiny">
                {idx === 0 ? "这张已经是封面" : "设为封面后，衣橱卡片会换成这张"}
              </span>
              <div className="row-end">
                <button
                  type="button"
                  className="btn btn-sm"
                  disabled={busy || idx === 0}
                  onClick={() =>
                    void imageAction(() =>
                      wardrobeApi.setPrimaryImage(saved!.id, Number(item.key)),
                    )
                  }
                >
                  设为封面
                </button>
                <button
                  type="button"
                  className="btn btn-sm danger"
                  disabled={busy}
                  onClick={() => {
                    void (async () => {
                      const ok = await confirmDialog({
                        title: "删除这张图片？",
                        message: "图片会从这件衣物上移除，此操作无法撤销。",
                        confirmLabel: "删除",
                        danger: true,
                      });
                      if (!ok) return;
                      void imageAction(() =>
                        wardrobeApi.deleteImage(saved!.id, Number(item.key)),
                      );
                      setZoom(null);
                    })();
                  }}
                >
                  删除这张
                </button>
              </div>
            </div>
          )}
        />
      )}

      {editingCutout && saved && (
        <CutoutEditor
          garment={saved}
          image={editingCutout}
          onClose={() => setEditingCutout(null)}
          onSaved={(updated) => {
            setSaved(updated);
            setImages(updated.images);
            // 保存即完成：编辑器随后自己会关，回到衣物编辑（想再修重新点进来，
            // 那时的基线就是刚保存的结果）。框选重抠不一样 —— 它走 applyBox，
            // 不关编辑器，因为重抠完通常还要接着涂几笔。
            setEditingCutout(null);
            onSaved(updated, false);
          }}
        />
      )}
    </Dialog>
  );
}

/**
 * 编辑器里的标签选择行（复核弹窗复用同一套手势，避免两处走样）。
 *
 * 级联子类别（小类）按父取值分组；一旦选了父值就只列该父值下的子值，
 * 和筛选栏保持同一套手感。
 */
export function TagPickRow({
  cat,
  categories,
  selected,
  onToggle,
  onCreateValue,
}: {
  cat: TagCategory;
  categories: TagCategory[];
  selected: Set<number>;
  onToggle: (valueId: number) => void;
  /** 传了才显示「+ 取值」：就地给这个类别补一个新取值，并顺手选上。 */
  onCreateValue?: (cat: TagCategory, value: string) => Promise<unknown> | unknown;
}) {
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // 回车提交会让输入框卸载并触发 blur，用它防止 blur 把「正在提交」当成「点了别处」
  const typingRef = useRef(false);

  const submit = async () => {
    const value = draft.trim();
    if (!value) {
      setAdding(false);
      return;
    }
    setSaving(true);
    typingRef.current = true;
    try {
      await onCreateValue?.(cat, value);
      setDraft("");
      setAdding(false);
    } finally {
      typingRef.current = false;
      setSaving(false);
    }
  };

  const parent = parentCategory(cat, categories);
  const groups = groupValues(cat, parent);
  const selectedParents = parent
    ? new Set(parent.values.filter((v) => selected.has(v.id)).map((v) => v.id))
    : null;
  const narrowed = selectedParents !== null && selectedParents.size > 0;
  const shown = narrowed
    ? groups.filter((g) => g.parent !== null && selectedParents.has(g.parent.id))
    : groups;
  const count = shown.reduce((n, g) => n + g.values.length, 0);

  return (
    <div className="tagpick-row">
      <span
        className="tagpick-name"
        title={parent ? `${cat.name}（级联自「${parent.name}」）` : cat.name}
      >
        {cat.name}
        {parent && <em className="filterbar-cascade">↖</em>}
      </span>
      <div className="chips">
        {count === 0 && !onCreateValue ? (
          <span className="tiny">
            {parent && narrowed
              ? `所选「${parent.name}」下还没有取值`
              : "（无取值，点「管理标签」加）"}
          </span>
        ) : (
          shown.map((group) => (
            <Fragment key={group.parent?.id ?? "orphan"}>
              {parent && (
                <span className="chip-group">{group.parent ? group.parent.value : "未归类"}</span>
              )}
              {group.values.map((v) => (
                <button
                  type="button"
                  key={v.id}
                  className={`chip chip-toggle${selected.has(v.id) ? " on" : ""}`}
                  onClick={() => onToggle(v.id)}
                >
                  {v.value}
                </button>
              ))}
            </Fragment>
          ))
        )}
        {onCreateValue &&
          (adding ? (
            <input
              className="chip chip-input"
              autoFocus
              value={draft}
              placeholder={`新${cat.name}`}
              maxLength={32}
              disabled={saving}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  void submit();
                } else if (e.key === "Escape") {
                  typingRef.current = true;
                  setAdding(false);
                  setDraft("");
                }
              }}
              onBlur={() => {
                // 走神点了别处就当取消，不留半个没提交的输入框
                if (!typingRef.current && !saving) {
                  setAdding(false);
                  setDraft("");
                }
              }}
            />
          ) : (
            <button
              type="button"
              className="chip chip-add"
              title={`给「${cat.name}」加一个取值`}
              onClick={() => {
                setDraft("");
                setAdding(true);
              }}
            >
              + 取值
            </button>
          ))}
      </div>
    </div>
  );
}
