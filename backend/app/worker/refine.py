"""抠图结果的二次修整：框选局部重抠 + 画笔擦除/恢复。

为什么会有这个模块：通用的抠图模型（不管 u2netp 还是 u2net）都不可能一次抠干净 ——
衣架、吊牌、地上的影子、袖口和背景的粘连，总会留下残角。跟模型较劲不如让用户
动手补两笔，所以这里提供两种「二次抠图」手段：

* **框选重抠**（:func:`remat_box`）：用户在某个矩形里重跑一次抠图。适用于「这块
  区域模型没看明白」—— 比如整图是远景，衣袖只占几十像素，crop 出来再跑，分辨率
  上去了、背景也单纯了，结果通常准得多。
* **画笔**（前端完成）：擦掉残留 / 补回被误删的部分。Restore 的语义是**回到本次
  AI 抠图的原始 alpha**，而不是「涂回不透明」—— 所以不管叠多少笔，都不会凭空造出
  模型原本不认的像素。

两条硬规则跟 Worker 一致：rembg 惰性导入（缺依赖要能优雅报错），推理丢到
同步线程池里跑（不阻塞 asyncio 事件循环）。
"""

from __future__ import annotations

import logging

from PIL import Image

from app.config import get_settings

log = logging.getLogger(__name__)


class RefineError(Exception):
    """可以原样展示给用户的错误（没装依赖 / 区域非法 / 推理失败）。"""


def _session():
    """取一个 rembg session。**复用 Worker 的进程级缓存**，别每个请求重载一次模型。"""
    from app.worker.pipeline import _default_matting  # 延迟导入，避免 API 进程被 rembg 拖住

    provider = _default_matting()
    if provider.name == "none":
        raise RefineError(
            "未启用抠图 provider（WARDROBE_AI_MATTING_PROVIDER=none），无法局部重抠"
        )
    return provider


def _require_ready(provider) -> None:
    """让 provider 先把模型准备好；准备不了就把原因转成 RefineError 抛出去。"""
    probe = Image.new("RGB", (8, 8), (0, 0, 0))
    result = provider.remove(probe)
    if result.error:
        raise RefineError(result.error)


def _fit_for_model(image: Image.Image) -> Image.Image:
    """按 `ai_input_max_edge` 缩到长边上限（复用主流水线的实现，口径只此一处）。"""
    from app.worker.pipeline import _fit_for_ai

    return _fit_for_ai(image, get_settings().ai_input_max_edge)


def _clamp_box(image: Image.Image, box: dict) -> tuple[int, int, int, int]:
    """把前端给的框限制在图片范围内；框太小直接判非法（抠不出东西）。"""
    try:
        x = float(box.get("x", 0))
        y = float(box.get("y", 0))
        w = float(box.get("width", 0))
        h = float(box.get("height", 0))
    except (TypeError, ValueError) as exc:
        raise RefineError("框选坐标不是数字") from exc

    x0, y0 = max(0, int(round(x))), max(0, int(round(y)))
    x1 = min(image.width, int(round(x + w)))
    y1 = min(image.height, int(round(y + h)))
    if x1 - x0 < 8 or y1 - y0 < 8:
        raise RefineError("框选区域太小（至少 8×8 像素）")
    return x0, y0, x1, y1


def remat_box(original: Image.Image, cutout: Image.Image, box: dict, mode: str) -> Image.Image:
    """对框选区域重跑一次抠图，把结果贴回现有的抠图 alpha 通道。

    Args:
        original: 原图（RGB）。crop 出来送模型 —— 用原图而不是抠图，是因为抠图已经
            被裁过 alpha，再 crop 等于让模型看一堆透明像素。
        cutout: 当前抠图结果（RGBA），尺寸应与原图一致。
        box: ``{x, y, width, height}``，坐标为原图像素。
        mode: ``remove`` 只减不加（去掉框里的背景残留）；``add`` 只加不减（补回被
            误删的部分）。

    Returns:
        合成后的 RGBA 图（尺寸与 cutout 一致）。

    Raises:
        RefineError: 依赖缺失、框非法、或这一笔推理失败。
    """
    if mode not in ("remove", "add"):
        raise RefineError(f"未知的框选模式：{mode}")

    provider = _session()
    x0, y0, x1, y1 = _clamp_box(original, box)
    crop = original.crop((x0, y0, x1, y1))

    # ★ 与 Worker 主流水线共用同一个内存安全阀：框可以框得很大（一下框住整张
    #   15 MP 原图完全合法），不缩就直接再跑一次推理，在 2 GB 机器上等于自杀。
    #   注意掩码要按**原始裁切**尺寸拉回来 —— 坐标是原图像素，不能跟着缩放。
    model_input = _fit_for_model(crop)

    result = provider.remove(model_input)
    if not result.ok:
        raise RefineError(result.error or "局部抠图失败")

    fresh_mask = result.image.convert("RGBA").getchannel("A").resize(
        crop.size, Image.Resampling.LANCZOS
    )

    base = cutout.convert("RGBA")
    if base.size != original.size:
        base = base.resize(original.size, Image.Resampling.LANCZOS)

    alpha = base.getchannel("A")
    previous = alpha.crop((x0, y0, x1, y1))

    if mode == "remove":
        merged = Image.composite(
            Image.new("L", crop.size, 0),  # 背景像素 → 透明
            previous,
            fresh_mask,  # 模型认定为前景的保留原样
        )
    else:
        merged = Image.composite(
            Image.new("L", crop.size, 255),
            previous,
            fresh_mask,
        )

    alpha.paste(merged, (x0, y0))
    base.putalpha(alpha)
    return base


def replace_cutout_pixels(cutout: Image.Image, submitted: Image.Image) -> Image.Image:
    """用前端画好并提交上来的图替换抠图内容（最后保存用）。

    前端传的是整张编辑完成的 RGBA PNG，这里只做尺寸校对：万一跟库里的图对不上，
    以**库里那张的画布**为准拉伸回去。
    """
    base = cutout.convert("RGBA")
    incoming = submitted.convert("RGBA")
    if incoming.size != base.size:
        incoming = incoming.resize(base.size, Image.Resampling.LANCZOS)
    return incoming
