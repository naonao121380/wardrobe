/**
 * 衣物 / 标签接口。
 *
 * 标签分两套作用域（scope）：
 * - `garment`：大类 / 小类 / 颜色 / 材质
 * - `outfit` ：季节 / 风格 / 色系（批次 4 使用）
 * 两者在服务端同表不同 scope，互不可见。
 */

import { request } from "./client";

export type TagScope = "garment" | "outfit";

export type TagValue = {
  id: number;
  value: string;
  sort_order: number;
};

export type TagCategory = {
  id: number;
  scope: TagScope;
  name: string;
  sort_order: number;
  values: TagValue[];
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
};

export type GarmentList = {
  items: Garment[];
  total: number;
  page: number;
  page_size: number;
};

function qs(params: Record<string, string | number | undefined>, list?: [string, number][]): string {
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

  createCategory: (scope: TagScope, name: string) =>
    request<TagCategory>("/tags/categories", {
      method: "POST",
      body: JSON.stringify({ scope, name }),
    }),

  renameCategory: (categoryId: number, name: string) =>
    request<TagCategory>(`/tags/categories/${categoryId}`, {
      method: "PATCH",
      body: JSON.stringify({ name }),
    }),

  deleteCategory: (categoryId: number) =>
    request<{ deleted_values: number }>(`/tags/categories/${categoryId}`, { method: "DELETE" }),

  createValue: (categoryId: number, value: string) =>
    request<TagValue>(`/tags/categories/${categoryId}/values`, {
      method: "POST",
      body: JSON.stringify({ value }),
    }),

  renameValue: (valueId: number, value: string) =>
    request<TagValue>(`/tags/values/${valueId}`, {
      method: "PATCH",
      body: JSON.stringify({ value }),
    }),

  deleteValue: (valueId: number) =>
    request<{ untagged: number }>(`/tags/values/${valueId}`, { method: "DELETE" }),

  // ---------- 衣物 ----------

  listGarments: (opts: { q?: string; tagValueIds?: number[]; page?: number; pageSize?: number } = {}) =>
    request<GarmentList>(
      `/garments${qs(
        { q: opts.q, page: opts.page, page_size: opts.pageSize },
        (opts.tagValueIds ?? []).map((v) => ["tag_value_ids", v] as [string, number]),
      )}`,
    ),

  getGarment: (id: number) => request<Garment>(`/garments/${id}`),

  createGarment: (payload: { name: string; notes?: string | null; tag_value_ids?: number[] }) =>
    request<Garment>("/garments", { method: "POST", body: JSON.stringify(payload) }),

  updateGarment: (
    id: number,
    payload: { name?: string; notes?: string | null; tag_value_ids?: number[] },
  ) => request<Garment>(`/garments/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),

  deleteGarment: (id: number) =>
    request<{ deleted_images: number }>(`/garments/${id}`, { method: "DELETE" }),

  uploadImages: (id: number, files: File[]) => {
    const form = new FormData();
    for (const f of files) form.append("files", f);
    return request<Garment>(`/garments/${id}/images`, { method: "POST", body: form });
  },

  setPrimaryImage: (garmentId: number, imageId: number) =>
    request<Garment>(`/garments/${garmentId}/images/${imageId}/primary`, { method: "POST" }),

  deleteImage: (garmentId: number, imageId: number) =>
    request<Garment>(`/garments/${garmentId}/images/${imageId}`, { method: "DELETE" }),
};
