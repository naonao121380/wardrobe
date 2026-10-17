"""单件衣物的流水线：抠图 → 打标 → 写回候选结果。

状态流转见 DECISIONS D6：::

    pending ──► (worker) ──► needs_review ──► (用户确认) ──► ready
                    │
                    └──────────────────────► failed

**状态的落点规则**（本文件的核心约定）：

- 抠出了图 / 给出了任何候选 → ``needs_review``，等用户看一眼；
- 啥也没产出、但也没出错（例如 provider 全是 none）→ ``ready``，别拿空弹窗烦人；
- 出错且没有任何产出 → ``failed``，错误原因留在 ``ai_suggestion.error`` 里，可重试。
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from PIL import Image
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.core.media import DELETION_SWALLOWED, resolve_key, store_image
from app.core.time import now_naive_utc, now_utc
from app.models.garment import Garment, GarmentImage, GarmentStatus
from app.models.tag import TagCategory, TagScope, TagValue
from app.models.user import User
from app.services.llm import tagger_for_user
from app.worker.providers import (
    CandidateTag,
    MattingResult,
    NoOpMatting,
    NoOpTagger,
    TagSuggestion,
    build_matting,
    call_tagger,
)

log = logging.getLogger(__name__)

SCOPE = "garment"


# ⚠️ provider 必须缓存：rembg 的 new_session() 会重新加载模型并新建一组 ORT 线程池。
# 原来每处理一件就 new 一个，既慢（每次重新加载 4.7MB~176MB 模型），又会不断泄漏
# session 与线程（日志里每次推理的线程号都在递增就是证据）—— 2C4G 的机器上跑多了
# 就会把内存啃光。这里按「provider + 模型」缓存，整个进程只加载一次。
_matting_cache: dict[tuple[str, str], object] = {}

# 抠图专用线程池，**只有 1 个 worker**。
#
# 为什么不用 asyncio.to_thread（默认会开 min(32, cpu+4) 个线程）：
# `asyncio.wait_for` 超时只能取消 await，取消不掉线程。一旦某张图卡住，超时后那个
# 线程还在推理，下一件又投进另一个线程 —— 几个推理同时占内存，RSS 只涨不降，
# 最后把宿主机拖死（2026-10-17 的事故现场）。单线程池等于给抠图加了个互斥：
# 慢的那张跑完之前，不会有第二个推理入场，最坏情况是「慢」，不是「爆内存」。
_AI_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="wardrobe-ai")


def _fit_for_ai(image: Image.Image, max_edge: int) -> Image.Image:
    """把图等比缩到长边 ≤ ``max_edge``，已经够小就原样返回。

    ★ 这一步是**内存安全阀**，不是「顺手优化」：抠图（尤其是 alpha matting）的
    复杂度跟像素数强相关，手机原图 15 MP 在 2 GB 机器上跑一次就能把宿主拖死。
    缩到 2 MP 后，同一张图的峰值内存降一个数量级，结果肉眼几乎无差别
    （展示用的最大尺寸也就是 720~1600）。
    """
    if max_edge <= 0:
        return image
    longest = max(image.size)
    if longest <= max_edge:
        return image
    scale = max_edge / longest
    size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
    # LANCZOS 比 BILINEAR 慢一点点，但边缘更干净 —— 抠图边界全靠它
    return image.resize(size, Image.Resampling.LANCZOS)


def _default_matting():
    s = get_settings()
    key = (s.ai_matting_provider, s.matting_model)
    provider = _matting_cache.get(key)
    if provider is None:
        provider = build_matting(
            s.ai_matting_provider,
            s.matting_model,
            alpha_matting=s.matting_alpha_matting,
            foreground_threshold=s.matting_foreground_threshold,
            background_threshold=s.matting_background_threshold,
            erode_size=s.matting_erode_size,
        )
        _matting_cache[key] = provider
    return provider


async def _tagger_for(session: AsyncSession, owner_id: int):
    """按**衣物主人**当前的大模型配置造打标 provider。

    不缓存、也不复用别的用户的：一个人的 Key 只会用在它自己的衣物上
    （见 app/services/llm.py）。用户中途改了配置，下一件衣物就用新的。
    """
    user = await session.get(User, owner_id)
    if user is None:  # 用户被删了，衣物还在队列里 —— 当作没配
        return NoOpTagger()
    return tagger_for_user(user)


def _pick_original(garment: Garment) -> GarmentImage | None:
    """封面那张原图。抠图只处理它 —— 同一件衣物的其它角度重跑没意义。"""
    ordered = sorted(garment.images, key=lambda i: (i.sort_order, i.id))
    return next((img for img in ordered if img.kind == "original"), None)


def _load_image(img: GarmentImage) -> Image.Image | None:
    try:
        data = resolve_key(img.storage_key).read_bytes()
        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except Exception as exc:  # noqa: BLE001 - 文件丢了 / 格式坏了都只是这一件跑不了
        log.warning("读取原图失败 %s：%s", img.storage_key, exc)
        return None


async def _candidate_tags(session: AsyncSession, owner_id: int) -> list[CandidateTag]:
    """该用户全部单品标签取值，作为模型的候选池。

    小类带上父取值（外套/风衣），模型才不会把「开衫（上装）」和「开衫（连衣裙）」搞混。
    """
    rows = (
        await session.execute(
            select(TagValue.id, TagValue.value, TagCategory.name, TagValue.parent_value_id)
            .join(TagCategory, TagCategory.id == TagValue.category_id)
            .where(TagValue.owner_id == owner_id, TagCategory.scope == TagScope.garment.value)
            .order_by(TagCategory.name, TagValue.id)
        )
    ).all()

    parents = {}
    if any(row[3] for row in rows):
        parent_ids = {row[3] for row in rows if row[3]}
        found = (
            await session.execute(select(TagValue.id, TagValue.value).where(TagValue.id.in_(parent_ids)))
        ).all()
        parents = dict(found)

    return [
        CandidateTag(
            value_id=row[0],
            category_name=row[2],
            value=row[1],
            parent_value=parents.get(row[3]),
        )
        for row in rows
    ]


async def _recent_names(
    session: AsyncSession, owner_id: int, exclude_id: int, limit: int = 40
) -> list[str]:
    """衣橱里最近几件衣物的名字，回带给模型让它避开重名。

    只取最近 ``limit`` 件：全塞进去 prompt 会很长，而模型真正需要知道的是
    「最近这批是什么风格」，看到前几十件足够它判断会不会撞名。
    """
    rows = (
        await session.execute(
            select(Garment.name)
            .where(Garment.owner_id == owner_id, Garment.id != exclude_id)
            .order_by(Garment.id.desc())
            .limit(limit)
        )
    ).all()
    return [str(name).strip() for (name,) in rows if name and str(name).strip()]


async def _unique_name(
    session: AsyncSession, owner_id: int, garment_id: int, name: str | None
) -> str | None:
    """名字撞车就加序号后缀 —— 模型避重失败时的兜底。

    判重看两类名字：衣物**现在叫**什么，以及**还有没复核的 AI 建议名** —— 批量跑
    一批衣物时，前面的结果还躺在建议里没入库，只看 garment.name 会连着给出两个
    「黑色T恤 (2)」。

    只和同 owner 下的**其它**衣物比，自己以前叫什么不管：重跑时它自己的旧名字就在
    库里，那不算重名。
    """
    if not name:
        return name
    name = name.strip()
    rows = (
        await session.execute(
            select(Garment.name, Garment.ai_suggestion).where(
                Garment.owner_id == owner_id, Garment.id != garment_id
            )
        )
    ).all()
    taken: set[str] = set()
    for current, raw in rows:
        if current and str(current).strip():
            taken.add(str(current).strip())
        if not raw:
            continue
        try:
            pending = json.loads(raw)
        except (ValueError, TypeError):
            continue
        if isinstance(pending, dict) and isinstance(pending.get("name"), str):
            if pending["name"].strip():
                taken.add(pending["name"].strip())
    if name not in taken:
        return name
    for i in range(2, 1000):
        candidate = f"{name} ({i})"
        if candidate not in taken:
            log.info("AI 给的名字撞车，改为 %s", candidate)
            return candidate
    return f"{name} ({now_utc().timestamp():.0f})"


def _store_cutout(garment: Garment, result: MattingResult) -> GarmentImage | None:
    """把抠图结果落盘，追加为一张 ``kind=cutout`` 的图。

    **不替换原图**：用户可能觉得抠坏了，原图必须留着；也不抢封面（封面由用户在
    审核界面自己点），只按 sort_order 排到末尾。
    """
    assert result.image is not None
    buf = io.BytesIO()
    result.image.save(buf, format="PNG")
    meta = store_image(garment.owner_id, buf.getvalue(), "image/png")

    next_order = max((img.sort_order for img in garment.images), default=-1) + 1
    image = GarmentImage(
        garment_id=garment.id,
        owner_id=garment.owner_id,
        kind="cutout",
        storage_key=str(meta["storage_key"]),
        thumb_small_key=str(meta["thumb_small_key"]),
        thumb_medium_key=str(meta["thumb_medium_key"]),
        width=int(meta["width"]),  # type: ignore[arg-type]
        height=int(meta["height"]),  # type: ignore[arg-type]
        bytes=int(meta["bytes"]),  # type: ignore[arg-type]
        content_type=str(meta["content_type"]),
        sort_order=next_order,
    )
    garment.images.append(image)
    return image


async def _clear_old_cutouts(session: AsyncSession, garment: Garment) -> int:
    """清掉上一次的抠图结果（DB 记录 + 磁盘文件）。

    重跑流水线时如果不清，每跑一次就多攒一张 ``kind=cutout``：复核界面里会出现一排
    一模一样的图，磁盘也只涨不降。只在抠图**成功之后**调用，免得抠失败还赔上旧的那张。
    """
    stale = [img for img in garment.images if img.kind == "cutout"]
    if not stale:
        return 0

    for img in stale:
        for key in (img.storage_key, img.thumb_small_key, img.thumb_medium_key):
            if not key:
                continue
            try:
                resolve_key(key).unlink(missing_ok=True)
            except DELETION_SWALLOWED as exc:  # noqa: BLE001 - 文件本来就可能已经不在了
                log.debug("清理旧抠图文件失败 %s：%r", key, exc)

    # 直接发 DELETE，避免依赖 ORM 关系集合的同步状态（见 process_garment 里的说明）
    await session.execute(
        delete(GarmentImage).where(
            GarmentImage.garment_id == garment.id,
            GarmentImage.kind == "cutout",
        )
    )
    # 内存里的关系集合也要同步掉，否则本轮后续逻辑还会看到它们
    garment.images = [img for img in garment.images if img.kind != "cutout"]
    await session.flush()
    log.info("清理掉 %s 张旧抠图（garment=%s）", len(stale), garment.id)
    return len(stale)


async def process_garment(
    session: AsyncSession,
    garment_id: int,
    matting=None,
    tagger=None,
    *,
    threshold_minutes: int | None = None,
) -> str:
    """处理一件衣物，返回落到的状态。

    ``matting`` / ``tagger`` 可注入（测试用假的）；不传就按配置组装。
    调用方**必须先抢到processing**（见 :func:`app.worker.runner.claim_garment`），
    否则两个 Worker 会重复算同一件。
    """
    matting = matting if matting is not None else _default_matting()
    settings = get_settings()

    garment = (
        await session.execute(
            select(Garment)
            .where(Garment.id == garment_id)
            .options(selectinload(Garment.images), selectinload(Garment.tag_links))
        )
    ).scalar_one_or_none()
    if garment is None:
        log.warning("衣物 %s 不存在（可能已被删除），跳过", garment_id)
        return ""

    # tagger 要按衣物**主人**的大模型配置来造，所以得等 garment 读出来之后
    tagger = tagger if tagger is not None else await _tagger_for(session, garment.owner_id)

    errors: list[str] = []
    image: Image.Image | None = None
    result: MattingResult | None = None  # 抠图关闭时不会赋值，打标要据此退回原图
    cutout_created = False
    # 一个 AI 都没启用时，没图不是「失败」—— 只是没东西可跑，安静地 ready 即可
    ai_enabled = matting.name != NoOpMatting.name or tagger.name != NoOpTagger.name

    original = _pick_original(garment)
    if original is None:
        if ai_enabled:
            errors.append("没有原图，跳过 AI 处理")
    else:
        image = _load_image(original)
        if image is None:
            errors.append("原图无法读取")
        else:
            # ★ 先缩再抠。原图可能是 3500×4500 的手机照，直接喂给 rembg 会按原尺寸
            #   跑 alpha matting —— 内存/耗时超线性，小机器会被一张图拖死宿主。
            before = image.size
            image = _fit_for_ai(image, settings.ai_input_max_edge)
            if image.size != before:
                log.info(
                    "抠图前缩放 %sx%s → %sx%s（上限 %s）",
                    before[0], before[1], image.size[0], image.size[1],
                    settings.ai_input_max_edge,
                )

    # --- 抠图 ---
    if image is not None and matting.name != NoOpMatting.name:
        try:
            loop = asyncio.get_running_loop()
            # 单线程池：同一时刻只允许一个推理在跑（超时的线程取消不掉，但至少不叠加）
            result = await loop.run_in_executor(_AI_POOL, matting.remove, image)
        except Exception as exc:  # noqa: BLE001 - provider 内部已经兜过，这里防的是漏网的
            result = None
            errors.append(f"抠图异常：{exc}")
        if result is not None:
            if result.ok:
                # 先把上一轮的抠图清掉，再存新的 —— 否则重跑一次就多一张
                try:
                    await _clear_old_cutouts(session, garment)
                except Exception as exc:  # noqa: BLE001 - 清不掉也不该拦住新结果
                    errors.append(f"清理旧抠图失败：{exc}")
                try:
                    _store_cutout(garment, result)
                    cutout_created = True
                except Exception as exc:  # noqa: BLE001 - 存盘失败不影响打标
                    errors.append(f"抠图结果保存失败：{exc}")
            elif result.error:
                errors.append(result.error)

    # --- 打标 ---
    #抠图成功就拿抠图给模型看：背景被剔掉之后，颜色、版型这些判断明显更稳
    #（原图里的木地板、衣架、人会污染判别）。抠没了就退回原图。
    suggestion = TagSuggestion()
    if tagger.name != NoOpTagger.name:
        tagger_image = result.image if (result is not None and result.ok) else image
        # 这里不再重复报错：没有图的情况上游（原图缺失/读不了）已经记过了
        if tagger_image is not None:
            candidates = await _candidate_tags(session, garment.owner_id)
            # 把已有名字告诉模型：只给「颜色+品类」的话，三件黑 T 恤会起出一模一样的名字
            existing_names = await _recent_names(session, garment.owner_id, garment.id)
            try:
                suggestion = await call_tagger(tagger, tagger_image, candidates, existing_names)
            except Exception as exc:  # noqa: BLE001
                suggestion = TagSuggestion(error=f"打标异常：{exc}")
            if suggestion.error:
                errors.append(suggestion.error)
            else:
                # 模型没躲开重名就由我们来兜底，宁可「黑色T恤 (2)」也不要两件同名
                suggestion.name = await _unique_name(
                    session, garment.owner_id, garment.id, suggestion.name
                )

    # --- 落库 ---
    provider = "+".join(
        part for part in (matting.name, tagger.name) if part not in ("none", "")
    ) or "none"
    error_text = "；".join(dict.fromkeys(e for e in errors if e)) or None
    has_output = cutout_created or not suggestion.is_empty

    if has_output:
        new_status = GarmentStatus.NEEDS_REVIEW
        payload = json.dumps(
            {
                "name": suggestion.name,
                "tag_value_ids": suggestion.tag_value_ids,
                "provider": provider,
                "confidence": suggestion.confidence,
                "error": error_text,
            },
            ensure_ascii=False,
        )
    elif error_text:
        new_status = GarmentStatus.FAILED
        payload = json.dumps(
            {"name": None, "tag_value_ids": [], "provider": provider, "confidence": None,
             "error": error_text},
            ensure_ascii=False,
        )
    else:
        # 什么都没跑出来、也没出错：没必要打扰用户
        new_status = GarmentStatus.READY
        payload = None

    # ⚠️ 状态必须用 Core UPDATE 显式写，不能只靠 `garment.status = ...`。
    #
    # Worker 整进程共用一个 AsyncSession（db.py 里 expire_on_commit=False），identity
    # map 里缓存的 Garment 对象属性**停留在上一轮的终值**。重跑同一件衣物时，若这轮算出的
    # 结果与上轮一模一样（同一张原图 + 同一个模型，几乎必然如此），赋值会被 SQLAlchemy
    # 判定为「没有变化」，UPDATE 语句里根本不带 status / ai_suggestion 列 —— 而 claim
    # 已经把库里的状态改成了 processing，于是这件衣物永久卡在处理中。前端只要看到
    # processing 就会不停轮询，表现就是「一直处理中 + 页面不断刷新」。
    #
    # 显式 UPDATE 绕开 ORM 的 dirty 追踪，无论内存里是什么值都写库。
    # ★ updated_at 是 timestamptz（要 aware）；processed_at 是 timestamp（要 naive）。
    #   以前两者共用 `now_utc()`，PG 上写 processed_at 必炸（SQLite 不报错，所以测试绿）。
    stamped = now_utc()
    processed = now_naive_utc()
    await session.execute(
        update(Garment)
        .where(Garment.id == garment.id)
        .values(
            status=new_status,
            ai_suggestion=payload,
            processed_at=processed,
            updated_at=stamped,
        )
    )
    await session.commit()
    log.info("衣物 %s 处理完毕：%s（provider=%s）", garment_id, new_status, provider)
    return new_status


async def stale_processing_ids(session: AsyncSession, minutes: int) -> list[int]:
    """把卡在 processing 超过 N 分钟的衣物 id 捞出来。

    Worker 被 kill -9 或 OOM 都会留下永久的 processing，不回收就永远没人再碰它。
    """
    rows = (
        await session.execute(
            select(Garment.id, Garment.updated_at).where(
                Garment.status == GarmentStatus.PROCESSING
            )
        )
    ).all()

    from app.core.time import as_aware

    cutoff = now_utc() - timedelta(minutes=minutes)
    stale = []
    for garment_id, updated_at in rows:
        if updated_at is None:
            stale.append(garment_id)
            continue
        try:
            if as_aware(updated_at) < cutoff:
                stale.append(garment_id)
        except TypeError:  # 理论上不会，as_aware 已归一化
            stale.append(garment_id)
    return stale
