"""Worker 的内部 HTTP 接口：只服务一件事 —— 框选重抠。

**为什么需要它**：抠图模型只装在 Worker 镜像里（DECISIONS D5：API 镜像保持轻量，
不带 onnxruntime），可「框选重抠」是用户点一下就要看结果的同步操作，天然落在 API
进程。API 没有 rembg，于是这个功能在 Docker 部署里一直是

    rembg 不可用：No module named 'rembg'

（裸机能用，只是因为那台机的 venv 把两套依赖装在一起了 —— 所以本地测不出来。）

**为什么不干脆给 API 也装上 rembg**：那会让 API 容器的常驻内存多出 u2net 的
~180MB 模型 + ORT 运行时（一次重抠就要加载），而 API 是全家桶的入口，OOM 一次
整个网站就挂了。Worker 本来就有模型、也有 1500m 的硬上限，让它干这活最省。

三条实现约束：

1. **只走容器网络**：发行版 compose 不 publish 这个端口，外部打不到；
   接口本身不带鉴权，别把它暴露出去（`WARDROBE_WORKER_REFINE_PORT=0` 可关掉）。
2. **不传图片，只传 storage_key**：api 与 worker 共用 `./data/media` 这个挂载，
   让 Worker 自己去读盘，省掉一次几 MB 的 HTTP 搬运。
3. **跑在 AI 线程池里**：与后台流水线共用那个只有 1 个线程的池子 —— 同一时刻
   只有一个推理在跑。这台机器就 1~4 核，两个推理并行只会一起变慢、一起吃内存。
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from PIL import Image
from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.media import resolve_key

log = logging.getLogger(__name__)

__all__ = ["app", "start_refine_server"]


class MatteBoxIn(BaseModel):
    """一次框选重抠的请求。坐标单位是**原图像素**，与前端一致。"""

    original_key: str = Field(description="原图 storage_key")
    cutout_key: str = Field(description="当前抠图结果 storage_key")
    box: dict[str, Any] = Field(description="{x, y, width, height}")
    mode: str = Field(default="remove", description="remove 只减 / add 只加")


def _read_image(rel_key: str) -> Image.Image:
    """按 storage_key 读图。读不到就抛 409 —— 两个容器共用同一块盘，读不到是真丢了。"""
    try:
        path = resolve_key(rel_key)
    except Exception as exc:  # noqa: BLE001 - 非法 key（穿越）也只当作读不到
        raise HTTPException(400, f"非法的媒体路径：{exc}") from exc
    if not path.is_file():
        raise HTTPException(409, "图片文件缺失（api 与 worker 的 media 挂载是否一致？）")
    try:
        with Image.open(path) as img:
            img.load()
            return img.copy()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(409, f"图片文件损坏：{exc}") from exc


app = FastAPI(
    title="wardrobe worker refine",
    # 内部接口，不对外开放文档
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.get("/healthz")
async def healthz() -> dict[str, object]:
    """给冒烟脚本用的探针：能看到 matting provider 名字，就知道模型装没装。"""
    settings = get_settings()
    return {
        "ok": True,
        "matting_provider": settings.ai_matting_provider,
        "matting_model": settings.matting_model,
    }


@app.post("/refine/matte-box")
async def matte_box(payload: MatteBoxIn) -> Response:
    """框选一块区域重跑抠图，返回合成好的 RGBA PNG（**不落库、不改文件**）。

    DB 记录与落盘都留在 API 侧：这里只做「算」，职责单一，出错也不会写坏数据。
    """
    # 延迟导入：pipeline 会带进 rembg 相关模块，让 worker 启动别为它多等几秒
    from app.worker.pipeline import _AI_POOL
    from app.worker.refine import RefineError
    from app.worker.refine import remat_box as _remat_box

    original = _read_image(payload.original_key)
    cutout = _read_image(payload.cutout_key)

    loop = asyncio.get_running_loop()
    try:
        merged = await loop.run_in_executor(
            _AI_POOL,
            _remat_box,
            original.convert("RGB"),
            cutout.convert("RGBA"),
            payload.box,
            payload.mode,
        )
    except RefineError as exc:
        # 依赖缺失 / 框非法 / 这一笔推理失败 —— 都是能原样给用户看的原因
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        log.exception("框选重抠失败 original=%s", payload.original_key)
        raise HTTPException(500, f"局部重抠失败：{exc}") from exc

    buf = io.BytesIO()
    merged.save(buf, format="PNG", optimize=True)
    return Response(content=buf.getvalue(), media_type="image/png")


def start_refine_server() -> threading.Thread | None:
    """在后台线程里起 uvicorn；返回线程（已经启动）或 None（关掉/起不来）。

    为什么放在**线程**里而不是和主循环共用事件循环：

    * 主循环用 `loop.add_signal_handler` 接管了 SIGTERM（跑完手上这批再退），
      uvicorn 若在同一个循环里 `serve()`，会去装自己的信号处理器把它顶掉，
      结果是 `docker stop` 只关掉 HTTP，worker 变僵尸；
    * uvicorn 在非主线程里会**自动跳过**装信号处理器（它自己判断的），
      主线程的信号处理因此原封不动。
    * 线程里那个循环只有一个处理函数，`remat_box` 又是丢进 `_AI_POOL` 的，
      不会和主循环抢事件循环。
    """
    settings = get_settings()
    if settings.worker_refine_port <= 0:
        log.info("框选重抠接口已关闭（WARDROBE_WORKER_REFINE_PORT=0）")
        return None
    try:
        import uvicorn
    except Exception as exc:  # noqa: BLE001 - 没装 uvicorn 也要能跑流水线
        log.warning("没有 uvicorn，框选重抠接口不启动：%s", exc)
        return None

    config = uvicorn.Config(
        app,
        host="0.0.0.0",  # 容器内监听；compose 不 publish，外部打不到
        port=settings.worker_refine_port,
        log_level="warning",
        access_log=False,
        # 单个请求最多重抠一张图，不需要 keep-alive 之外的优化
        timeout_keep_alive=15,
    )
    server = uvicorn.Server(config)

    def _run() -> None:
        try:
            server.run()
        except Exception:  # noqa: BLE001 - 挂了也别把 worker 主循环带走
            log.exception("框选重抠接口异常退出")

    thread = threading.Thread(target=_run, name="wardrobe-refine-http", daemon=True)
    thread.start()
    log.info("框选重抠接口监听 :%s（仅容器网络，无鉴权）", settings.worker_refine_port)
    return thread
