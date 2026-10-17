/** 批次 3 流水线的状态展示。UI 只认这里，不要在组件里散落字符串字面量。 */

import type { AiSuggestion, GarmentStatus } from "../api/wardrobe";

type StatusMeta = {
  label: string;
  /** 对应 CSS 修饰类：`badge-${tone}` */
  tone: string;
  hint: string;
};

export const STATUS_META: Record<GarmentStatus, StatusMeta> = {
  pending: { label: "排队中", tone: "pending", hint: "已入队，等 Worker 捞走处理" },
  processing: { label: "处理中", tone: "processing", hint: "Worker 正在抠图 / 打标" },
  needs_review: { label: "待复核", tone: "review", hint: "AI 出了候选结果，等你确认" },
  ready: { label: "已就绪", tone: "ready", hint: "" },
  failed: { label: "处理失败", tone: "failed", hint: "可以重新入队重试" },
};

export function statusMeta(status: GarmentStatus): StatusMeta {
  return STATUS_META[status] ?? { label: status, tone: "pending", hint: "" };
}

/** 还在线上跑着，列表要自动刷新才看得到变化。 */
export function isBusy(status: GarmentStatus): boolean {
  return status === "pending" || status === "processing";
}

/** 需要用户点一下才有进展的状态（复核 / 重试）。 */
export function needsAttention(status: GarmentStatus): boolean {
  return status === "needs_review" || status === "failed";
}

/** 「待复核」筛选按钮用的状态集合。 */
export const ATTENTION_STATUSES: GarmentStatus[] = ["needs_review", "failed", "pending", "processing"];

export function providerLabel(provider: string | null | undefined): string {
  switch (provider) {
    case "rembg":
      return "抠图（rembg）";
    case "rembg+clip":
      return "抠图 + CLIP 打标";
    case "rembg+vlm":
      return "抠图 + 大模型打标";
    case "clip":
      return "CLIP 打标";
    case "vlm":
      return "大模型打标";
    case "none":
      return "未启用 AI";
    default:
      return provider || "未知来源";
  }
}

/** 置信度只在有意义时才显示，避免把 null 渲染成 0%。 */
export function confidenceText(s: AiSuggestion | null): string | null {
  if (!s || s.confidence == null) return null;
  return `${Math.round(s.confidence * 100)}%`;
}
