import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import {
  wardrobeApi,
  type Garment,
  type GarmentImage,
  type TagCategory,
} from "../api/wardrobe";
import Dialog from "./Dialog";
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

  const [name, setName] = useState(garment?.name ?? "");
  const [notes, setNotes] = useState(garment?.notes ?? "");
  const [selected, setSelected] = useState<Set<number>>(new Set(garment?.tag_value_ids ?? []));
  const [pending, setPending] = useState<File[]>([]);
  const [images, setImages] = useState<GarmentImage[]>(garment?.images ?? []);
  const [saved, setSaved] = useState<Garment | null>(garment);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  const fileRef = useRef<HTMLInputElement>(null);
  const pendingUrls = useMemo(() => pending.map((f) => URL.createObjectURL(f)), [pending]);

  useEffect(() => () => pendingUrls.forEach(URL.revokeObjectURL), [pendingUrls]);

  function toggle(valueId: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(valueId)) next.delete(valueId);
      else next.add(valueId);
      return next;
    });
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
        <div className="row-end">
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={() => void persist()} disabled={busy}>
            {busy ? "保存中…" : isNew ? "创建" : "保存"}
          </button>
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
              <div className="tagpick-row" key={cat.id}>
                <span className="tagpick-name">{cat.name}</span>
                <div className="chips">
                  {cat.values.length === 0 ? (
                    <span className="tiny">（无取值）</span>
                  ) : (
                    cat.values.map((v) => (
                      <button
                        type="button"
                        key={v.id}
                        className={`chip chip-toggle${selected.has(v.id) ? " on" : ""}`}
                        onClick={() => toggle(v.id)}
                      >
                        {v.value}
                      </button>
                    ))
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="field">
        <div className="row-between">
          <label>图片（可多张，第一张为封面）</label>
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
          <div className="empty">还没有图片。批次 3 会在这里接入智能抠图。</div>
        ) : (
          <div className="imgrid">
            {images.map((img, idx) => (
              <figure className="imgcell" key={img.id}>
                <Thumb src={img.thumb_small_url} fallback="图片丢失" />
                {idx === 0 && <span className="imgcell-badge">封面</span>}
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
                      if (!confirm("删除这张图片？")) return;
                      void imageAction(() => wardrobeApi.deleteImage(saved!.id, img.id));
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
    </Dialog>
  );
}
