import { useEffect, useState } from "react";

type Props = {
  src: string;
  alt?: string;
  /** 加载失败时显示的文案 */
  fallback?: string;
};

/**
 * 带降级的缩略图。
 *
 * 图片是磁盘上的文件，可能因为清理、迁移、卷挂载错位而丢失。默认的 `<img>`
 * 失败只会渲染一个破图图标，看不出是「没图」还是「图挂了」。这里退化成占位块，
 * 并且 `src` 变化时重置状态（列表刷新后可能换成新的签名 URL）。
 */
export default function Thumb({ src, alt = "", fallback = "图片丢失" }: Props) {
  const [failed, setFailed] = useState(false);

  useEffect(() => setFailed(false), [src]);

  if (failed) return <span className="gcard-noimg">{fallback}</span>;

  return <img src={src} alt={alt} loading="lazy" onError={() => setFailed(true)} />;
}
