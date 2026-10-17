"""框选重抠的执行入口（**API 侧**）。

抠图模型只在 Worker 镜像里（DECISIONS D5：ONNX 不进 API 镜像，否则 API 容器要
多背 ~180MB 模型 + ORT 运行时，OOM 一次整个网站就挂了）。可「框选重抠」是个
同步接口 —— 用户框一块、点一下、马上要看到结果，所以它落在 API 进程里。

一条活两条路：

* 配了 ``WARDROBE_WORKER_REFINE_URL`` → 把两个 storage_key 交给 Worker 的内部
  接口算（发行版 compose 的标准路径，API 镜像里一个 rembg 符号都没有）；
* 留空 → 在本进程里算（裸机部署 / 本机开发：那台机的 venv 里装全了依赖）。

⚠️ 曾经的坑：这段逻辑写死在路由里，直接 ``import app.worker.refine``。裸机能跑，
   Docker 里则稳定报 ``rembg 不可用：No module named 'rembg'`` —— 因为 API 镜像
   本来就没有它。**本地测不出来，只有真部署才看得见。**
"""

from __future__ import annotations

import asyncio
import io
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx
from PIL import Image

from app.config import get_settings

log = logging.getLogger(__name__)

__all__ = ["RefineOutcome", "remat_box"]


#: 本机算时的线程池。**只有 1 个线程**：抠图吃满一个核，两个并行只会一起变慢、
#: 一起吃内存（与 Worker 的 `_AI_POOL` 同一套道理）。
_LOCAL_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wardrobe-refine")


@dataclass(frozen=True)
class RefineOutcome:
    """重抠结果。``png`` 与 ``error`` 二选一，``status`` 是建议回给前端的状态码。"""

    png: bytes | None = None
    error: str | None = None
    status: int = 400

    @property
    def ok(self) -> bool:
        return self.png is not None


async def remat_box(
    *,
    original: Image.Image,
    cutout: Image.Image,
    original_key: str,
    cutout_key: str,
    box: dict,
    mode: str,
) -> RefineOutcome:
    """对框选区域重跑一次抠图，返回合成后的 RGBA PNG。

    ``original`` / ``cutout`` 已经由路由读进内存（它要先做 404/409 校验），
    本机路径直接用它们；代理路径只传 key，让 Worker 自己读盘 ——
    api 与 worker 共用 ``./data/media``，没必要把几 MB 的图再 HTTP 搬一遍。
    """
    base = (get_settings().worker_refine_url or "").strip().rstrip("/")
    if base:
        return await _remat_remote(base, original_key, cutout_key, box, mode)
    return await _remat_local(original, cutout, box, mode)


async def _remat_local(
    original: Image.Image, cutout: Image.Image, box: dict, mode: str
) -> RefineOutcome:
    try:
        from app.worker.refine import RefineError
        from app.worker.refine import remat_box as _remat_box
    except Exception as exc:  # noqa: BLE001 - 依赖缺失要能优雅报错
        return RefineOutcome(
            error=(
                "这台服务器算不了框选重抠：API 进程没有抠图依赖（rembg），"
                "也没配 Worker 重抠地址（WARDROBE_WORKER_REFINE_URL）。"
                f"原始错误：{exc}"
            ),
            status=503,
        )

    loop = asyncio.get_running_loop()
    try:
        merged = await loop.run_in_executor(
            _LOCAL_POOL,
            _remat_box,
            original.convert("RGB"),
            cutout.convert("RGBA"),
            box,
            mode,
        )
    except RefineError as exc:
        return RefineOutcome(error=str(exc), status=400)
    except Exception as exc:  # noqa: BLE001 - 推理炸了不该变成 500 白屏
        log.exception("本机框选重抠失败")
        return RefineOutcome(error=f"局部重抠失败：{exc}", status=500)

    return RefineOutcome(png=_to_png(merged))


async def _remat_remote(
    base: str, original_key: str, cutout_key: str, box: dict, mode: str
) -> RefineOutcome:
    """把活交给 Worker。超时 / 连不上都翻译成「能看懂的一句话」。"""
    settings = get_settings()
    timeout = settings.worker_refine_timeout_seconds
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{base}/refine/matte-box",
                json={
                    "original_key": original_key,
                    "cutout_key": cutout_key,
                    "box": box,
                    "mode": mode,
                },
            )
    except httpx.TimeoutException:
        return RefineOutcome(
            error=f"抠图服务忙（等超过 {timeout:.0f}s）。队列里可能还有衣物在处理，稍后再试一次。",
            status=504,
        )
    except Exception as exc:  # noqa: BLE001 - 连不上 = Worker 没起 / 被 scale 成 0
        log.warning("连不上 Worker 重抠接口 %s：%s", base, exc)
        return RefineOutcome(
            error="抠图服务没在跑（Worker 容器未启动或已停用），框选重抠暂时不可用。",
            status=503,
        )

    if resp.status_code >= 400:
        return RefineOutcome(error=_detail_of(resp), status=resp.status_code)

    if not resp.content:
        return RefineOutcome(error="抠图服务返回了空结果", status=502)
    return RefineOutcome(png=resp.content)


def _detail_of(resp: httpx.Response) -> str:
    """FastAPI 的错误体是 ``{"detail": "..."}``；解析不出来就退回状态码。"""
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return f"抠图服务返回 {resp.status_code}"
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail
    return f"抠图服务返回 {resp.status_code}"


def _to_png(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.convert("RGBA").save(buf, format="PNG", optimize=True)
    return buf.getvalue()
