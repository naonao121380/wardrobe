/**
 * 抠图编辑器：给 AI 抠图的结果做第二次修整。
 *
 * 为什么需要它：通用抠图模型一次抠干净是奢望 —— 衣架、吊牌、地上的影子，或者袖子
 * 跟背景颜色接近时被一起吃掉，都很常见。与其跟模型较劲，不如让用户补两笔。
 *
 * 四种手段：
 * 1. **擦除笔** —— 抹掉残留（衣架、标签、背景碎片）
 * 2. **恢复笔** —— 把原图的像素整块贴回来（颜色 + 不透明度都取自原图）。
 *    不取抠图图层的原因：抠图开了 alpha matting，边缘要羽化，估算前景色时会把
 *    边缘像素带偏（黑边变白边就是这么来的），沿用抠图的像素等于把错色补回去。
 *    也不沿用抠图的 alpha：羽化边缘是半透明的，只换颜色不换透明度的话涂完还是
 *    一层淡色，肉眼看不出变化。既然是「恢复」，就回到照片本来的样子。
 *    想退回抠图那个版本用「撤销」。
 *    另外，整笔范围内一点不透明像素都没有时（远离主体的空白处）**不动笔**，
 *    避免手一抖把一整块背景涂回来。
 * 3. **框选重抠** —— 框一块区域丢回模型重跑。crop 之后分辨率上去了、背景也单纯了，
 *    通常比整图重跑准得多。
 * 4. **缩放平移** —— 笔刷半径是按**图像像素**算的，整图缩进一屏时最小的一笔也有
 *    好几个像素宽，领口那条一两像素的白边根本点不准。放大到 1:1 甚至 400% 再涂，
 *    笔才落得到想去的那个像素上。见下面「视口」那段注释。
 *
 * ---------- 怎么走到「想改的那一块」（用户反馈驱动，2026-10-16）----------
 *
 * 原来只有「空格 + 拖」和「鼠标中键拖」两种平移方式，都藏在提示文字里；而 `+` / 滑杆
 * 缩放**永远锚定舞台正中** —— 于是「一放大就只剩图像中心，够不到要修的地方」。
 * 补了四条路，都不需要先读说明：
 *
 * a. **抓手工具**（工具栏第 4 个）—— 选中后左键直接拖画面。最像常识的那条路。
 * b. **滚轮 = 缩放**，且**以光标为锚点** —— 把指针放到要修的地方再滚，那块就留在原地
 *    长大。不再要求按住 Ctrl。（Shift + 滚轮放行给浏览器，用来滚弹窗。）
 * c. **导航器（左上角小地图）** —— 放大时出现，显示整张图 + 当前视口方框；点哪儿跳哪儿，
 *    也可以拖方框扫。回答「我现在看的是整张图的哪个位置」。
 * d. **方向键微调平移**（Shift 加速）—— 贴着边缘修的时候比拖鼠标稳。
 *
 * `+` / `−` / 滑杆的锚点也从「舞台正中」改成「鼠标最后停留的位置」（`lastAnchorRef`）：
 * 指针停在裙摆上点 `+`，长出来的就是裙摆，而不是又一次居中。
 *
 * 撤销 / 重做走**笔画重放**而不是像素快照：快照要存 12 份全图，手机上都得几百 MB。
 * 这里只记「哪些点、什么模式、多大笔刷」，撤销 = 从 base 重建后重放剩下的笔画。
 *
 * 画布按原始分辨率（长边上限 MAX_SIDE）建立，显示时用 CSS 缩放，这样保存出去的
 * PNG 不会丢画质。
 *
 * ---------- 视口（缩放 / 平移）实现要点 ----------
 *
 * 舞台 `.refine-stage` 是个**固定高度的视口**（overflow:hidden），里面一层
 * `.refine-view` 是「照片本身」，尺寸用 px 显式写成 `画布尺寸 × 像素比`，
 * 平移则用 `translate` 从居中位置推开。
 *
 * 缩放的口径是**像素比**：100% = 一个图像像素对一个屏幕像素（1:1），
 * 适应窗口时通常只有 20% 出头。滑杆、百分比、笔刷光圈都用这一把尺子，
 * 不用「相对适应窗口的倍数」—— 那样拖到 100% 反而不是 1:1，修边的时候容易看错。
 *
 * 为什么不用 `transform: scale()` 做缩放：`.refine-view` 里还挂着框选的那个虚线框，
 * 它是按**百分比**定位的；用 transform 的话框的百分比基准不会跟着变，得另外换算一遍。
 * 直接改宽高，框的百分比天然就是相对「照片」的，代码里只剩「照片多大」一件事。
 *
 * 光标 → 图像像素的换算仍然走 `canvas.getBoundingClientRect()`：宽高是显式设的、
 * 没有 transform 里的 scale，所以 rect 就精确等于「照片」的可见框，除一下即可。
 *
 * 小坑：浏览器给**滚轮事件**的 `clientX/Y` 是整数（真实鼠标位置本来也是整数），
 * 所以锚点就按上报的坐标算 —— 拿 `getBoundingClientRect` 里的小数位置去推期望值，
 * 会看到每步 1px 的「假漂移」，其实底层像素钉得死死的。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { wardrobeApi, type Garment, type GarmentImage } from "../api/wardrobe";
import Dialog from "./Dialog";

type Tool = "erase" | "restore" | "box" | "pan";

/** 连续的一笔：同一工具、同一半径下划过的所有圆点。 */
type Stroke = {
  erase: boolean;
  radius: number;
  points: { x: number; y: number }[];
};

type Surface = {
  width: number;
  height: number;
  /** 画布当前内容（内存里的权威副本） */
  out: ImageData;
  /** 进入编辑器时的抠图 RGBA：撤销重放的起点；恢复笔只借用它的 alpha */
  base: Uint8ClampedArray;
  /** 原图 RGBA：恢复笔的颜色来源 —— 抠图图层被 alpha matting 带偏过，不能用 */
  source: Uint8ClampedArray;
  /** 当前 alpha，与 out 同步维护 */
  alpha: Uint8ClampedArray;
  /** 画布坐标 → 原图坐标的倍率 */
  toNatural: number;
};

type Size = { w: number; h: number };
type Point = { x: number; y: number };

const MAX_SIDE = 2200;
const HISTORY_LIMIT = 12;

/** 适应窗口时四周留出的空隙，别贴边。 */
const STAGE_PAD = 10;
/** 按钮 / 滚轮每一步的倍率。 */
const ZOOM_STEP = 1.4;
/** 缩放上限的兜底：像素比不超过 8（8 倍像素看织纹够用了）。 */
const MAX_SCALE_CAP = 8;

/** 导航器（左上角小地图）的外框上限，px。够看清位置就行，不抢画面。 */
const NAV_MAX_W = 104;
const NAV_MAX_H = 84;
/** 方向键平移的步长（px，舞台坐标）；按住 Shift 加速。 */
const KEY_PAN_STEP = 24;
const KEY_PAN_STEP_FAST = 120;

type Props = {
  garment: Garment;
  image: GarmentImage;
  onClose: () => void;
  onSaved: (garment: Garment) => void;
};

export default function CutoutEditor({ garment, image, onClose, onSaved }: Props) {
  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const navCanvasRef = useRef<HTMLCanvasElement>(null);
  const surfaceRef = useRef<Surface | null>(null);
  const strokesRef = useRef<Stroke[]>([]);
  const redoRef = useRef<Stroke[]>([]);
  const drawingRef = useRef<Stroke | null>(null);
  /**
   * 鼠标最后停在**画面上**（不含缩放条 / 导航器）的位置，舞台坐标。
   * `+` / `−` / 滑杆缩放以它为锚点 —— 否则不管鼠标在哪，一放大就又是图像正中。
   */
  const lastAnchorRef = useRef<Point | null>(null);
  const navDragRef = useRef(false);

  const [tool, setTool] = useState<Tool>("erase");
  const [radius, setRadius] = useState(28);
  const [boxMode, setBoxMode] = useState<"remove" | "add">("remove");
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [info, setInfo] = useState<string>("");
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [canvasSize, setCanvasSize] = useState({ w: 0, h: 0 });
  const [hist, setHist] = useState({ undo: 0, redo: 0 });
  /** 手动 +1 触发重新载入（框选重抠 / 保存之后 base 要换成新结果） */
  const [reloadKey, setReloadKey] = useState(0);

  // ---------- 视口状态 ----------
  const [stage, setStage] = useState<Size>({ w: 0, h: 0 });
  /**
   * 缩放口径：**一个图像像素占几个屏幕像素**（100% = 1:1）。
   * 用「实际像素比」而不是「相对适应窗口的倍数」，是因为这里的活儿就是贴着像素修边；
   * 而且百分比和滑杆能线性对应上，拖到 100% 就是 1:1，所见即所标。
   * `null` = 跟随「适应窗口」：窗户尺寸一变，照片自己重新铺满。
   */
  const [scalePx, setScalePx] = useState<number | null>(null);
  /** 相对居中位置推开多少 px */
  const [pan, setPan] = useState<Point>({ x: 0, y: 0 });
  /** 鼠标在舞台里的位置（画笔刷光圈用） */
  const [hover, setHover] = useState<Point | null>(null);
  const [spaceHeld, setSpaceHeld] = useState(false);
  const [panning, setPanning] = useState(false);

  /** 舞台尺寸：适应窗口的计算基准（ResizeObserver 报的是布局尺寸，不受缩放影响）。 */
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const measure = () => {
      const r = el.getBoundingClientRect();
      setStage((prev) =>
        Math.abs(prev.w - r.width) < 0.5 && Math.abs(prev.h - r.height) < 0.5
          ? prev
          : { w: r.width, h: r.height },
      );
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  /** 适应窗口时照片的显示尺寸（不放大：小图就按原始像素显示）。 */
  const baseSize = useMemo(
    () => fitInto(canvasSize, { w: stage.w - STAGE_PAD * 2, h: stage.h - STAGE_PAD * 2 }),
    [canvasSize, stage],
  );
  /** 「适应窗口」对应的像素比 —— 缩放的下限，再往外缩就没有意义了。 */
  const fitScale = canvasSize.w > 0 && baseSize.w > 0 ? baseSize.w / canvasSize.w : 1;
  const scale = scalePx ?? fitScale;
  const dispSize: Size = useMemo(
    () => ({ w: canvasSize.w * scale, h: canvasSize.h * scale }),
    [canvasSize, scale],
  );
  /** 缩放上限：至少能放到 200%，最多 800%（1:1 再往上 8 倍，看织纹、看毛边都够了）。 */
  const maxScale = Math.min(MAX_SCALE_CAP, Math.max(2, fitScale * 8));
  /** 显示尺寸是 px 显式算出来的，笔刷光圈、框选框都靠它换算。 */
  const pxPerImage = scale;

  // 窗口变大 / 缩放上限变小以后，把显式设过的缩放收回可放范围
  useEffect(() => {
    if (scalePx !== null && (scalePx > maxScale || scalePx < fitScale)) {
      setScalePx(clamp(scalePx, fitScale, maxScale));
    }
  }, [scalePx, maxScale, fitScale]);

  // 平移越界（窗口变小 / 缩回去）时收回边界内
  useEffect(() => {
    setPan((p) => {
      const c = clampPan(p, dispSize, stage);
      return c.x === p.x && c.y === p.y ? p : c;
    });
  }, [dispSize, stage]);

  // 手势里要读「当前」的缩放/平移，闭包会过期，用 ref 跟一份
  const viewRef = useRef({ scale, pan, disp: dispSize });
  useEffect(() => {
    viewRef.current = { scale, pan, disp: dispSize };
  }, [scale, pan, dispSize]);
  const spaceRef = useRef(false);

  // ---------- 导航器（小地图） ----------
  /** 把主画布缩绘到导航器上（每次 paint 之后跟着刷一次，缩略图始终是当前结果）。 */
  const drawNav = useCallback(() => {
    const main = canvasRef.current;
    const nav = navCanvasRef.current;
    if (!main || !nav || !nav.width || !nav.height) return;
    const ctx = nav.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, nav.width, nav.height);
    ctx.drawImage(main, 0, 0, nav.width, nav.height);
  }, []);

  /** 导航器尺寸：整张图等比缩进 NAV_MAX_* 里。 */
  const navBox: Size = useMemo(() => {
    if (!canvasSize.w || !canvasSize.h) return { w: 0, h: 0 };
    const s = Math.min(NAV_MAX_W / canvasSize.w, NAV_MAX_H / canvasSize.h);
    return { w: Math.max(32, canvasSize.w * s), h: Math.max(32, canvasSize.h * s) };
  }, [canvasSize]);

  /** 只在「放大到看不全」时出现 —— 适应窗口时视口就是整张图，摆着只挡画面。 */
  const showNav = ready && navBox.w > 0 && scale > fitScale + 0.002;

  /** 当前视口方框在导航器里的位置（图像像素 → 导航器坐标）。 */
  const navView = useMemo(() => {
    if (!showNav || !stage.w || !stage.h || !canvasSize.w) return null;
    const dispW = canvasSize.w * scale;
    const dispH = canvasSize.h * scale;
    const left = -((stage.w - dispW) / 2 + pan.x) / scale;
    const top = -((stage.h - dispH) / 2 + pan.y) / scale;
    const x0 = clamp(left, 0, canvasSize.w);
    const y0 = clamp(top, 0, canvasSize.h);
    const x1 = clamp(left + stage.w / scale, 0, canvasSize.w);
    const y1 = clamp(top + stage.h / scale, 0, canvasSize.h);
    if (x1 <= x0 || y1 <= y0) return null;
    return {
      left: (x0 / canvasSize.w) * navBox.w,
      top: (y0 / canvasSize.h) * navBox.h,
      width: Math.max(6, ((x1 - x0) / canvasSize.w) * navBox.w),
      height: Math.max(6, ((y1 - y0) / canvasSize.h) * navBox.h),
    };
  }, [showNav, stage, canvasSize, scale, pan, navBox]);

  // 导航器画布尺寸（按 DPR 放大，缩略图才不糊）；尺寸一变就补画一次。
  // ★ 依赖里必须有 showNav：适应窗口时导航器根本没渲染（navCanvasRef 是 null），
  //   放大后才挂上去 —— 不跟着 showNav 重跑，画布会一直停在 0×0，缩略图永远是空的。
  useEffect(() => {
    const nav = navCanvasRef.current;
    if (!nav || !navBox.w || !navBox.h) return;
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    nav.width = Math.round(navBox.w * dpr);
    nav.height = Math.round(navBox.h * dpr);
    drawNav();
  }, [navBox, drawNav, canvasSize, showNav]);

  const paint = useCallback((rect?: { x: number; y: number; w: number; h: number }) => {
    const surface = surfaceRef.current;
    const canvas = canvasRef.current;
    if (!surface || !canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const { out, alpha, width, height } = surface;
    if (rect) {
      const x0 = Math.max(0, Math.floor(rect.x));
      const y0 = Math.max(0, Math.floor(rect.y));
      const x1 = Math.min(width, Math.ceil(rect.x + rect.w));
      const y1 = Math.min(height, Math.ceil(rect.y + rect.h));
      if (x1 <= x0 || y1 <= y0) return;
      for (let y = y0; y < y1; y += 1) {
        let idx = y * width + x0;
        for (let x = x0; x < x1; x += 1, idx += 1) {
          out.data[idx * 4 + 3] = alpha[idx];
        }
      }
      // dirty 矩形是「源坐标」，目标位置 = dx + dirtyX —— dx 取 0 才能对上
      ctx.putImageData(out, 0, 0, x0, y0, x1 - x0, y1 - y0);
    } else {
      for (let i = 0, n = width * height; i < n; i += 1) out.data[i * 4 + 3] = alpha[i];
      ctx.putImageData(out, 0, 0);
    }
    drawNav();
  }, [drawNav]);

  // ---------- 载入：抠图结果 + 原图 ----------
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setErr(null);
    setBox(null);
    strokesRef.current = [];
    redoRef.current = [];
    setHist({ undo: 0, redo: 0 });

    const cutoutUrl = image.original_url;
    const originalUrl = pickOriginalUrl(garment) ?? cutoutUrl;

    void (async () => {
      try {
        const cutoutImg = await loadImage(cutoutUrl);
        // 原图只是补像素用的，取不到就退回抠图自身（此时恢复笔等于纯撤销擦除）
        const originalImg = await loadImage(originalUrl).catch(() => cutoutImg);
        if (cancelled) return;
        const canvas = canvasRef.current;
        if (!canvas) return;

        const scale = Math.min(
          1,
          MAX_SIDE / Math.max(cutoutImg.naturalWidth, cutoutImg.naturalHeight),
        );
        const width = Math.max(1, Math.round(cutoutImg.naturalWidth * scale));
        const height = Math.max(1, Math.round(cutoutImg.naturalHeight * scale));
        canvas.width = width;
        canvas.height = height;

        const cutout = rasterize(cutoutImg, width, height);
        const source =
          originalImg === cutoutImg ? cutout : rasterize(originalImg, width, height);

        const base = cutout.data.slice();
        const out = new ImageData(new Uint8ClampedArray(base), width, height);
        const alpha = new Uint8ClampedArray(width * height);
        for (let i = 0, n = alpha.length; i < n; i += 1) alpha[i] = base[i * 4 + 3];

        surfaceRef.current = {
          width,
          height,
          out,
          base,
          source: source.data,
          alpha,
          toNatural: 1 / scale,
        };
        setCanvasSize({ w: width, h: height });
        setReady(true);
        setLoading(false);
        setInfo(
          originalImg === cutoutImg
            ? `编辑分辨率 ${width}×${height}（没找到原图，恢复笔只能沿用抠图的像素）`
            : `编辑分辨率 ${width}×${height}`,
        );
        paint();
      } catch {
        if (!cancelled) {
          setLoading(false);
          setErr("抠图加载失败，请刷新后重试");
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [image.original_url, garment.id, reloadKey, paint]);

  // ---------- 视口操作 ----------
  /** 舞台中心（客户端坐标），按钮 / 滑杆缩放以它为锚点。 */
  const stageCenter = () => {
    const el = stageRef.current;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2, r };
  };

  /**
   * 以屏幕某点为锚点缩放：锚点底下的那个像素**不动**。
   *
   * 位置关系（`view` 居中，再沿 translate 推开 pan）：屏幕上 = 舞台中心 + pan + (锚点在照片里的比例 − 0.5) × 显示尺寸。
   * 缩放前后各写一次、令两式相等，就解出新的 pan。
   *
   * 参数 `next` 是**像素比**（1 = 1:1），不是相对适应窗口的倍数。
   */
  const zoomAt = useCallback(
    (next: number, clientX: number, clientY: number) => {
      const el = stageRef.current;
      const v = viewRef.current;
      if (!el || !canvasSize.w || !canvasSize.h) return;
      const r = el.getBoundingClientRect();
      const ns = clamp(next, fitScale, maxScale);
      const nw = canvasSize.w * ns;
      const nh = canvasSize.h * ns;
      const mx = clientX - r.left;
      const my = clientY - r.top;
      // 锚点在照片里的比例位置（可能落在照片外的留白上，超出 0~1 也无所谓）
      const u = v.disp.w ? (mx - (r.width - v.disp.w) / 2 - v.pan.x) / v.disp.w : 0.5;
      const w = v.disp.h ? (my - (r.height - v.disp.h) / 2 - v.pan.y) / v.disp.h : 0.5;
      setScalePx(ns);
      setPan(
        clampPan(
          { x: mx - r.width / 2 + nw * (0.5 - u), y: my - r.height / 2 + nh * (0.5 - w) },
          { w: nw, h: nh },
          { w: r.width, h: r.height },
        ),
      );
    },
    [canvasSize, fitScale, maxScale],
  );

  /**
   * 按钮 / 滑杆用：以「鼠标最后停在画面上的位置」为锚点缩放。
   *
   * 原来固定锚舞台正中 —— 指针停在裙摆上点 `+`，长出来的却是图像中心，于是
   * 「一放大就只剩中心点」，够不到要修的那块。指针没落在画面上时（刚进编辑器、
   * 或一直停在缩放条 / 导航器上）才退回舞台中心。
   */
  const zoomTo = useCallback(
    (next: number) => {
      const r = stageRef.current?.getBoundingClientRect();
      const a = lastAnchorRef.current;
      if (r && a) {
        zoomAt(next, r.left + a.x, r.top + a.y);
        return;
      }
      const c = stageCenter();
      if (c) zoomAt(next, c.x, c.y);
    },
    [zoomAt],
  );

  /** 回到「适应窗口」：整张图一屏装下、居中，并跟随窗口尺寸。 */
  const fitView = useCallback(() => {
    setScalePx(null);
    setPan({ x: 0, y: 0 });
  }, []);

  /**
   * 导航器点击 / 拖动：把图像上的这个比例位置挪到舞台正中。
   *
   * 就是 `zoomAt` 里那条位置关系的反解 —— 想让比例位置 f 落在舞台中心，
   * 平移量 = `disp × (0.5 − f)`（推导见 `zoomAt` 的注释）。
   */
  const navJumpTo = useCallback((fx: number, fy: number) => {
    const r = stageRef.current?.getBoundingClientRect();
    const v = viewRef.current;
    if (!r || !v.disp.w || !v.disp.h) return;
    setPan(
      clampPan(
        { x: v.disp.w * (0.5 - fx), y: v.disp.h * (0.5 - fy) },
        v.disp,
        { w: r.width, h: r.height },
      ),
    );
  }, []);

  const onNavPointer = (event: React.PointerEvent<HTMLDivElement>) => {
    const r = event.currentTarget.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return;
    navJumpTo(
      clamp((event.clientX - r.left) / r.width, 0, 1),
      clamp((event.clientY - r.top) / r.height, 0, 1),
    );
  };

  /**
   * 滚轮 = 缩放，**以光标为锚点**（触控板捏合带 ctrlKey，一并吃下）。
   *
   * 原来要求先按住 Ctrl/⌘，普通滚轮放行给弹窗。但这里的活儿就是「把指针放到要修的
   * 地方、放大、下笔」——多一个修饰键就多一道坎，而弹窗本身几乎不需要滚。现在直接滚。
   * 留 Shift + 滚轮放行给浏览器，需要滚弹窗时还有条路。
   */
  useEffect(() => {
    const el = stageRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      if (!ready || busy) return;
      if (event.shiftKey) return; // 让浏览器滚弹窗
      event.preventDefault();
      zoomAt(viewRef.current.scale * Math.exp(-event.deltaY * 0.0022), event.clientX, event.clientY);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [ready, busy, zoomAt]);

  // 空格 = 临时抓手（Photoshop 那套习惯）
  useEffect(() => {
    const set = (on: boolean) => {
      spaceRef.current = on;
      setSpaceHeld(on);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "Space") return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      e.preventDefault(); // 免得空格顺手把焦点上的按钮按了
      set(e.type === "keydown");
    };
    const clear = () => set(false);
    window.addEventListener("keydown", onKey);
    window.addEventListener("keyup", onKey);
    window.addEventListener("blur", clear);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("keyup", onKey);
      window.removeEventListener("blur", clear);
    };
  }, []);

  // 方向键微调平移：贴着边缘修的时候比拖鼠标稳；按住 Shift 加速
  useEffect(() => {
    if (!ready || busy) return;
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
      const step = e.shiftKey ? KEY_PAN_STEP_FAST : KEY_PAN_STEP;
      let dx = 0;
      let dy = 0;
      if (e.key === "ArrowLeft") dx = step;
      else if (e.key === "ArrowRight") dx = -step;
      else if (e.key === "ArrowUp") dy = step;
      else if (e.key === "ArrowDown") dy = -step;
      else return;
      e.preventDefault();
      setPan((p) => {
        const r = stageRef.current?.getBoundingClientRect();
        return clampPan(
          { x: p.x + dx, y: p.y + dy },
          viewRef.current.disp,
          { w: r?.width ?? 0, h: r?.height ?? 0 },
        );
      });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, busy]);

  // ---------- 多指手势（捏合缩放 / 双指拖动） ----------
  const pointersRef = useRef(new Map<number, Point>());
  const panDragRef = useRef<{ sx: number; sy: number; pan: Point } | null>(null);
  const pinchRef = useRef<{ d0: number; zoom0: number; u: number; v: number } | null>(null);

  /** 两指捏合：距离变化改缩放，中点位移顺带就是平移。 */
  const applyPinch = () => {
    const el = stageRef.current;
    const pin = pinchRef.current;
    const pts = [...pointersRef.current.values()];
    if (!el || !pin || pts.length < 2 || !canvasSize.w || !canvasSize.h) return;
    const [a, b] = pts;
    const r = el.getBoundingClientRect();
    const raw = (pin.zoom0 * Math.hypot(a.x - b.x, a.y - b.y)) / pin.d0;
    const nz = clamp(raw, fitScale, maxScale);
    const nw = canvasSize.w * nz;
    const nh = canvasSize.h * nz;
    const mx = (a.x + b.x) / 2 - r.left;
    const my = (a.y + b.y) / 2 - r.top;
    setScalePx(nz);
    setPan(
      clampPan(
        { x: mx - r.width / 2 + nw * (0.5 - pin.u), y: my - r.height / 2 + nh * (0.5 - pin.v) },
        { w: nw, h: nh },
        { w: r.width, h: r.height },
      ),
    );
  };

  // ---------- 画笔 ----------
  /** 在内存里盖一个圆笔（不负责刷新，重放时批量调用）。 */
  const stampAt = useCallback(
    (
      surface: Surface,
      cx: number,
      cy: number,
      erase: boolean,
      r: number,
    ): { x: number; y: number; w: number; h: number } | null => {
      const { width, height, alpha, base, source, out } = surface;
      const x0 = Math.max(0, Math.floor(cx - r));
      const y0 = Math.max(0, Math.floor(cy - r));
      const x1 = Math.min(width, Math.ceil(cx + r));
      const y1 = Math.min(height, Math.ceil(cy + r));
      if (x1 <= x0 || y1 <= y0) return null;
      const rr = r * r;

      // 恢复笔的「邻近保护」：整笔范围内一点不透明像素都没有，就当涂在空背景上，
      // 不动笔 —— 否则一挥就把半张背景墙补回来了。
      let nearOpaque = erase;
      if (!nearOpaque) {
        scan: for (let y = y0; y < y1; y += 1) {
          for (let x = x0; x < x1; x += 1) {
            const dx = x - cx;
            const dy = y - cy;
            if (dx * dx + dy * dy > rr) continue;
            if (base[(y * width + x) * 4 + 3] > 0 || alpha[y * width + x] > 0) {
              nearOpaque = true;
              break scan;
            }
          }
        }
      }
      if (!nearOpaque) return null;

      for (let y = y0; y < y1; y += 1) {
        for (let x = x0; x < x1; x += 1) {
          const dx = x - cx;
          const dy = y - cy;
          if (dx * dx + dy * dy > rr) continue;
          const idx = y * width + x;
          const o = idx * 4;
          if (erase) {
            alpha[idx] = 0;
            out.data[o + 3] = 0;
          } else {
            // 恢复 = 把原图这一块的像素整块贴回来（含不透明度）。
            //
            // 为什么不沿用抠图的 alpha：抠图的羽化边缘是半透明的，只换 RGB 不换
            // alpha 的话，涂完还是一层半透明的淡色，肉眼几乎看不出变化 —— 用户
            // 只会觉得「恢复笔没生效」。既然点的是「恢复」，就该回到照片本来的
            // 样子：原图是不透明的，贴回来就是不透明的。
            out.data[o] = source[o];
            out.data[o + 1] = source[o + 1];
            out.data[o + 2] = source[o + 2];
            const a = source[o + 3] > 0 ? source[o + 3] : 255;
            out.data[o + 3] = a;
            alpha[idx] = a;
          }
        }
      }
      return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
    },
    [],
  );

  const stamp = useCallback(
    (cx: number, cy: number, erase: boolean) => {
      const surface = surfaceRef.current;
      if (!surface) return;
      const rect = stampAt(surface, cx, cy, erase, radius);
      if (rect) paint(rect);
    },
    [paint, radius, stampAt],
  );

  /** 撤销 / 重做：回到基线，把还有效的笔画重放一遍。 */
  const replay = useCallback(() => {
    const surface = surfaceRef.current;
    if (!surface) return;
    const { out, base, alpha } = surface;
    // 先整体回到「进入编辑器时」那张图，再把还生效的笔画盖回去
    out.data.set(base);
    for (let i = 0, n = alpha.length; i < n; i += 1) alpha[i] = base[i * 4 + 3];
    for (const stroke of strokesRef.current) {
      for (const point of stroke.points) {
        stampAt(surface, point.x, point.y, stroke.erase, stroke.radius);
      }
    }
    setHist({ undo: strokesRef.current.length, redo: redoRef.current.length });
    paint();
  }, [paint, stampAt]);

  const undo = useCallback(() => {
    const stroke = strokesRef.current.pop();
    if (!stroke) return;
    redoRef.current.push(stroke);
    replay();
  }, [replay]);

  const redo = useCallback(() => {
    const stroke = redoRef.current.pop();
    if (!stroke) return;
    strokesRef.current.push(stroke);
    replay();
  }, [replay]);

  /** 客户端坐标 → 画布（图像）像素。用了显式宽高、没有 transform 缩放，rect 就是照片的框。 */
  const imagePoint = (clientX: number, clientY: number): Point => {
    const canvas = canvasRef.current;
    if (!canvas) return { x: 0, y: 0 };
    const r = canvas.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return { x: 0, y: 0 };
    return {
      x: ((clientX - r.left) / r.width) * canvas.width,
      y: ((clientY - r.top) / r.height) * canvas.height,
    };
  };

  const inImage = (p: Point) =>
    p.x >= 0 && p.y >= 0 && p.x <= canvasSize.w && p.y <= canvasSize.h;

  const onPointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!ready || busy) return;
    // 悬浮的缩放条 / 导航器不参与画布交互
    if ((event.target as HTMLElement | null)?.closest?.(".refine-zoom, .refine-nav")) return;
    pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });

    // 两指 = 捏合缩放 + 平移，不画（点下去那一笔撤掉，免得留个点）
    if (pointersRef.current.size === 2) {
      if (drawingRef.current) {
        drawingRef.current = null;
        replay();
      }
      panDragRef.current = null;
      setPanning(false);
      const [a, b] = [...pointersRef.current.values()];
      const r = stageRef.current?.getBoundingClientRect();
      const v = viewRef.current;
      if (!r || !v.disp.w || !v.disp.h) return;
      const mx = (a.x + b.x) / 2 - r.left;
      const my = (a.y + b.y) / 2 - r.top;
      pinchRef.current = {
        d0: Math.max(1, Math.hypot(a.x - b.x, a.y - b.y)),
        zoom0: v.scale,
        u: (mx - (r.width - v.disp.w) / 2 - v.pan.x) / v.disp.w,
        v: (my - (r.height - v.disp.h) / 2 - v.pan.y) / v.disp.h,
      };
      return;
    }
    if (pointersRef.current.size > 2) return;

    // 中键 / 空格 / 抓手工具 = 拖画面（前两个在任何工具下都能用）
    if (event.button === 1 || spaceRef.current || (event.button === 0 && tool === "pan")) {
      event.currentTarget.setPointerCapture(event.pointerId);
      panDragRef.current = {
        sx: event.clientX,
        sy: event.clientY,
        pan: viewRef.current.pan,
      };
      setPanning(true);
      return;
    }
    if (event.button !== 0) return;

    const point = imagePoint(event.clientX, event.clientY);
    const hit = inImage(point);
    if (tool === "box") {
      event.currentTarget.setPointerCapture(event.pointerId);
      drawingRef.current = null;
      // 点在照片外的留白上就不起框
      setBox(hit ? { x: point.x, y: point.y, w: 0, h: 0 } : null);
      return;
    }
    if (!hit) return; // 点在留白上不动笔
    event.currentTarget.setPointerCapture(event.pointerId);
    const stroke: Stroke = { erase: tool === "erase", radius, points: [point] };
    drawingRef.current = stroke;
    redoRef.current = [];
    stamp(point.x, point.y, stroke.erase);
  };

  const onPointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    const el = stageRef.current;
    if (el) {
      const r = el.getBoundingClientRect();
      const overControls = (event.target as HTMLElement | null)?.closest?.(".refine-zoom, .refine-nav");
      if (overControls) {
        setHover(null);
      } else {
        const p = { x: event.clientX - r.left, y: event.clientY - r.top };
        setHover(p);
        // 记住它 —— `+` / `−` / 滑杆以这里为缩放锚点，而不是永远舞台正中
        lastAnchorRef.current = p;
      }
    }
    if (pointersRef.current.has(event.pointerId)) {
      pointersRef.current.set(event.pointerId, { x: event.clientX, y: event.clientY });
    }

    if (pinchRef.current && pointersRef.current.size >= 2) {
      applyPinch();
      return;
    }
    if (panDragRef.current) {
      const d = panDragRef.current;
      setPan(
        clampPan(
          { x: d.pan.x + (event.clientX - d.sx), y: d.pan.y + (event.clientY - d.sy) },
          viewRef.current.disp,
          stage,
        ),
      );
      return;
    }
    if (!ready || busy || !event.currentTarget.hasPointerCapture(event.pointerId)) return;

    const point = imagePoint(event.clientX, event.clientY);
    if (tool === "box") {
      setBox((current) =>
        current ? { ...current, w: point.x - current.x, h: point.y - current.y } : current,
      );
      return;
    }
    const stroke = drawingRef.current;
    if (!stroke) return;
    const last = stroke.points[stroke.points.length - 1];
    // 采样：笔迹密集时丢点，重放才不会随着笔画变长越来越慢
    const step = Math.max(1, stroke.radius * 0.25);
    if (Math.hypot(point.x - last.x, point.y - last.y) < step) return;
    stroke.points.push(point);
    stamp(point.x, point.y, stroke.erase);
  };

  const onPointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    pointersRef.current.delete(event.pointerId);
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }

    // 拖画布收尾
    if (panDragRef.current && pointersRef.current.size === 0) {
      panDragRef.current = null;
      setPanning(false);
      return;
    }
    // 捏合收尾：剩下那根手指也不接着画，免得抬手时误涂（要涂得重新按下去）
    if (pinchRef.current && pointersRef.current.size < 2) {
      pinchRef.current = null;
      drawingRef.current = null;
      if (pointersRef.current.size === 0) {
        panDragRef.current = null;
        setPanning(false);
      }
      return;
    }
    if (!ready) return;

    const stroke = drawingRef.current;
    drawingRef.current = null;
    if (tool === "box") {
      setBox((current) => {
        if (!current) return current;
        const norm = {
          x: Math.min(current.x, current.x + current.w),
          y: Math.min(current.y, current.y + current.h),
          w: Math.abs(current.w),
          h: Math.abs(current.h),
        };
        return norm.w < 6 || norm.h < 6 ? null : norm;
      });
      return;
    }
    if (!stroke || stroke.points.length === 0) return;
    strokesRef.current.push(stroke);
    if (strokesRef.current.length > HISTORY_LIMIT) strokesRef.current.shift();
    setHist({ undo: strokesRef.current.length, redo: redoRef.current.length });
  };

  // ---------- 操作 ----------
  const resetAll = useCallback(() => {
    strokesRef.current = [];
    redoRef.current = [];
    replay();
  }, [replay]);

  const applyBox = async () => {
    const surface = surfaceRef.current;
    if (!surface || !box) return;
    setBusy(true);
    setErr(null);
    try {
      const k = surface.toNatural;
      const updated = await wardrobeApi.matteBox(garment.id, image.id, {
        x: box.x * k,
        y: box.y * k,
        width: box.w * k,
        height: box.h * k,
        mode: boxMode,
      });
      setBox(null);
      onSaved(updated);
      setReloadKey((v) => v + 1); // 换成新结果当基线，笔画历史作废
      setInfo(`已对框选区域重新抠图（${boxMode === "remove" ? "去背景" : "补回"}）`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "局部重抠失败");
    } finally {
      setBusy(false);
    }
  };

  const save = async () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await new Promise<Blob | null>((resolve) =>
        canvas.toBlob((b) => resolve(b), "image/png"),
      );
      if (!blob) throw new Error("导出失败");
      const updated = await wardrobeApi.refineCutout(garment.id, image.id, blob);
      onSaved(updated);
      onClose();
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy(false);
    }
  };

  // 笔刷光圈：半径按屏幕上真实的笔刷直径画，放得越大圈越大
  // （原来是固定 26px 的光标 SVG，笔刷调到 120 也还是那么大，根本看不出会涂到哪儿）
  const ringR = Math.max(2, radius * pxPerImage);
  const showRing =
    ready && !busy && !panning && hover !== null && tool !== "box" && tool !== "pan";
  const atFit = scalePx === null && pan.x === 0 && pan.y === 0;
  const atFull = Math.abs(scale - 1) < 0.005;

  return (
    <Dialog
      title="修整抠图"
      width={900}
      className="modal-refine"
      onClose={busy ? () => undefined : onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            取消
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => void save()}
            disabled={!ready || busy}
          >
            {busy ? "保存中…" : "保存修整结果"}
          </button>
        </>
      }
    >
      <p className="tiny">
        棋盘格是已经被抠掉的背景。<strong>擦除</strong>抹掉残留；<strong>恢复</strong>把
        <strong>原图</strong>的像素整块贴回来（想退回抠图那版用「撤销」）；
        <strong>框选重抠</strong>只把那块丢回模型重跑。<br />
        <strong>放大</strong>：滚轮（锚在指针处）或双指捏合，＋/− 与滑杆也行（锚在鼠标最后停的位置）。
        <strong>移动</strong>：切到<strong>「抓手」</strong>左键拖，或按住<strong>空格</strong> /
        <strong>中键</strong>拖；左上角<strong>小地图</strong>点哪跳哪；<strong>方向键</strong>微调。
      </p>

      <div className="refine-tools">
        <div className="chip-row">
          <button
            type="button"
            className={`chip-toggle ${tool === "erase" ? "on" : ""}`}
            onClick={() => setTool("erase")}
          >
            擦除笔
          </button>
          <button
            type="button"
            className={`chip-toggle ${tool === "restore" ? "on" : ""}`}
            onClick={() => setTool("restore")}
          >
            恢复笔
          </button>
          <button
            type="button"
            className={`chip-toggle ${tool === "box" ? "on" : ""}`}
            onClick={() => {
              setTool("box");
              setBox(null);
            }}
          >
            框选重抠
          </button>
          <button
            type="button"
            className={`chip-toggle ${tool === "pan" ? "on" : ""}`}
            title="左键拖动画面（也可以随时按住空格临时切换）"
            onClick={() => {
              setTool("pan");
              setBox(null);
            }}
          >
            抓手
          </button>
        </div>

        <label className="refine-slider">
          笔刷 {radius}px
          <input
            type="range"
            min={4}
            max={120}
            value={radius}
            onChange={(e) => setRadius(Number(e.target.value))}
          />
        </label>

        <div className="chip-row">
          <button
            type="button"
            className="btn btn-sm"
            onClick={undo}
            disabled={hist.undo === 0}
          >
            撤销
          </button>
          <button
            type="button"
            className="btn btn-sm"
            onClick={redo}
            disabled={hist.redo === 0}
          >
            重做
          </button>
          <button type="button" className="btn btn-sm" onClick={resetAll} disabled={!ready}>
              重置
          </button>
        </div>
      </div>

      <div
        ref={stageRef}
        className={`refine-stage tool-${tool}${busy ? " busy" : ""}${
          panning ? " panning" : spaceHeld && scale > fitScale + 0.001 ? " can-pan" : ""
        }`}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        onPointerLeave={() => setHover(null)}
      >
        <div
          className="refine-view"
          style={{ width: dispSize.w, height: dispSize.h, transform: `translate(${pan.x}px, ${pan.y}px)` }}
        >
          <canvas ref={canvasRef} className="refine-canvas" />
          {box && box.w > 0 && box.h > 0 && (
            <div
              className="refine-box"
              style={{
                left: `${percentage(box.x, canvasSize.w)}%`,
                top: `${percentage(box.y, canvasSize.h)}%`,
                width: `${percentage(box.w, canvasSize.w)}%`,
                height: `${percentage(box.h, canvasSize.h)}%`,
              }}
            />
          )}
        </div>

        {showRing && hover && (
          <div
            className={`refine-ring${tool === "restore" ? " on-restore" : ""}`}
            style={{
              left: hover.x - ringR,
              top: hover.y - ringR,
              width: ringR * 2,
              height: ringR * 2,
            }}
          />
        )}

        <div className="refine-zoom">
          <button
            type="button"
            className="btn btn-sm refine-zoom-btn"
            title="缩小"
            onClick={() => zoomTo(scale / ZOOM_STEP)}
            disabled={!ready || busy || scale <= fitScale + 0.002}
          >
            −
          </button>
          <input
            type="range"
            min={fitScale}
            max={maxScale}
            step={0.02}
            value={scale}
            aria-label="缩放"
            title="100% = 一个图像像素对一个屏幕像素（1:1）"
            disabled={!ready || busy}
            onChange={(e) => zoomTo(Number(e.target.value))}
          />
          <span className="refine-zoom-val" title="100% = 1:1，按真实像素看">
            {Math.round(scale * 100)}%
          </span>
          <button
            type="button"
            className="btn btn-sm refine-zoom-btn"
            title="放大"
            onClick={() => zoomTo(scale * ZOOM_STEP)}
            disabled={!ready || busy || scale >= maxScale - 0.002}
          >
            ＋
          </button>
          <button
            type="button"
            className="btn btn-sm"
            title="整张图一屏装下并居中"
            onClick={fitView}
            disabled={!ready || busy || atFit}
          >
            适应窗口
          </button>
          <button
            type="button"
            className="btn btn-sm"
            title="一个图像像素对一个屏幕像素，看细节最准"
            onClick={() => zoomTo(1)}
            disabled={!ready || busy || atFull}
          >
            1:1
          </button>
        </div>

        {showNav && (
          <div
            className="refine-nav"
            style={{ width: navBox.w, height: navBox.h }}
            title="整张图的位置图 —— 点哪儿跳到哪儿，也可以拖这个方框扫"
            onPointerDown={(e) => {
              // 别让舞台把这一下当成下笔
              e.stopPropagation();
              e.currentTarget.setPointerCapture(e.pointerId);
              navDragRef.current = true;
              onNavPointer(e);
            }}
            onPointerMove={(e) => {
              if (!navDragRef.current) return;
              e.stopPropagation();
              onNavPointer(e);
            }}
            onPointerUp={(e) => {
              navDragRef.current = false;
              if (e.currentTarget.hasPointerCapture(e.pointerId)) {
                e.currentTarget.releasePointerCapture(e.pointerId);
              }
              e.stopPropagation();
            }}
          >
            <canvas ref={navCanvasRef} className="refine-nav-canvas" />
            {navView && <div className="refine-nav-view" style={navView} />}
          </div>
        )}

        {loading && <div className="tiny">加载中…</div>}
      </div>

      {tool === "box" && (
        <div className="refine-boxbar">
          <span className="tiny">
            {box ? `已框选 ${Math.round(box.w)}×${Math.round(box.h)} 像素` : "在图上拖出一个框"}
          </span>
          <select
            value={boxMode}
            onChange={(e) => setBoxMode(e.target.value as "remove" | "add")}
          >
            <option value="remove">去掉框内的背景</option>
            <option value="add">补回框内的衣物</option>
          </select>
          <button
            type="button"
            className="btn btn-sm btn-primary"
            onClick={() => void applyBox()}
            disabled={!box || busy}
          >
            {busy ? "重抠中…" : "重抠这块"}
          </button>
        </div>
      )}

      {info && <p className="tiny">{info}</p>}
      {err && <p className="alert">{err}</p>}
    </Dialog>
  );
}

/** canvas 像素 → 百分比（overlay 用百分比定位，才能跟随视口缩放） */
function percentage(value: number, total: number): number {
  if (total <= 0) return 0;
  return Math.max(0, Math.min(100, (value / total) * 100));
}

/**
 * 把图片按 contain 缩进给定的框里。
 *
 * **不放大**：原图比舞台还小时就按原始像素显示（和 `max-width:100%` 那套一致），
 * 免得小图被拉大成一团糊，那反而看不清边缘。
 */
function fitInto(img: Size, box: Size): Size {
  if (!img.w || !img.h || box.w <= 0 || box.h <= 0) return { w: 0, h: 0 };
  const s = Math.min(1, box.w / img.w, box.h / img.h);
  return { w: img.w * s, h: img.h * s };
}

/**
 * 平移的边界：`|照片 − 取景框| / 2`。
 *
 * - 照片比取景框**大**：能一路拖到边缘对齐（角角落落都够得着）；
 * - 照片比取景框**小**：只能在框内挪（挪到哪都还在视野里，不会一走手就找不到图）。
 *
 * 两种情形能写成同一个式子，而且边界连续 —— 早期写成 `max(0, 差/2)`（小的那条轴
 * 死锁在正中）时，光标缩放到一半会横向拖不动，「光标底下的像素不动」这条就断了。
 */
function clampPan(pan: Point, disp: Size, stage: Size): Point {
  const mx = Math.abs(disp.w - stage.w) / 2;
  const my = Math.abs(disp.h - stage.h) / 2;
  return {
    x: Math.min(mx, Math.max(-mx, pan.x)),
    y: Math.min(my, Math.max(-my, pan.y)),
  };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    // 同源的签名 URL 也要显式声明，否则 canvas 会被标记为污染、读不出像素
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error("图片加载失败"));
    img.src = url;
  });
}

/** 把图片按给定尺寸画到离屏画布上取像素。 */
function rasterize(img: HTMLImageElement, width: number, height: number): ImageData {
  const scratch = document.createElement("canvas");
  scratch.width = width;
  scratch.height = height;
  const ctx = scratch.getContext("2d");
  if (!ctx) throw new Error("无法创建画布");
  ctx.drawImage(img, 0, 0, width, height);
  return ctx.getImageData(0, 0, width, height);
}

/** 找原图：跟后端 `_pick_original_image` 一个口径（sort_order, id 最小的 original）。 */
function pickOriginalUrl(garment: Garment): string | null {
  const ordered = [...garment.images].sort((a, b) => a.sort_order - b.sort_order || a.id - b.id);
  return ordered.find((i) => i.kind === "original")?.original_url ?? null;
}
