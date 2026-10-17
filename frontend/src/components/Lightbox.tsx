/**
 * 图片大图查看层。
 *
 * 几个刻意的选择：
 * - **不用 Dialog 包一层**：大图要铺满视口，Dialog 的 modal 宽度模型反而碍事；
 *   但 Esc 键得自己在 capture 阶段拦下来，否则会把背后的编辑弹窗一起关掉
 *   （已知坑：内联输入框的 Esc 会冒泡到 Dialog）。
 * - **抠图垫棋盘格**：透明 PNG 直接放白底上看不出背景到底去没去掉。
 * - **切换时不清空上一张**：先留着旧图，等新图 onLoad 再换，避免中间闪白。
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";

export type LightboxItem = {
  key: string;
  /** 大图地址（原图） */
  url: string;
  /** 角落小字，如「原图」「抠图」 */
  label: string;
  /** 副标题，如尺寸 / 大小 */
  caption?: string;
  /** 透明 PNG —— 需要棋盘格衬底 */
  transparent?: boolean;
};

type Props = {
  items: LightboxItem[];
  index: number;
  onClose: () => void;
  onIndexChange: (next: number) => void;
  /** 每张图下方的操作区（设封面 / 删除等），由调用方决定放什么 */
  actions?: (item: LightboxItem, idx: number) => ReactNode;
};

export default function Lightbox({ items, index, onClose, onIndexChange, actions }: Props) {
  const total = items.length;
  const safeIndex = total === 0 ? 0 : Math.min(Math.max(index, 0), total - 1);
  const item = items[safeIndex];

  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);

  // 换图时重置状态
  useEffect(() => {
    setLoaded(false);
    setFailed(false);
  }, [item?.url]);

  const step = useCallback(
    (delta: number) => {
      if (total === 0) return;
      onIndexChange((safeIndex + delta + total) % total);
    },
    [onIndexChange, safeIndex, total],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        // 拦下，别冒泡到背后的 Dialog
        e.stopPropagation();
        onClose();
      } else if (e.key === "ArrowLeft") {
        e.preventDefault();
        step(-1);
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        step(1);
      }
    };
    // capture=true：抢在 Dialog 的 document 级监听之前处理
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose, step]);

  if (!item) return null;

  return (
    <div
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label="查看大图"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <button
        type="button"
        className="lightbox-close icon-btn"
        onClick={onClose}
        aria-label="关闭"
      >
        ✕
      </button>

      {total > 1 && (
        <button
          type="button"
          className="lightbox-nav prev"
          onClick={() => step(-1)}
          aria-label="上一张"
        >
          ‹
        </button>
      )}

      <figure className="lightbox-stage">
        <div className={`lightbox-frame${item.transparent ? " cutout-stage" : ""}`}>
          {failed ? (
            <div className="empty">这张图加载不出来（可能文件已丢失）</div>
          ) : (
            <img
              src={item.url}
              alt={item.label}
              onLoad={() => setLoaded(true)}
              onError={() => setFailed(true)}
              style={{ opacity: loaded ? 1 : 0 }}
            />
          )}
          {!loaded && !failed && <span className="tiny">加载中…</span>}
        </div>
        <figcaption>
          <strong>{item.label}</strong>
          {item.caption ? <span className="tiny"> · {item.caption}</span> : null}
          {total > 1 ? (
            <span className="tiny">
              {" "}
              · {safeIndex + 1}/{total}
            </span>
          ) : null}
          {" · "}
          <a className="lightbox-open-link" href={item.url} target="_blank" rel="noreferrer">
            新标签打开
          </a>
        </figcaption>
      </figure>

      {total > 1 && (
        <button
          type="button"
          className="lightbox-nav next"
          onClick={() => step(1)}
          aria-label="下一张"
        >
          ›
        </button>
      )}

      {actions ? <div className="lightbox-actions">{actions(item, safeIndex)}</div> : null}
    </div>
  );
}
