import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import {
  isWornImage,
  wardrobeApi,
  type Garment,
  type Outfit,
  type OutfitImage,
  type OutfitItem,
  type OutfitItemIn,
  type TagCategory,
} from "../api/wardrobe";
import Dialog from "./Dialog";
import { TagPickRow } from "./GarmentEditor";
import LibraryFilterDialog from "./LibraryFilterDialog";
import Lightbox from "./Lightbox";
import Thumb from "./Thumb";
import { confirmDialog } from "../lib/confirm";
import { buildFacetGroups, buildTagGroups } from "../lib/tags";

/** 导出成图的画布像素尺寸（3:4，与画布显示比例一致 —— 所见即所得）。 */
const EXPORT_W = 900;
const EXPORT_H = 1200;

const MIN_W = 0.05;
const MAX_W = 2;

/** 成图裁掉四周空白后，长边最多这么多像素；最多放大 2 倍，免得糊。 */
const OUT_LONG = 1200;
const OUT_MAX_SCALE = 2;

/** 编辑区画布显示大小的缩放倍数（用户可调），存在本地。 */
const ZOOM_KEY = "wardrobe.ocanvasZoom";
const ZOOM_MIN = 0.6;
const ZOOM_MAX = 1.6;

/** 素材库一次拉多少件。窄栏也放得下几十件，再多要靠搜索 / 筛选收窄。 */
const LIB_PAGE_SIZE = 60;

const BACKGROUNDS: [string, string][] = [
  ["#ffffff", "白"],
  ["#f4f1ec", "米"],
  ["#e9e4dc", "灰米"],
  ["#d9dee3", "浅灰"],
  ["#2f3237", "深灰"],
];

/** 一块像素矩形（原始图片像素坐标）。 */
type Rect = { x: number; y: number; w: number; h: number };

/** 量出来的素材信息：原始尺寸 + 该保留的区域。 */
type Measured = { nat: { w: number; h: number }; crop: Rect };

/** 编辑器里的图层（比服务端多一份本地状态：唯一的 key、显示名、裁边信息）。 */
type Layer = {
  key: string;
  garmentId: number | null;
  imageId: number | null;
  name: string;
  url: string;
  /** 素材原始像素尺寸（量到之后才有） */
  nat: { w: number; h: number } | null;
  /** 该显示的像素区域；null = 还没量到，先整张显示 */
  crop: Rect | null;
  /** 显示区域宽高比（w / h），DOM 与导出都靠它算高度 */
  ratio: number;
  x: number;
  y: number;
  w: number;
  rotation: number;
  flip: boolean;
  opacity: number;
  missing: boolean;
  /** 用户还没手动动过位置/大小：量完裁边后可以自动重新适配一次。 */
  auto: boolean;
  /** 自动排版时占的格子（归一化宽高）；手动摆过就是 null。 */
  cell: { cw: number; ch: number } | null;
};

let seq = 0;
const nextKey = () => `L${++seq}`;

function pickSource(g: Garment): { url: string; imageId: number; width: number; height: number } | null {
  /** 素材图：抠图优先（透明底才能叠在一起），没有抠图就退回原图。 */
  const cutout = [...g.images].reverse().find((i) => i.kind === "cutout");
  const img = cutout ?? g.images[0];
  if (!img) return null;
  return { url: img.original_url, imageId: img.id, width: img.width, height: img.height };
}

function toLayer(item: OutfitItem, fallbackName = "素材"): Layer {
  return {
    key: nextKey(),
    garmentId: item.garment_id,
    imageId: item.garment_image_id,
    name: item.garment_name || item.name_snapshot || fallbackName,
    url: item.image_url ?? "",
    nat: null,
    crop: null,
    ratio: item.height ? item.width / item.height : 1,
    x: item.x,
    y: item.y,
    w: item.w_ratio,
    rotation: item.rotation,
    flip: item.flip_h,
    opacity: item.opacity,
    missing: item.missing || !item.image_url,
    // 已存过的图层按库里的摆法显示，不自动动它
    auto: false,
    cell: null,
  };
}

function toItemIn(l: Layer): OutfitItemIn {
  return {
    garment_id: l.garmentId,
    garment_image_id: l.imageId,
    name_snapshot: l.name,
    x: l.x,
    y: l.y,
    w_ratio: l.w,
    rotation: l.rotation,
    flip_h: l.flip,
    opacity: l.opacity,
  };
}

// ---------- 素材量尺：算原始尺寸 + 要裁掉多少边 ----------

/** 透明到几乎看不见的像素才算「空气」；阈值压得很低，免得把衣服的软边一起裁掉。 */
const ALPHA_MIN = 4;
const WHITE_MIN = 246;
/** 识别用的缩略图边长：够看清边界，又不用把 4000px 的原图整块读进内存。 */
const PROBE = 512;

const measureCache = new Map<string, Promise<Measured>>();

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("图片加载失败"));
    img.src = url;
  });
}

/**
 * 沿着四周往里削「整行 / 整列都是纯白」的边。
 *
 * 只削最外圈连续的空白，削到有内容就停，且每边最多削掉 1/4 —— 白衬衫这类
 * 主体本身是白的图，不至于把衣服也啃掉一块。
 */
function trimSolidWhite(
  data: Uint8ClampedArray,
  w: number,
  box: Rect,
): Rect {
  let x0 = box.x;
  let y0 = box.y;
  let x1 = box.x + box.w - 1;
  let y1 = box.y + box.h - 1;

  const rowWhite = (y: number) => {
    let white = 0;
    const n = x1 - x0 + 1;
    for (let x = x0; x <= x1; x++) {
      const i = (y * w + x) * 4;
      if (data[i] >= WHITE_MIN && data[i + 1] >= WHITE_MIN && data[i + 2] >= WHITE_MIN) white++;
    }
    return n > 0 && white / n >= 0.995;
  };
  const colWhite = (x: number) => {
    let white = 0;
    const n = y1 - y0 + 1;
    for (let y = y0; y <= y1; y++) {
      const i = (y * w + x) * 4;
      if (data[i] >= WHITE_MIN && data[i + 1] >= WHITE_MIN && data[i + 2] >= WHITE_MIN) white++;
    }
    return n > 0 && white / n >= 0.995;
  };

  const capY = Math.max(1, Math.floor(box.h * 0.25));
  const capX = Math.max(1, Math.floor(box.w * 0.25));

  for (let k = 0; k < capY && y0 < y1 && rowWhite(y0); k++) y0++;
  for (let k = 0; k < capY && y0 < y1 && rowWhite(y1); k++) y1--;
  for (let k = 0; k < capX && x0 < x1 && colWhite(x0); k++) x0++;
  for (let k = 0; k < capX && x0 < x1 && colWhite(x1); k++) x1--;

  const w2 = x1 - x0 + 1;
  const h2 = y1 - y0 + 1;
  if (w2 < 8 || h2 < 8) return box;
  return { x: x0, y: y0, w: w2, h: h2 };
}

/** 从像素里找「该保留的区域」：先按透明边裁，没有透明边再削纯白边。 */
function detectCrop(data: Uint8ClampedArray, w: number, h: number): Rect {
  let hasAlpha = false;
  let minX = w;
  let minY = h;
  let maxX = -1;
  let maxY = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      const a = data[(y * w + x) * 4 + 3];
      if (a < 250) hasAlpha = true;
      if (a <= ALPHA_MIN) continue;
      if (x < minX) minX = x;
      if (x > maxX) maxX = x;
      if (y < minY) minY = y;
      if (y > maxY) maxY = y;
    }
  }
  if (maxX < 0) return { x: 0, y: 0, w, h }; // 整张全透明，别乱裁

  let box: Rect = { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
  if (!hasAlpha) box = trimSolidWhite(data, w, box);

  // 留 1% 余量，免得抗锯齿的半透明边被切掉一圈
  const padX = Math.max(1, Math.round(box.w * 0.01));
  const padY = Math.max(1, Math.round(box.h * 0.01));
  const x = Math.max(0, box.x - padX);
  const y = Math.max(0, box.y - padY);
  return {
    x,
    y,
    w: Math.min(w - x, box.w + padX * 2),
    h: Math.min(h - y, box.h + padY * 2),
  };
}

async function measure(url: string): Promise<Measured> {
  const hit = measureCache.get(url);
  if (hit) return hit;
  const task = (async () => {
    const img = await loadImage(url);
    const w = img.naturalWidth || 1;
    const h = img.naturalHeight || 1;
    let crop: Rect = { x: 0, y: 0, w, h };
    try {
      const scale = Math.min(1, PROBE / Math.max(w, h));
      const dw = Math.max(1, Math.round(w * scale));
      const dh = Math.max(1, Math.round(h * scale));
      const canvas = document.createElement("canvas");
      canvas.width = dw;
      canvas.height = dh;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (ctx) {
        ctx.drawImage(img, 0, 0, dw, dh);
        const small = detectCrop(ctx.getImageData(0, 0, dw, dh).data, dw, dh);
        if (small.w < dw || small.h < dh) {
          // 放大回原图坐标：起点向下取整、终点向上取整，宁可多留一点边
          const x = Math.max(0, Math.floor(small.x / scale));
          const y = Math.max(0, Math.floor(small.y / scale));
          const x2 = Math.min(w, Math.ceil((small.x + small.w) / scale));
          const y2 = Math.min(h, Math.ceil((small.y + small.h) / scale));
          crop = { x, y, w: x2 - x, h: y2 - y };
        }
      }
    } catch {
      /* 读像素失败（跨域等）：老老实实整张显示 */
    }
    return { nat: { w, h }, crop };
  })();
  measureCache.set(url, task);
  task.catch(() => measureCache.delete(url));
  return task;
}

/** 图层在画布上的外接框（归一化坐标，0~1 = 整张画布），旋转也考虑进去。 */
function boxSize(ratio: number, w: number, rotation = 0) {
  const nh = (w / Math.max(0.01, ratio)) * (EXPORT_W / EXPORT_H);
  const rad = (rotation * Math.PI) / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  return {
    w: w * cos + nh * sin,
    h: w * sin + nh * cos,
  };
}

function layerBox(l: Layer) {
  return boxSize(l.ratio, l.w, l.rotation);
}

/**
 * 新图层怎么摆：一件就居中占满，多了按 2 列铺格子（细长的裙子靠高度吃尺寸，
 * 两列比两行更能用满画布）。每件都按格子缩到装得下 —— 既不会被画布边缘切掉，
 * 也不会整件压在上一件身上。
 */
const CELL_FILL = 0.9;

function autoCell(i: number, n: number) {
  const cols = n <= 1 ? 1 : 2;
  const rows = Math.max(1, Math.ceil(n / cols));
  return {
    x: ((i % cols) + 0.5) / cols,
    y: (Math.floor(i / cols) + 0.5) / rows,
    cw: 1 / cols,
    ch: 1 / rows,
  };
}

/** 在这个格子里，按宽高比给一个尽量大的尺寸。 */
function cellWidth(ratio: number, cw: number, ch: number) {
  const r = Math.max(0.01, ratio);
  const byHeight = (ch * CELL_FILL * r) / (EXPORT_W / EXPORT_H);
  return Math.max(MIN_W, Math.min(cw * CELL_FILL, byHeight));
}

/** 图层露在画布外的比例（0 = 完整在画布里）。 */
function overflowPct(l: Layer) {
  const b = boxSize(l.ratio, l.w, l.rotation);
  const out = (c: number, half: number) =>
    Math.max(0, half - c) + Math.max(0, c + half - 1);
  return {
    x: b.w > 0 ? out(l.x, b.w / 2) / b.w : 0,
    y: b.h > 0 ? out(l.y, b.h / 2) / b.h : 0,
  };
}

/** 把图层中心收进画布，至少不会整件被推出去。 */
const clampCenter = (v: number) => Math.min(1, Math.max(0, v));

/**
 * 成图该保留哪块画布 —— 图层实际占的地方，四周留一点余量。
 *
 * 整张 3:4 画布直接导出去，一个居中的人像会顶出大片底色；裁到内容框后
 * 卡片的示意图和缩略图就都是「有内容的」，不再是白边里找衣服。
 */
function contentBox(layers: Layer[], pad = 0.02): Rect {
  let minX = 1;
  let minY = 1;
  let maxX = 0;
  let maxY = 0;
  for (const l of layers) {
    const b = layerBox(l);
    const hw = Math.min(0.5, b.w / 2);
    const hh = Math.min(0.5, b.h / 2);
    minX = Math.min(minX, l.x - hw);
    maxX = Math.max(maxX, l.x + hw);
    minY = Math.min(minY, l.y - hh);
    maxY = Math.max(maxY, l.y + hh);
  }
  if (maxX <= minX || maxY <= minY) return { x: 0, y: 0, w: 1, h: 1 };
  const x = Math.max(0, minX - pad);
  const y = Math.max(0, minY - pad);
  const w = Math.max(0.12, Math.min(1 - x, maxX - minX + pad * 2));
  const h = Math.max(0.12, Math.min(1 - y, maxY - minY + pad * 2));
  return { x, y, w, h };
}

/** 把图层画到一张离屏 canvas 上 —— 保存时合成成图（按内容裁边）。 */
async function render(layers: Layer[], background: string): Promise<Blob | null> {
  const box = contentBox(layers);
  // 内容框在成图坐标系里的像素尺寸 → 缩放到长边 1200（最多放大 2 倍，别把糊图放大）
  const pxW = Math.max(1, box.w * EXPORT_W);
  const pxH = Math.max(1, box.h * EXPORT_H);
  const k = Math.min(OUT_LONG / Math.max(pxW, pxH), OUT_MAX_SCALE);
  const outW = Math.max(240, Math.round(pxW * k));
  const outH = Math.max(240, Math.round(pxH * k));

  const canvas = document.createElement("canvas");
  canvas.width = outW;
  canvas.height = outH;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;

  ctx.fillStyle = background;
  ctx.fillRect(0, 0, outW, outH);
  // 之后所有坐标都按「成图坐标系」下笔，剩下的是平移 + 缩放
  ctx.setTransform(k, 0, 0, k, -box.x * EXPORT_W * k, -box.y * EXPORT_H * k);

  const images = await Promise.all(
    layers.map(
      (l) =>
        new Promise<HTMLImageElement | null>((resolve) => {
          if (!l.url) return resolve(null);
          const img = new Image();
          img.onload = () => resolve(img);
          img.onerror = () => resolve(null);
          img.src = l.url;
        }),
    ),
  );

  layers.forEach((l, i) => {
    const img = images[i];
    if (!img) return;
    const c = l.crop ?? { x: 0, y: 0, w: img.naturalWidth, h: img.naturalHeight };
    const w = l.w * EXPORT_W;
    const h = w / l.ratio;
    ctx.save();
    ctx.globalAlpha = l.opacity;
    ctx.translate(l.x * EXPORT_W, l.y * EXPORT_H);
    ctx.rotate((l.rotation * Math.PI) / 180);
    if (l.flip) ctx.scale(-1, 1);
    ctx.drawImage(img, c.x, c.y, c.w, c.h, -w / 2, -h / 2, w, h);
    ctx.restore();
  });

  return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/png"));
}

/** 把还没被手动动过的图层重新铺一遍格子：每件都完整露出来，不整件压在一起。 */
function arrangeAutos(layers: Layer[]): Layer[] {
  const n = layers.length;
  if (n === 0) return layers;
  return layers.map((l, i) => {
    if (!l.auto) return l;
    const cell = autoCell(i, n);
    return {
      ...l,
      cell: { cw: cell.cw, ch: cell.ch },
      x: cell.x,
      y: cell.y,
      w: cellWidth(l.ratio, cell.cw, cell.ch),
    };
  });
}

type Props = {
  outfit: Outfit | null;
  categories: TagCategory[];
  onClose: () => void;
  onSaved: (outfit: Outfit) => void;
};

export default function OutfitEditor({ outfit, categories, onClose, onSaved }: Props) {
  const [name, setName] = useState(outfit?.name ?? "未命名搭配");
  const [notes, setNotes] = useState(outfit?.notes ?? "");
  const [background, setBackground] = useState(outfit?.background || "#ffffff");
  const [tagIds, setTagIds] = useState<Set<number>>(new Set(outfit?.tag_value_ids ?? []));
  const [layers, setLayers] = useState<Layer[]>(() =>
    (outfit?.items ?? []).map((i) => toLayer(i)),
  );
  const [selected, setSelected] = useState<string | null>(null);

  const [garments, setGarments] = useState<Garment[]>([]);
  /** 命中的总数可能超过一屏拉取的上限，超了给一句提示（数据是真的多时才知道要收窄）。 */
  const [libTotal, setLibTotal] = useState(0);
  const [q, setQ] = useState("");
  /** 素材库的标签筛选：选中的取值 id（弹窗里改，点「应用」才写回）。 */
  const [libFilterIds, setLibFilterIds] = useState<number[]>([]);
  const [libFilterOpen, setLibFilterOpen] = useState(false);
  const [libCats, setLibCats] = useState<TagCategory[]>([]);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  /** 新建的搭配在「AI 推荐」时会被先存一次，记住 id，别再建第二套。 */
  const [savedId, setSavedId] = useState<number | null>(null);
  const outfitId = outfit?.id ?? savedId;

  const [aiAvailable, setAiAvailable] = useState<boolean | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiNote, setAiNote] = useState<string | null>(null);

  /** 标签类别的本地副本：就地新建取值后要把新 chip 补进去，不能等整页刷新。 */
  const [cats, setCats] = useState<TagCategory[]>(categories);
  useEffect(() => setCats(categories), [categories]);

  const canvasRef = useRef<HTMLDivElement>(null);

  /**
   * 上身图（可选）：真人穿搭照，与画布合成出来的搭配图分开存。
   * 摆的是「这套衣服怎么搭」，上身图记的是「穿上去什么样」—— 一张都不传也完全成立。
   */
  const [worn, setWorn] = useState<OutfitImage[]>(() =>
    (outfit?.images ?? []).filter((i) => isWornImage(i.kind)),
  );
  const [wornBusy, setWornBusy] = useState(false);
  const [wornView, setWornView] = useState<number | null>(null);
  const wornInput = useRef<HTMLInputElement>(null);

  /**
   * 画布在编辑区里显示多大（相对「自适应」尺寸的倍数）。
   * 自适应 = 按视口高度顶满，尽量占住屏幕主体；调小是为了同时看清旁边两栏。
   */
  const [zoom, setZoom] = useState(() => {
    const v = Number(localStorage.getItem(ZOOM_KEY));
    return Number.isFinite(v) && v >= ZOOM_MIN && v <= ZOOM_MAX ? v : 1;
  });
  const applyZoom = (v: number) => {
    const next = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(v * 20) / 20));
    setZoom(next);
    try {
      localStorage.setItem(ZOOM_KEY, String(next));
    } catch {
      /* 隐私模式下写不进去，不影响这一次 */
    }
  };

  useEffect(() => {
    wardrobeApi.outfitAiStatus().then((s) => setAiAvailable(s.available)).catch(() => setAiAvailable(false));
  }, []);

  /**
   * 素材库筛选栏用的标签：**衣物**范围（编辑器里传入的 categories 是「搭配标签」的，
   * 两套标签互不相干，不能混用）。
   */
  useEffect(() => {
    wardrobeApi
      .listCategories("garment")
      .then(setLibCats)
      .catch(() => setLibCats([]));
  }, []);

  /** 勾中的标签 → 后端 tag_groups（维度内 OR、维度间 AND）。 */
  const libTagGroups = useMemo(
    () => buildTagGroups(buildFacetGroups(libCats, libFilterIds)),
    [libCats, libFilterIds],
  );

  const loadGarments = useCallback(async () => {
    try {
      const res = await wardrobeApi.listGarments({
        q: q.trim() || undefined,
        tagGroups: libTagGroups,
        pageSize: LIB_PAGE_SIZE,
      });
      setGarments(res.items.filter((g) => g.images.length > 0));
      setLibTotal(res.total);
    } catch {
      /* 素材库拉不到不影响摆搭配 */
    }
  }, [q, libTagGroups]);

  useEffect(() => {
    const t = setTimeout(loadGarments, 250);
    return () => clearTimeout(t);
  }, [loadGarments]);

  const patch = (key: string, next: Partial<Layer>) =>
    setLayers((prev) => prev.map((l) => (l.key === key ? { ...l, ...next } : l)));

  const addLayer = (g: Garment) => {
    const src = pickSource(g);
    if (!src) return;
    setLayers((prev) => {
      const ratio = src.height ? src.width / src.height : 1;
      return arrangeAutos([
        // 后加的在上层
        ...prev,
        {
          key: nextKey(),
          garmentId: g.id,
          imageId: src.imageId,
          name: g.name,
          url: src.url,
          nat: null,
          crop: null,
          ratio,
          x: 0.5,
          y: 0.5,
          w: cellWidth(ratio, 1, 1),
          rotation: 0,
          flip: false,
          opacity: 1,
          missing: false,
          auto: true,
          cell: null,
        },
      ]);
    });
    setSelected(null);
  };

  /**
   * 量素材：原始尺寸 + 该裁掉多少透明/白边。
   *
   * 抠图是「原图尺寸 + 透明底」，主体常常只占中间一小块；不裁的话图层盒子里
   * 大半是空气，摆件既看不准、成图也一堆白边。量到后同步改 ratio —— 高度跟着
   * 内容走。URL 级缓存，同一件素材只算一次。
   *
   * 量出来才知道真实的宽高比，所以要按裁后的比例重新适配一次尺寸（用户还没动
   * 过这个图层时才动它），否则细长的裙子会顶出画布。
   */
  useEffect(() => {
    const todo = layers.filter((l) => !l.missing && l.url && !l.nat);
    if (todo.length === 0) return;
    let alive = true;
    void Promise.all(
      todo.map(async (l) => [l.key, await measure(l.url).catch(() => null)] as const),
    ).then((pairs) => {
      if (!alive) return;
      setLayers((prev) =>
        prev.map((l) => {
          if (l.nat) return l;
          const hit = pairs.find(([k]) => k === l.key)?.[1];
          if (!hit) return l;
          const ratio = hit.crop.w / hit.crop.h;
          if (!l.auto) return { ...l, nat: hit.nat, crop: hit.crop, ratio };
          // 量出来才知道真实比例，按它重新适配到自己的格子里
          const cell = l.cell ?? { cw: 1, ch: 1 };
          const w = cellWidth(ratio, cell.cw, cell.ch);
          const bh = boxSize(ratio, w).h;
          return {
            ...l,
            nat: hit.nat,
            crop: hit.crop,
            ratio,
            w,
            y: Math.min(1 - bh / 2, Math.max(bh / 2, l.y)),
          };
        }),
      );
    });
    return () => {
      alive = false;
    };
  }, [layers]);

  // ---------- 画布手势 ----------

  const startDrag = (e: React.PointerEvent, key: string) => {
    e.stopPropagation();
    const layer = layers.find((l) => l.key === key);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!layer || !rect) return;
    setSelected(key);

    const startX = e.clientX;
    const startY = e.clientY;
    const originX = layer.x;
    const originY = layer.y;

    const move = (ev: PointerEvent) => {
      const dx = (ev.clientX - startX) / rect.width;
      const dy = (ev.clientY - startY) / rect.height;
      // 中心收在画布内：可以露出去一点，但不会整件被推没
      patch(key, {
        x: clampCenter(originX + dx),
        y: clampCenter(originY + dy),
        auto: false,
      });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const startScale = (e: React.PointerEvent, key: string) => {
    e.stopPropagation();
    const layer = layers.find((l) => l.key === key);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!layer || !rect) return;

    const cx = rect.left + layer.x * rect.width;
    const cy = rect.top + layer.y * rect.height;
    // 以中心为原点，按指针到中心的距离等比缩放
    const d0 = Math.hypot(e.clientX - cx, e.clientY - cy) || 1;
    const w0 = layer.w;

    const move = (ev: PointerEvent) => {
      const d = Math.hypot(ev.clientX - cx, ev.clientY - cy);
      patch(key, { w: Math.min(MAX_W, Math.max(MIN_W, (w0 * d) / d0)), auto: false });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  const startRotate = (e: React.PointerEvent, key: string) => {
    e.stopPropagation();
    const layer = layers.find((l) => l.key === key);
    const rect = canvasRef.current?.getBoundingClientRect();
    if (!layer || !rect) return;

    const cx = rect.left + layer.x * rect.width;
    const cy = rect.top + layer.y * rect.height;
    const a0 = Math.atan2(e.clientY - cy, e.clientX - cx);
    const r0 = layer.rotation;

    const move = (ev: PointerEvent) => {
      const a = Math.atan2(ev.clientY - cy, ev.clientX - cx);
      patch(key, { rotation: r0 + ((a - a0) * 180) / Math.PI, auto: false });
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  // ---------- 图层操作 ----------

  const reorder = (key: string, dir: "top" | "up" | "down" | "bottom") => {
    setLayers((prev) => {
      const i = prev.findIndex((l) => l.key === key);
      if (i < 0) return prev;
      const next = [...prev];
      const [item] = next.splice(i, 1);
      const target =
        dir === "top" ? next.length : dir === "bottom" ? 0 : dir === "up" ? i + 1 : i - 1;
      next.splice(Math.max(0, Math.min(next.length, target)), 0, item);
      return next;
    });
  };

  const removeLayer = (key: string) => {
    setLayers((prev) => prev.filter((l) => l.key !== key));
    setSelected((cur) => (cur === key ? null : cur));
  };

  /** 把图层重新收进画布：按它当前的宽高比给一个刚好装得下的大小，摆回中间。 */
  const fitToCanvas = (key: string) => {
    const l = layers.find((x) => x.key === key);
    if (!l) return;
    patch(key, { w: cellWidth(l.ratio, 1, 1), x: 0.5, y: 0.5, auto: false, cell: null });
  };

  const current = layers.find((l) => l.key === selected) ?? null;
  const currentOverflow = current && !current.missing ? overflowPct(current) : null;

  // ---------- 保存 ----------

  /** 落库 + 合成成图。不关弹窗 —— 「AI 推荐」也要先走这一步拿 id 和成图。 */
  const persist = async (): Promise<Outfit | null> => {
    const items = layers.filter((l) => !l.missing).map(toItemIn);
    const payload = {
      name: name.trim() || "未命名搭配",
      notes: notes || null,
      background,
      tag_value_ids: [...tagIds],
      items,
    };
    const saved = outfitId
      ? await wardrobeApi.updateOutfit(outfitId, payload)
      : await wardrobeApi.createOutfit(payload);
    setSavedId(saved.id);

    // 合成成图：没有可画的图层就不上传，免得存一堆白板
    if (items.length) {
      const blob = await render(layers.filter((l) => !l.missing), background);
      if (blob) await wardrobeApi.addOutfitImage(saved.id, blob, "result");
    }
    return saved;
  };

  // ---------- 上身图（可选） ----------

  const syncWorn = (saved: Outfit) =>
    setWorn(saved.images.filter((i) => isWornImage(i.kind)));

  /** 传上身图。搭配还没落库就先存一次 —— 图总得挂在某一套搭配上。 */
  const uploadWorn = async (files: File[]) => {
    if (files.length === 0) return;
    setWornBusy(true);
    setError(null);
    try {
      let id = outfitId;
      if (!id) {
        const created = await persist();
        if (!created) return;
        id = created.id;
      }
      let last: Outfit | null = null;
      for (const f of files) last = await wardrobeApi.addOutfitImage(id, f, "worn");
      if (last) syncWorn(last);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "上传上身图失败");
    } finally {
      setWornBusy(false);
      // 清掉 input 的值，否则同一张图第二次选不会触发 change
      if (wornInput.current) wornInput.current.value = "";
    }
  };

  const removeWorn = async (image: OutfitImage) => {
    if (!outfitId) return;
    const ok = await confirmDialog({
      title: "删除这张上身图？",
      message: "删除后这套搭配的搭配图不受影响，此操作无法撤销。",
      confirmLabel: "删除",
      danger: true,
    });
    if (!ok) return;
    setError(null);
    try {
      syncWorn(await wardrobeApi.deleteOutfitImage(outfitId, image.id));
      setWornView(null);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "删除失败");
    }
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const saved = await persist();
      if (saved) onSaved(saved);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  /** AI 起名 + 挑标签。只填进表单，用户看过再自己保存。 */
  const suggest = async () => {
    if (layers.filter((l) => !l.missing).length === 0) {
      setAiNote("先往画布上摆几件，AI 得看着图才说得准");
      return;
    }
    setAiBusy(true);
    setAiNote(null);
    setError(null);
    try {
      // 还没保存过的搭配先存一次：推荐接口认 outfit_id
      let id = outfitId;
      if (!id) {
        const saved = await persist();
        if (!saved) return;
        id = saved.id;
      }
      // 把**当前画布**传给后端 —— 库里的成图可能是上次保存的旧样子
      const canvas = (await render(layers.filter((l) => !l.missing), background)) ?? undefined;
      const res = await wardrobeApi.suggestOutfit(id, canvas);
      if (res.error) {
        setAiNote(res.error);
        return;
      }
      if (res.name) setName(res.name);
      if (res.tag_value_ids.length) setTagIds(new Set(res.tag_value_ids));
      const bits = [res.name ? `名字：${res.name}` : null];
      if (res.tag_value_ids.length) {
        bits.push(`标签：${res.tags.map((t) => t.value).join("、")}`);
      }
      if (res.confidence) bits.push(`把握 ${Math.round(res.confidence * 100)}%`);
      if (res.hint) bits.push(res.hint);
      setAiNote(`${bits.filter(Boolean).join(" · ")}（可直接改，保存后才生效）`);
    } catch (exc) {
      setAiNote(exc instanceof Error ? exc.message : "AI 推荐失败");
    } finally {
      setAiBusy(false);
    }
  };

  const toggleTag = (id: number) =>
    setTagIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  /** 就地给某个搭配标签类别补一个取值，并顺手选上（保存搭配时才落库）。 */
  const createTagValue = async (cat: TagCategory, value: string) => {
    try {
      const created = await wardrobeApi.createValue(cat.id, value);
      setCats((prev) =>
        prev.map((c) => (c.id === cat.id ? { ...c, values: [...c.values, created] } : c)),
      );
      setTagIds((prev) => new Set(prev).add(created.id));
    } catch (exc) {
      // 重名（409）不算错：本来就是想选它，直接选上
      const dup = cat.values.find((v) => v.value.trim() === value.trim());
      if (dup) {
        setTagIds((prev) => new Set(prev).add(dup.id));
        return;
      }
      setError(exc instanceof Error ? exc.message : "新增标签失败");
    }
  };

  const orderedLayers = useMemo(() => [...layers].reverse(), [layers]);

  return (
    <Dialog
      title={outfit ? "编辑搭配" : "新建搭配"}
      onClose={onClose}
      width={1240}
      className="modal-outfit"
      footer={
        <>
          {error && <span className="tiny" style={{ color: "var(--danger)" }}>{error}</span>}
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>
            {saving ? "保存中…" : "保存搭配"}
          </button>
        </>
      }
    >
      <div className="oeditor">
        {/* 左：素材库 */}
        <aside className="oeditor-lib">
          <div className="olib-head">
            <h4>素材库</h4>
            <span className="olib-filter-wrap">
              <button
                type="button"
                className={`btn btn-sm olib-filter${libFilterIds.length > 0 ? " btn-on" : ""}`}
                title="按标签筛选素材"
                onClick={() => setLibFilterOpen(true)}
              >
                筛选
                {libFilterIds.length > 0 && (
                  <span className="olib-filter-n">{libFilterIds.length}</span>
                )}
              </button>
              {libFilterIds.length > 0 && (
                <button
                  type="button"
                  className="btn btn-sm olib-filter-clear"
                  title={`清空标签筛选（已选 ${libFilterIds.length} 项）`}
                  aria-label="清空标签筛选"
                  onClick={() => setLibFilterIds([])}
                >
                  ✕
                </button>
              )}
            </span>
          </div>
          <input
            className="input"
            placeholder="搜衣物名"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <div className="olib-grid">
            {garments.map((g) => {
              const src = pickSource(g);
              return (
                <button
                  type="button"
                  key={g.id}
                  className="olib-cell"
                  title={g.name}
                  onClick={() => src && addLayer(g)}
                >
                  <Thumb src={src?.url ?? g.cover_url ?? ""} alt={g.name} fallback="无图" />
                  <span className="olib-name">{g.name}</span>
                  {!g.images.some((i) => i.kind === "cutout") && (
                    <span className="olib-flag">未抠图</span>
                  )}
                </button>
              );
            })}
          </div>
          {garments.length === 0 && (
            <p className="tiny olib-empty">
              {libFilterIds.length > 0 || q.trim() ? "没有符合条件的素材" : "没有可用素材（先把衣物抠好图）"}
              {libFilterIds.length > 0 && (
                <button type="button" className="link-btn" onClick={() => setLibFilterIds([])}>
                  清空筛选
                </button>
              )}
            </p>
          )}
          {/*
            只有**被翻页截断**时才值得提一句。命中数比列表长不等于截断 ——
            没有图片的衣物本来就会被过滤掉（它也没法摆到画布上）。
          */}
          {libTotal > LIB_PAGE_SIZE && (
            <p className="tiny olib-more" title="用上面的搜索框或筛选把范围收小">
              仅显示前 {LIB_PAGE_SIZE} 件（共 {libTotal} 件）
            </p>
          )}
        </aside>

        {/* 中：画布 */}
        <section className="oeditor-canvas">
          <div className="ocanvas-tools">
            <span className="tiny">底色</span>
            {BACKGROUNDS.map(([hex, label]) => (
              <button
                type="button"
                key={hex}
                className={`oswatch${background === hex ? " on" : ""}`}
                style={{ background: hex }}
                title={label}
                onClick={() => setBackground(hex)}
              />
            ))}
            <label className="ozoom" title="调整画布在编辑区里的大小">
              <span className="tiny">画布大小</span>
              <input
                type="range"
                min={ZOOM_MIN}
                max={ZOOM_MAX}
                step={0.05}
                value={zoom}
                onChange={(e) => applyZoom(Number(e.target.value))}
              />
              <span className="tiny ozoom-pct">{Math.round(zoom * 100)}%</span>
              {zoom !== 1 && (
                <button type="button" className="btn tiny-btn" onClick={() => applyZoom(1)}>
                  自适应
                </button>
              )}
            </label>
            <span className="tiny ocanvas-hint">点图层选中 · 拖动手柄缩放 / 旋转</span>
          </div>

          <div
            className="ocanvas"
            ref={canvasRef}
            style={{ background, "--ocanvas-zoom": zoom } as CSSProperties}
            title="点素材选中 · 拖动移动 · 手柄缩放 / 旋转"
            onPointerDown={() => setSelected(null)}
          >
            {layers.map((l) => {
              if (l.missing) {
                return (
                  <div
                    key={l.key}
                    className="olayer omissing"
                    style={{
                      left: `${l.x * 100}%`,
                      top: `${l.y * 100}%`,
                      width: `${l.w * 100}%`,
                      transform: `translate(-50%, -50%) rotate(${l.rotation}deg)`,
                    }}
                  >
                    素材已删除
                  </div>
                );
              }
              // 只显示裁出来的那块内容：图放大到 (原图 / 裁剪) 倍，再挪到窗口外
              const view = l.crop ?? (l.nat ? { x: 0, y: 0, w: l.nat.w, h: l.nat.h } : null);
              const zoom =
                view && l.nat
                  ? {
                      width: `${(l.nat.w / view.w) * 100}%`,
                      left: `${(-view.x / view.w) * 100}%`,
                      top: `${(-view.y / view.h) * 100}%`,
                    }
                  : null;
              return (
                <div
                  key={l.key}
                  className={`olayer${selected === l.key ? " on" : ""}`}
                  style={{
                    left: `${l.x * 100}%`,
                    top: `${l.y * 100}%`,
                    width: `${l.w * 100}%`,
                    opacity: l.opacity,
                    transform: `translate(-50%, -50%) rotate(${l.rotation}deg) scaleX(${l.flip ? -1 : 1})`,
                  }}
                  onPointerDown={(e) => startDrag(e, l.key)}
                >
                  <div
                    className="olayer-img"
                    /* 没量出裁边前也要有个比例，否则绝对定位的图会把盒子撑成 0 高 */
                    style={{ aspectRatio: view ? `${view.w} / ${view.h}` : `${l.ratio}` }}
                    data-crop={l.nat ? "1" : "0"}
                  >
                    <img
                      src={l.url}
                      alt={l.name}
                      draggable={false}
                      style={zoom ?? { width: "100%" }}
                    />
                  </div>
                  {selected === l.key && (
                    <>
                      <span
                        className="ohandle orotate"
                        title="拖动旋转"
                        onPointerDown={(e) => startRotate(e, l.key)}
                      />
                      <span
                        className="ohandle oscale"
                        title="拖动缩放"
                        onPointerDown={(e) => startScale(e, l.key)}
                      />
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        {/* 右：属性 + 图层 */}
        <aside className="oeditor-side">
          <label className="field">
            <span>名称</span>
            <div className="field-ai">
              <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              <button
                type="button"
                className="btn tiny-btn"
                onClick={suggest}
                disabled={aiBusy || aiAvailable === false}
                title={
                  aiAvailable === false
                    ? "还没配打标模型：去「设置 → 大模型」填一个能看图的视觉模型"
                    : "让 AI 看着画布起名并挑标签"
                }
              >
                {aiBusy ? "推荐中…" : "AI 推荐"}
              </button>
            </div>
            {aiNote && <span className="tiny ai-note">{aiNote}</span>}
          </label>
          <label className="field">
            <span>备注</span>
            <textarea
              className="input"
              rows={2}
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
            />
          </label>

          <h4>
            搭配标签 <span className="tiny">（点一下选中 / 取消 · 缺什么直接 + 取值）</span>
          </h4>
          {cats.length === 0 && <p className="tiny">（标签管理里还没有搭配侧类别）</p>}
          {cats.map((cat) => (
            <TagPickRow
              key={cat.id}
              cat={cat}
              categories={cats}
              selected={tagIds}
              onToggle={toggleTag}
              onCreateValue={createTagValue}
            />
          ))}

          <h4>
            图层 <span className="tiny">（上=顶层）</span>
          </h4>
          <ul className="olayers">
            {orderedLayers.map((l) => (
              <li
                key={l.key}
                className={`olayer-row${selected === l.key ? " on" : ""}`}
                onClick={() => setSelected(l.key)}
              >
                <span className="olayer-name" title={l.name}>
                  {l.missing ? `${l.name}（素材已删除）` : l.name}
                </span>
                <span className="olayer-ops">
                  <button type="button" title="置顶" onClick={() => reorder(l.key, "top")}>
                    ⤒
                  </button>
                  <button type="button" title="上移" onClick={() => reorder(l.key, "up")}>
                    ↑
                  </button>
                  <button type="button" title="下移" onClick={() => reorder(l.key, "down")}>
                    ↓
                  </button>
                  <button type="button" title="置底" onClick={() => reorder(l.key, "bottom")}>
                    ⤓
                  </button>
                  <button type="button" title="删除" onClick={() => removeLayer(l.key)}>
                    ✕
                  </button>
                </span>
              </li>
            ))}
            {orderedLayers.length === 0 && <li className="tiny">还没有图层，从左边素材库点一件</li>}
          </ul>

          {/* 单层微调放在图层列表下面：贴着「选中的是哪一层」，也不占画布的高度 */}
          {current && !current.missing ? (
            <div className="oprop">
              <label>
                <span className="oprop-k">缩放</span>
                <input
                  type="range"
                  min={MIN_W}
                  max={MAX_W}
                  step={0.01}
                  value={current.w}
                  onChange={(e) => patch(current.key, { w: Number(e.target.value), auto: false })}
                />
              </label>
              <label>
                <span className="oprop-k">旋转</span>
                <input
                  type="range"
                  min={-180}
                  max={180}
                  step={1}
                  value={Math.round(current.rotation)}
                  onChange={(e) =>
                    patch(current.key, { rotation: Number(e.target.value), auto: false })
                  }
                />
                <span className="tiny oprop-v">{Math.round(current.rotation)}°</span>
              </label>
              <label>
                <span className="oprop-k">透明</span>
                <input
                  type="range"
                  min={0.2}
                  max={1}
                  step={0.05}
                  value={current.opacity}
                  onChange={(e) => patch(current.key, { opacity: Number(e.target.value) })}
                />
              </label>
              <div className="oprop-btns">
                <button
                  type="button"
                  className="btn tiny-btn"
                  onClick={() => patch(current.key, { flip: !current.flip })}
                >
                  {current.flip ? "取消镜像" : "镜像"}
                </button>
                <button
                  type="button"
                  className="btn tiny-btn"
                  title="按当前比例缩到刚好装进画布并居中"
                  onClick={() => fitToCanvas(current.key)}
                >
                  适应画布
                </button>
                <button
                  type="button"
                  className="btn tiny-btn"
                  onClick={() => patch(current.key, { rotation: 0, flip: false, opacity: 1 })}
                >
                  复位
                </button>
              </div>
              {currentOverflow && Math.max(currentOverflow.x, currentOverflow.y) > 0.001 && (
                <span className="tiny ooverflow" role="status">
                  有 {Math.round(Math.max(currentOverflow.x, currentOverflow.y) * 100)}% 露在画布外
                  （会被切掉）
                </span>
              )}
            </div>
          ) : (
            <p className="tiny oprop-tip">点画布上的素材选中它，再在这里微调</p>
          )}

          {/* 上身图：可选。摆的是「怎么搭」，穿上是「穿上去什么样」，分开存 */}
          <div className="oworn">
            <h4>
              上身图 <span className="tiny">（可选 · 想记录实际穿上的样子就传）</span>
            </h4>
            {worn.length > 0 ? (
              <div className="oworn-grid">
                {worn.map((img, i) => (
                  <div className="oworn-cell" key={img.id}>
                    <button
                      type="button"
                      className="oworn-img"
                      title="点开看大图"
                      onClick={() => setWornView(i)}
                    >
                      <Thumb src={img.thumb_medium_url} alt="上身图" fallback="图丢失" />
                    </button>
                    <button
                      type="button"
                      className="oworn-del"
                      title="删除这张上身图"
                      aria-label="删除这张上身图"
                      onClick={() => removeWorn(img)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              </div>
            ) : (
              <p className="tiny oworn-empty">还没传上身图 —— 不传也完全没问题。</p>
            )}
            <input
              ref={wornInput}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={(e) => uploadWorn(Array.from(e.target.files ?? []))}
            />
            <button
              type="button"
              className="btn tiny-btn oworn-add"
              disabled={wornBusy}
              onClick={() => wornInput.current?.click()}
            >
              {wornBusy ? "上传中…" : "＋ 上传上身图"}
            </button>
          </div>
        </aside>
      </div>

      {wornView !== null && worn[wornView] && (
        <Lightbox
          items={worn.map((img) => ({
            key: `worn-${img.id}`,
            url: img.original_url,
            label: "上身图",
            caption: `${img.width}×${img.height}`,
          }))}
          index={wornView}
          onClose={() => setWornView(null)}
          onIndexChange={setWornView}
        />
      )}
      {libFilterOpen && (
        <LibraryFilterDialog
          categories={libCats}
          value={libFilterIds}
          onApply={(ids) => {
            setLibFilterIds(ids);
            setLibFilterOpen(false);
          }}
          onClose={() => setLibFilterOpen(false)}
        />
      )}
    </Dialog>
  );
}
