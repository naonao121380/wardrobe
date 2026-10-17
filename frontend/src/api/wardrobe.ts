/**
 * 衣物 / 标签接口。
 *
 * 标签分两套作用域（scope）：
 * - `garment`：大类 / 小类 / 颜色 / 材质
 * - `outfit` ：季节 / 风格 / 色系（批次 4 使用）
 * 两者在服务端同表不同 scope，互不可见。
 */

import { ApiError, request } from "./client";
import { splitFilesByBytes, type UploadProgress } from "../lib/upload";

export type TagScope = "garment" | "outfit";

export type TagValue = {
  id: number;
  value: string;
  sort_order: number;
  /** 父取值 id。只有级联子类别（「小类」）的取值才有；null = 未归类。 */
  parent_value_id: number | null;
  /** 有多少件衣物在用它；0 = 没有任何衣物命中，删除时不必再确认。 */
  usage_count: number;
};

export type TagCategory = {
  id: number;
  scope: TagScope;
  name: string;
  sort_order: number;
  /** 父类别 id。null = 顶层类别；「小类」指向「大类」。 */
  parent_category_id: number | null;
  values: TagValue[];
  /** 本类别下所有取值被引用的总次数；0 = 整个类别都没被任何衣物使用。 */
  usage_count: number;
};

/**
 * 标签配置的导出文件。
 *
 * 里面**只有名字、没有 id** —— id 换个库/换个账号就全变了，没法当锚点。
 * 父级一律用名字表达：`parent` 对类别是父**类别名**，对取值是父**取值文本**。
 */
export type TagValueConfig = {
  value: string;
  sort_order: number;
  parent: string | null;
};

export type TagCategoryConfig = {
  name: string;
  sort_order: number;
  parent: string | null;
  values: TagValueConfig[];
};

export type TagConfigFile = {
  kind: "wardrobe.tag-config";
  version: number;
  scope: TagScope;
  exported_at: string;
  categories: TagCategoryConfig[];
};

export type TagConfigImportResult = {
  scope: TagScope;
  categories_created: number;
  categories_skipped: number;
  values_created: number;
  values_skipped: number;
  /** 非致命问题：父级认不出来、超过两层被降级… */
  warnings: string[];
};

export type GarmentImage = {
  id: number;
  kind: string;
  width: number;
  height: number;
  sort_order: number;
  thumb_small_url: string;
  thumb_medium_url: string;
  original_url: string;
};

export type GarmentTagRef = {
  value_id: number;
  value: string;
  category_id: number;
  category_name: string;
};

export type AiSuggestionTag = {
  value_id: number;
  value: string;
  category_id: number;
  category_name: string;
};

/** Worker 产出的候选结果（批次 3）。复核界面照着它渲染。 */
export type AiSuggestion = {
  name: string | null;
  tag_value_ids: number[];
  tags: AiSuggestionTag[];
  /** rembg / rembg+clip / vlm / none / manual */
  provider: string;
  confidence: number | null;
  error: string | null;
};

/** 流水线状态机（DECISIONS D6）。 */
export type GarmentStatus =
  | "pending"
  | "processing"
  | "needs_review"
  | "ready"
  | "failed";

export const GARMENT_STATUSES: GarmentStatus[] = [
  "pending",
  "processing",
  "needs_review",
  "ready",
  "failed",
];

export type Garment = {
  id: number;
  name: string;
  notes: string | null;
  created_at: string;
  updated_at: string;
  cover_url: string | null;
  images: GarmentImage[];
  tag_value_ids: number[];
  tags: GarmentTagRef[];
  status: GarmentStatus;
  ai_suggestion: AiSuggestion | null;
};

export type GarmentList = {
  items: Garment[];
  total: number;
  page: number;
  page_size: number;
};

/** 流水线各状态的件数。 */
export type QueueStatus = {
  pending: number;
  processing: number;
  needs_review: number;
  ready: number;
  failed: number;
};

/** 批量入队的结果。 */
export type BatchProcessResult = {
  enqueued: number[];
  skipped: { id: number; reason: string }[];
};

/** 删衣物会牵连到的搭配（用了几层）。 */
export type AffectedOutfit = {
  id: number;
  name: string;
  layers: number;
};

/** 删单件衣物的结果。 */
export type GarmentDeleteResult = {
  deleted_images: number;
  /** 引用了这件衣物的搭配清单（默认保留它们） */
  affected_outfits: AffectedOutfit[];
  /** 保留搭配时，变成「素材已删除」的层数 */
  orphaned_layers: number;
  /** 连搭配一起删掉的套数（cascade 时才有） */
  deleted_outfits: number;
};

/** 批量删除的结果。 */
export type BatchDeleteResult = {
  deleted: number[];
  deleted_images: number;
  skipped: { id: number; reason: string }[];
  affected_outfits: AffectedOutfit[];
  orphaned_layers: number;
  deleted_outfits: number;
};

/** 批量改标签的结果。 */
export type BatchTagsResult = {
  updated: number[];
  skipped: { id: number; reason: string }[];
};

/** 批量上传的结果：建成的衣物 + 被跳过的文件（含理由）。 */
export type BatchUploadResult = {
  created: Garment[];
  skipped: { file: string; reason: string }[];
};

// ---------- 批次 4：搭配 ----------

/** 画布上的一层。坐标全部归一化：x/y 是中心点（0~1），w_ratio 是宽度占画布的比例。 */
export type OutfitItem = {
  id: number;
  garment_id: number | null;
  garment_image_id: number | null;
  name_snapshot: string | null;
  x: number;
  y: number;
  w_ratio: number;
  rotation: number;
  z: number;
  flip_h: boolean;
  opacity: number;
  /** 素材图签名 URL；素材被删了就是 null。 */
  image_url: string | null;
  width: number;
  height: number;
  /** true = 引用的衣物 / 图片已经不在了。 */
  missing: boolean;
  garment_name: string | null;
};

/** 保存时提交的一层（z 由数组顺序决定，不用传）。 */
export type OutfitItemIn = {
  garment_id?: number | null;
  garment_image_id?: number | null;
  name_snapshot?: string | null;
  x: number;
  y: number;
  w_ratio: number;
  rotation: number;
  flip_h: boolean;
  opacity: number;
};

export type OutfitImage = {
  id: number;
  /**
   * result = 编辑器合成的搭配图（只留最新一张，卡片默认显示它）；
   * worn = **上身图**（可选、可多张）；老数据里可能是早期别名 upload。
   */
  kind: string;
  width: number;
  height: number;
  sort_order: number;
  thumb_small_url: string;
  thumb_medium_url: string;
  original_url: string;
};

/** 是不是上身图。早期数据把上身图存成 upload，两者一律当上身图看待。 */
export const isWornImage = (kind: string) => kind === "worn" || kind === "upload";

export type OutfitTagRef = {
  value_id: number;
  value: string;
  category_id: number;
  category_name: string;
};

export type Outfit = {
  id: number;
  name: string;
  notes: string | null;
  background: string;
  created_at: string;
  updated_at: string;
  cover_url: string | null;
  images: OutfitImage[];
  items: OutfitItem[];
  tag_value_ids: number[];
  tags: OutfitTagRef[];
};

export type OutfitList = {
  items: Outfit[];
  total: number;
  page: number;
  page_size: number;
};

/** 「这件衣物被哪些搭配用过」的一条记录。 */
export type OutfitRef = {
  id: number;
  name: string;
  layers: number;
};

/** AI 给一套搭配起的名 + 挑的标签（只是建议，界面上确认后才落库）。 */
export type OutfitSuggestion = {
  name: string | null;
  tag_value_ids: number[];
  tags: OutfitTagRef[];
  confidence: number | null;
  error: string | null;
  hint: string | null;
};

/** 打标模型有没有配好 —— 决定「AI 推荐」按钮亮着还是置灰。 */
export type OutfitAiStatus = {
  provider: string;
  available: boolean;
};

function qs(
  params: Record<string, string | number | undefined>,
  list?: [string, string | number][],
): string {
  const sp = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== "") sp.set(k, String(v));
  }
  for (const [k, v] of list ?? []) sp.append(k, String(v));
  const s = sp.toString();
  return s ? `?${s}` : "";
}

export const wardrobeApi = {
  // ---------- 标签 ----------

  listCategories: (scope: TagScope = "garment") =>
    request<TagCategory[]>(`/tags/categories${qs({ scope })}`),

  createCategory: (scope: TagScope, name: string, parentCategoryId: number | null = null) =>
    request<TagCategory>("/tags/categories", {
      method: "POST",
      body: JSON.stringify({ scope, name, parent_category_id: parentCategoryId }),
    }),

  renameCategory: (categoryId: number, name: string) =>
    request<TagCategory>(`/tags/categories/${categoryId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  /** 改类别的父级（挂到某个顶层类别下 / 提升为顶层）。 */
  setCategoryParent: (categoryId: number, parentCategoryId: number | null) =>
    request<TagCategory>(`/tags/categories/${categoryId}`, {
      method: "PATCH",
      body: JSON.stringify({ parent_category_id: parentCategoryId }),
    }),

  deleteCategory: (categoryId: number) =>
    request<{ deleted_values: number; demoted_categories: number }>(
      `/tags/categories/${categoryId}`,
      { method: "DELETE" },
    ),

  createValue: (categoryId: number, value: string, parentValueId: number | null = null) =>
    request<TagValue>(`/tags/categories/${categoryId}/values`, {
      method: "POST",
      body: JSON.stringify({ value, parent_value_id: parentValueId }),
    }),

  renameValue: (valueId: number, value: string) =>
    request<TagValue>(`/tags/values/${valueId}`, {
      method: "PATCH",
      body: JSON.stringify({ value }),
    }),

  /** 改取值的归属（挂到父类别下的某个取值 / 取消归类）。 */
  setValueParent: (valueId: number, parentValueId: number | null) =>
    request<TagValue>(`/tags/values/${valueId}`, {
      method: "PATCH",
      body: JSON.stringify({ parent_value_id: parentValueId }),
    }),

  deleteValue: (valueId: number) =>
    request<{ untagged: number }>(`/tags/values/${valueId}`, { method: "DELETE" }),

  /**
   * 导出整套标签配置（类别 + 取值 + 级联关系）。
   * 文件是**可移植**的：换账号、换机器都能导回去。
   */
  exportTagConfig: (scope: TagScope = "garment") =>
    request<TagConfigFile>(`/tags/export${qs({ scope })}`),

  /**
   * 导入标签配置：**只补不删**，按名字去重。
   * 直接把读进来的文件对象传过来即可（`kind`/`version`/`scope` 一并带上更稳），
   * 最小化的 `{ categories: [...] }` 也认。
   */
  importTagConfig: (
    payload: { categories: TagCategoryConfig[] } & Partial<TagConfigFile>,
    scope: TagScope = "garment",
  ) =>
    request<TagConfigImportResult>(`/tags/import${qs({ scope })}`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // ---------- 衣物 ----------

  /**
   * `tagGroups` 每个元素是一组**要同时满足**的标签（组内 AND），组与组之间是 OR。
   *
   * 分面筛选摊平规则：各维度内部是 OR，维度之间是 AND —— 前端把各维度的或条件做
   * 笛卡尔积，摊平成一串组。详见 `buildTagGroups`。
   */
  listGarments: (
    opts: {
      q?: string;
      tagValueIds?: number[];
      tagGroups?: number[][];
      /** 按流水线状态多选过滤；空数组 = 不过滤 */
      statuses?: GarmentStatus[];
      /** true = 只要还没抠过图的（一张 cutout 都没有） */
      unprocessed?: boolean;
      page?: number;
      pageSize?: number;
    } = {},
  ) =>
    request<GarmentList>(
      `/garments${qs(
        {
          q: opts.q,
          page: opts.page,
          page_size: opts.pageSize,
          unprocessed: opts.unprocessed ? 1 : undefined,
        },
        [
          ...(opts.tagValueIds ?? []).map((v) => ["tag_value_ids", v] as [string, number]),
          ...(opts.tagGroups ?? []).map((g) => ["tag_groups", g.join(",")] as [string, string]),
          ...(opts.statuses ?? []).map((s) => ["status", s] as [string, string]),
        ],
      )}`,
    ),

  /** 流水线各状态的件数，用来判断「这批活儿跑完没」。 */
  queueStatus: () => request<QueueStatus>("/garments/queue-status"),

  /** 批量入队。返回实际入队的 id 与被跳过的（含理由）。 */
  batchProcess: (ids: number[]) =>
    request<BatchProcessResult>("/garments/batch/process", {
      method: "POST",
      body: JSON.stringify({ garment_ids: ids }),
    }),

  /**
   * 批量删除：一次删掉选中的衣物和它们的图片文件。
   * `cascadeOutfits` = 连同引用了它们的搭配一起删（默认只删衣物，保留搭配）。
   */
  batchDelete: (ids: number[], cascadeOutfits = false) =>
    request<BatchDeleteResult>("/garments/batch/delete", {
      method: "POST",
      body: JSON.stringify({ garment_ids: ids, cascade_outfits: cascadeOutfits }),
    }),

  /**
   * 批量改标签。mode：add 追加（默认）/ remove 移除 / replace 覆盖。
   */
  batchTags: (ids: number[], tagValueIds: number[], mode: "add" | "remove" | "replace" = "add") =>
    request<BatchTagsResult>("/garments/batch/tags", {
      method: "POST",
      body: JSON.stringify({ garment_ids: ids, tag_value_ids: tagValueIds, mode }),
    }),

  /**
   * 批量上传：**一张图一件衣物**。名称默认当前时间（客户端本地时间，
   * 避免服务器时区对不上），一批多张会自动加 (2) (3) 后缀。
   *
   * ⚠️ 内部按字节**分批串行**发（见 lib/upload.ts）。网关的 client_max_body_size
   * 是按单个请求算的，一次把几十张图塞进一个 FormData 会直接 413；分批后每批都
   * 远小于上限，而且失败时能说清「已经成功几张」。`start_index` 让服务端接着上一批
   * 的序号命名，不会出现两件都叫「今天 10:00 (2)」。
   */
  batchUpload: async (
    files: File[],
    opts: { name?: string; enqueue?: boolean; onProgress?: (p: UploadProgress) => void } = {},
  ): Promise<BatchUploadResult> => {
    const batches = splitFilesByBytes(files);
    const created: Garment[] = [];
    const skipped: BatchUploadResult["skipped"] = [];

    for (let i = 0; i < batches.length; i += 1) {
      opts.onProgress?.({
        batch: i + 1,
        batches: batches.length,
        done: created.length,
        total: files.length,
      });
      const form = new FormData();
      for (const f of batches[i]) form.append("files", f);
      if (opts.name) form.append("name", opts.name);
      if (opts.enqueue) form.append("enqueue", "true");
      // 0 不用传：老服务端没有这个字段，默认就是从头编号
      if (created.length > 0) form.append("start_index", String(created.length));

      try {
        const res = await request<BatchUploadResult>("/garments/batch/upload", {
          method: "POST",
          body: form,
        });
        created.push(...res.created);
        skipped.push(...res.skipped);
      } catch (e) {
        // 前面几批已经落库了，别让用户以为「全都没进去」；把进度写进报错文案
        if (created.length === 0) throw e;
        const reason = e instanceof ApiError ? e.message : "网络异常";
        throw new ApiError(
          e instanceof ApiError ? e.status : 0,
          `已成功上传 ${created.length} 张，第 ${i + 1}/${batches.length} 批失败：${reason}`,
        );
      }
    }

    return { created, skipped };
  },

  getGarment: (id: number) => request<Garment>(`/garments/${id}`),

  createGarment: (payload: { name: string; notes?: string | null; tag_value_ids?: number[] }) =>
    request<Garment>("/garments", { method: "POST", body: JSON.stringify(payload) }),

  updateGarment: (
    id: number,
    payload: { name?: string; notes?: string | null; tag_value_ids?: number[] },
  ) => request<Garment>(`/garments/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),

  /**
   * 删一件衣物。`cascadeOutfits` = 连同引用了它的搭配一起删。
   * 返回里带「牵动了哪些搭配」，前端据此如实告知用户。
   */
  deleteGarment: (id: number, cascadeOutfits = false) =>
    request<GarmentDeleteResult>(
      `/garments/${id}${cascadeOutfits ? "?cascade_outfits=true" : ""}`,
      { method: "DELETE" },
    ),

  /**
   * 给一件衣物补图。同样按字节分批——一次选十几张原图照样会撞上网关请求体上限。
   * 每批都会回整个衣物，分批时取最后一批的结果即可。
   */
  uploadImages: async (id: number, files: File[]): Promise<Garment> => {
    const batches = splitFilesByBytes(files);
    if (batches.length === 0) throw new ApiError(400, "未选择文件");

    let latest: Garment | null = null;
    for (const batch of batches) {
      const form = new FormData();
      for (const f of batch) form.append("files", f);
      latest = await request<Garment>(`/garments/${id}/images`, { method: "POST", body: form });
    }
    return latest as Garment;
  },

  setPrimaryImage: (garmentId: number, imageId: number) =>
    request<Garment>(`/garments/${garmentId}/images/${imageId}/primary`, { method: "POST" }),

  deleteImage: (garmentId: number, imageId: number) =>
    request<Garment>(`/garments/${garmentId}/images/${imageId}`, { method: "DELETE" }),

  // ---------- 批次 3：AI 流水线 ----------

  /** 丢回流水线等 Worker 处理。processing 中会返回 409。 */
  processGarment: (id: number) =>
    request<Garment>(`/garments/${id}/process`, { method: "POST" }),

  /**
   * 复核确认。**不带字段 = 全盘采纳 AI 建议**；传了值以用户为准。
   * 确认后状态落 ready，ai_suggestion 清空。
   */
  reviewGarment: (
    id: number,
    payload: { name?: string; notes?: string | null; tag_value_ids?: number[] } = {},
  ) =>
    request<Garment>(`/garments/${id}/review`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  /**
   * 框选区域重新抠一次（二次抠图）。坐标为**原图像素**。
   * 目标必须是 kind=cutout 的图；环境没装 rembg 会返回 400。
   */
  matteBox: (
    garmentId: number,
    imageId: number,
    box: { x: number; y: number; width: number; height: number; mode: "remove" | "add" },
  ) =>
    request<Garment>(`/garments/${garmentId}/images/${imageId}/matte-box`, {
      method: "POST",
      body: JSON.stringify(box),
    }),

  /** 把画笔编辑完毕的 PNG 整张替换上去。 */
  refineCutout: (garmentId: number, imageId: number, file: Blob) => {
    const form = new FormData();
    form.append("file", file, "cutout.png");
    return request<Garment>(`/garments/${garmentId}/images/${imageId}/refine`, {
      method: "POST",
      body: form,
    });
  },

  // ---------- 批次 4：搭配 ----------

  listOutfits: (opts: { q?: string; tagValueIds?: number[]; page?: number; pageSize?: number } = {}) =>
    request<OutfitList>(
      `/outfits${qs(
        { q: opts.q, page: opts.page, page_size: opts.pageSize },
        (opts.tagValueIds ?? []).map((v) => ["tag_value_ids", v] as [string, number]),
      )}`,
    ),

  getOutfit: (id: number) => request<Outfit>(`/outfits/${id}`),

  createOutfit: (payload: {
    name: string;
    notes?: string | null;
    background?: string;
    tag_value_ids?: number[];
    items?: OutfitItemIn[];
  }) => request<Outfit>("/outfits", { method: "POST", body: JSON.stringify(payload) }),

  /**
   * 改搭配。**items 传了就是整份替换图层**，不传表示本次不动画布。
   */
  updateOutfit: (
    id: number,
    payload: {
      name?: string;
      notes?: string | null;
      background?: string;
      tag_value_ids?: number[];
      items?: OutfitItemIn[];
    },
  ) => request<Outfit>(`/outfits/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),

  deleteOutfit: (id: number) => request<{ deleted_images: number }>(`/outfits/${id}`, { method: "DELETE" }),

  /**
   * 上传搭配的图。
   * - `result`：编辑器合成的搭配图，会替换掉上一张（只留最新的一张）
   * - `worn`：**上身图**（可选、可多张）
   */
  addOutfitImage: (id: number, file: Blob, kind: "result" | "worn" = "result") => {
    const form = new FormData();
    form.append("file", file, "outfit.png");
    form.append("kind", kind);
    return request<Outfit>(`/outfits/${id}/images`, { method: "POST", body: form });
  },

  setPrimaryOutfitImage: (outfitId: number, imageId: number) =>
    request<Outfit>(`/outfits/${outfitId}/images/${imageId}/primary`, { method: "POST" }),

  deleteOutfitImage: (outfitId: number, imageId: number) =>
    request<Outfit>(`/outfits/${outfitId}/images/${imageId}`, { method: "DELETE" }),

  /**
   * 哪些搭配用到了这些衣物（删衣物前提示用）。
   * 传数组可以一次问一批 —— 批量删除时不必为每件各发一次请求。
   */
  outfitsReferencing: (garmentIds: number | number[]) => {
    const ids = Array.isArray(garmentIds) ? garmentIds : [garmentIds];
    if (ids.length === 0) return Promise.resolve<OutfitRef[]>([]);
    return request<OutfitRef[]>(
      `/outfits/referencing${qs({}, ids.map((v) => ["garment_ids", v] as [string, number]))}`,
    );
  },

  /** 打标模型有没有配好。没配就把「AI 推荐」按钮置灰并说明原因。 */
  outfitAiStatus: () => request<OutfitAiStatus>("/outfits/ai/status"),

  /**
   * 让 AI 给这套搭配起名 + 挑标签。
   * @param canvas 现场合成的画布图；传了就以它为准（库里的成图可能是旧的）
   */
  suggestOutfit: (id: number, canvas?: Blob) => {
    const form = new FormData();
    if (canvas) form.append("file", canvas, "canvas.png");
    return request<OutfitSuggestion>(`/outfits/${id}/ai-suggest`, { method: "POST", body: form });
  },
};
