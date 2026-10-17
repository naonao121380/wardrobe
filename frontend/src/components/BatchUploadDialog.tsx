import { useEffect, useMemo, useRef, useState } from "react";

import { ApiError } from "../api/client";
import { wardrobeApi, type BatchUploadResult } from "../api/wardrobe";
import { nowStamp } from "../lib/datetime";
import { splitFilesByBytes, type UploadProgress } from "../lib/upload";
import Dialog from "./Dialog";

type Props = {
  onClose: () => void;
  /** 上传完成，把结果交给列表页（刷新 / 起任务跟踪）。 */
  onDone: (result: BatchUploadResult, enqueued: boolean) => void;
};

const MAX_MB = 20;

/**
 * 批量上传：**一张图一件衣物**。
 *
 * 一件一件建太慢 —— 拍回来一整摞衣服，全选、传完、顺手把 AI 也排上队。
 * 名称默认取当前时间（可改），一批多张服务端会自动加 `(2)` `(3)` 后缀。
 */
export default function BatchUploadDialog({ onClose, onDone }: Props) {
  const [files, setFiles] = useState<File[]>([]);
  const [name, setName] = useState(() => nowStamp());
  const [enqueue, setEnqueue] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);

  const fileRef = useRef<HTMLInputElement>(null);
  const urls = useMemo(() => files.map((f) => URL.createObjectURL(f)), [files]);
  useEffect(() => () => urls.forEach(URL.revokeObjectURL), [urls]);
  // 只有「能不能一次发完」这个信息，跟字节数有关，跟张数无关
  const batchCount = useMemo(() => splitFilesByBytes(files).length, [files]);

  function pick(list: FileList | null) {
    if (!list) return;
    const accepted: File[] = [];
    const tooBig: string[] = [];
    for (const f of Array.from(list)) {
      if (f.size > MAX_MB * 1024 * 1024) tooBig.push(f.name);
      else accepted.push(f);
    }
    if (tooBig.length) setErr(`${tooBig.length} 个文件超过 ${MAX_MB} MB，已跳过：${tooBig.join("、")}`);
    else setErr(null);
    if (accepted.length) setFiles((prev) => [...prev, ...accepted]);
  }

  function dropFile(index: number) {
    setFiles((prev) => prev.filter((_, i) => i !== index));
  }

  async function upload() {
    if (files.length === 0) {
      setErr("还没选文件");
      return;
    }
    setBusy(true);
    setErr(null);
    setProgress(null);
    try {
      const res = await wardrobeApi.batchUpload(files, {
        name: name.trim() || nowStamp(),
        enqueue,
        onProgress: setProgress,
      });
      onDone(res, enqueue);
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "上传失败");
      setProgress(null);
      setBusy(false);
    }
  }

  return (
    <Dialog
      title="批量上传"
      onClose={onClose}
      width={720}
      footer={
        <div className="row-between">
          <span className="tiny">
            {files.length === 0
              ? "一张图 = 一件衣物"
              : `共 ${files.length} 张，将建成 ${files.length} 件衣物${
                  batchCount > 1 ? `（分 ${batchCount} 批上传）` : ""
                }`}
          </span>
          <div className="row-end">
            <button type="button" className="btn" onClick={onClose} disabled={busy}>
              取消
            </button>
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => void upload()}
              disabled={busy || files.length === 0}
            >
              {busy
                ? progress
                  ? `上传中 ${progress.batch}/${progress.batches} · 已传 ${progress.done}/${progress.total}`
                  : "上传中…"
                : `上传（${files.length}）`}
            </button>
          </div>
        </div>
      }
    >
      {err && <div className="alert alert-error">{err}</div>}

      <div className="field">
        <div className="row-between">
          <label>名称前缀</label>
          <button type="button" className="btn btn-sm" onClick={() => setName(nowStamp())}>
            重置为当前时间
          </button>
        </div>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder={nowStamp()}
        />
        <p className="tiny">
          一件就用这个名称；多件会自动在后面加 (2)、(3)…，方便先批量传、之后再逐件改名。
        </p>
      </div>

      <div className="field">
        <div className="row-between">
          <label>图片（可多选）</label>
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
            pick(e.target.files);
            e.target.value = "";
          }}
        />
        {files.length === 0 ? (
          <>
            <div
              className="dropzone"
              onClick={() => fileRef.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && fileRef.current?.click()}
            >
              点这里选择照片（支持多选，单张 ≤ {MAX_MB} MB）
            </div>
            <p className="tiny">
              张数不限：一次选几十张也行，会按大小自动拆成几批发，不用自己分批。
            </p>
          </>
        ) : (
          <div className="upload-grid">
            {files.map((f, i) => (
              <div className="upload-cell" key={`${f.name}-${i}`}>
                <img src={urls[i]} alt={f.name} />
                <button
                  type="button"
                  className="upload-drop"
                  title="移除这张"
                  onClick={() => dropFile(i)}
                >
                  ✕
                </button>
                <span className="upload-name">{f.name}</span>
              </div>
            ))}
            <button
              type="button"
              className="upload-cell upload-add"
              onClick={() => fileRef.current?.click()}
            >
              + 再加
            </button>
          </div>
        )}
      </div>

      <label className="switch">
        <input
          type="checkbox"
          checked={enqueue}
          onChange={(e) => setEnqueue(e.target.checked)}
        />
        <span className="switch-box" aria-hidden="true" />
        <span>上传后立即交给 AI 抠图打标（后台跑，跑完在列表里提示复核）</span>
      </label>
    </Dialog>
  );
}
