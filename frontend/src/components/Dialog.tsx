import { useEffect, useRef, type ReactNode } from "react";

type Props = {
  title: string;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  width?: number;
  /** 额外的 modal 类名：个别弹窗要突破默认的 90vh 高度上限时用。 */
  className?: string;
};

/** 轻量模态框：Esc 关闭、点遮罩关闭、锁滚动。 */
export default function Dialog({
  title,
  onClose,
  children,
  footer,
  width = 560,
  className = "",
}: Props) {
  const overlayRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      // 弹窗可以套弹窗（如编辑衣物时弹删除确认）。Esc 只该关掉最上面那层 ——
      // 每个 Dialog 都挂了 keydown，不判层级就会一次把两层一起关掉。
      const overlays = document.querySelectorAll<HTMLElement>(".overlay");
      const top = overlays[overlays.length - 1];
      if (top && top !== overlayRef.current) return;
      onClose();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <div
      className="overlay"
      ref={overlayRef}
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
    >
      <div
        className={`modal${className ? ` ${className}` : ""}`}
        style={{ maxWidth: width }}
        role="dialog"
        aria-modal="true"
      >
        <header className="modal-head">
          <h3>{title}</h3>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            ✕
          </button>
        </header>
        <div className="modal-body">{children}</div>
        {footer && <footer className="modal-foot">{footer}</footer>}
      </div>
    </div>
  );
}
