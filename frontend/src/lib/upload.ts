/**
 * 上传分批。
 *
 * 为什么要分批：nginx 的 `client_max_body_size` 是**按单个请求**算的，一整个
 * FormData 里塞 30 张手机照片，随便就过百兆 —— 网关直接回 413，浏览器只看到
 * 「上传失败」。而且这类请求失败得很早（网关根本没转给后端），后端「单张超限
 * 就跳过这一张」的容错完全用不上：一张都不进去。
 *
 * 所以前端自己按**字节数**切批，串行发；每批都远小于网关上限。
 * 注意是按 size 而不是按张数 —— 张数无关，"20 张 200KB 的图" 和 "3 张 8MB 的图"
 * 完全是两个量级。
 */

/**
 * 单次请求体的软上限：24 MiB。
 *
 * 网关（镜像内 nginx）配的是 64 MiB、201 上的系统 nginx 也是 64 MiB，
 * 留一倍余量给 multipart 边界、表单字段和别的前置代理（比如反代/Lucky）。
 * 真要调，nginx 和这里**一起**调，别只动一边。
 */
export const UPLOAD_BATCH_BYTES = 24 * 1024 * 1024;

/**
 * 按累计字节数把文件切成若干批。顺序保持不变。
 *
 * - 空数组 → 空数组（调用方不用特判「没东西可传」）。
 * - 单个文件本身就超过 `maxBytes` → 独占一批，照样发出去：让服务端自己按
 *   单张上限拒绝并给出「这张太大」的原因，比前端静默吞掉更好解释。
 */
export function splitFilesByBytes(files: File[], maxBytes = UPLOAD_BATCH_BYTES): File[][] {
  const batches: File[][] = [];
  let current: File[] = [];
  let currentBytes = 0;

  for (const file of files) {
    // current.length > 0 才切：否则超大单文件会切出一个空批
    if (current.length > 0 && currentBytes + file.size > maxBytes) {
      batches.push(current);
      current = [];
      currentBytes = 0;
    }
    current.push(file);
    currentBytes += file.size;
  }
  if (current.length > 0) batches.push(current);

  return batches;
}

/** 分批上传的进度（每批开始前回调一次）。 */
export type UploadProgress = {
  /** 第几批（从 1 开始） */
  batch: number;
  /** 一共几批 */
  batches: number;
  /** 已经成功的文件数 */
  done: number;
  /** 本次一共选了几个文件 */
  total: number;
};
