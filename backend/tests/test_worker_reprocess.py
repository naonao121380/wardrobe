"""回归：同一件衣物**重复**跑 AI 流水线时的状态落点。

这里只锁一件事：重跑不能把衣物卡在 ``processing``。

踩过的坑（CT202 上实测复现，现象是「一直处理中 + 页面不断刷新」）：

    Worker 整进程共用一个 ``AsyncSession``（db.py 里 ``expire_on_commit=False``），
    identity map 里缓存的 Garment 对象属性**停留在上一轮的终值**。重跑时若本轮
    结果与上轮完全一致（同一张原图 + 同一个模型 —— 几乎必然如此），
    ``garment.status = X`` 会被 SQLAlchemy 判定为「没有变化」，生成的 UPDATE
    语句里根本不带 ``status`` / ``ai_suggestion`` 列；而 ``claim_garment`` 已经
    把库里的状态改成了 ``processing``，于是这件衣物永久卡在处理中。前端只要
    看到 pending/processing 就会不停轮询，于是页面一直刷新。

两条硬性要求（少一条这个测试就是假的）：

1. 必须用**同一个 session** 连跑两轮 —— 换 session 就复现不出来；
2. 核实必须用**另一个 session** —— 同一个 session 会返回 identity map 里的
   内存对象，值看着是对的，实则库里还是 processing。
"""

from __future__ import annotations

import io
import json

import pytest
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.media import store_image
from app.models.garment import Garment, GarmentImage, GarmentStatus
from app.worker.pipeline import process_garment
from app.worker.providers import (
    CandidateTag,
    MattingResult,
    NoOpMatting,
    NoOpTagger,
    TagSuggestion,
)

pytestmark = pytest.mark.asyncio


# ---------- 假 provider ----------


class _StubMatting:
    """每次都产出**一模一样**的抠图 —— 复现这个 bug 的前提。"""

    name = "stub"

    def remove(self, image: Image.Image) -> MattingResult:
        return MattingResult(image=Image.new("RGBA", (32, 32), (10, 20, 30, 0)),
                 provider=self.name)


class _StubTagger:
    name = "stub"

    async def suggest(self, image: Image.Image, candidates: list[CandidateTag]) -> TagSuggestion:
        return TagSuggestion(
            name="黑色针织衫",
            tag_value_ids=[c.value_id for c in candidates[:2]],
            confidence=0.9,
        )


def _png(color: tuple[int, int, int] = (200, 40, 40), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


# ---------- 夹具 ----------


async def _new_garment(session: AsyncSession, owner_id: int, name: str) -> int:
    garment = Garment(owner_id=owner_id, name=name, status=GarmentStatus.PENDING)
    session.add(garment)
    await session.flush()
    meta = store_image(owner_id, _png(), "image/png")
    session.add(
        GarmentImage(
            garment_id=garment.id,
            owner_id=owner_id,
            kind="original",
            storage_key=str(meta["storage_key"]),
            thumb_small_key=str(meta["thumb_small_key"]),
            thumb_medium_key=str(meta["thumb_medium_key"]),
            width=int(meta["width"]),
            height=int(meta["height"]),
            bytes=int(meta["bytes"]),
            content_type="image/png",
            sort_order=0,
        )
        )
    await session.commit()
    return garment.id


async def _raw(session: AsyncSession, garment_id: int) -> tuple[str, str | None]:
    """只查列、不碰 identity map 里的对象，拿到库里的真实值。"""
    row = (
        await session.execute(
            select(Garment.status, Garment.ai_suggestion).where(Garment.id == garment_id)
        )
    ).one()
    return str(row[0]), (str(row[1]) if row[1] is not None else None)


# ---------- 回归测试 ----------


async def test_second_run_writes_status_even_if_result_identical(session_factory, user):
    """第二轮产物和第一轮完全一样时，status 也必须写进库里。

    修复前的表现：返回值是 needs_review，但库里停在 processing。
    """
    matting = _StubMatting()
    tagger = _StubTagger()

    async with session_factory() as s:
        gid = await _new_garment(s, user.id, "重复处理A")

        # 第一轮
        assert await process_garment(s, gid, matting, tagger) == GarmentStatus.NEEDS_REVIEW

        # 复核通过 → ready（清空建议）；再次入队 → pending
        await s.execute(
            update(Garment).where(Garment.id == gid).values(status=GarmentStatus.READY)
        )
        await s.execute(
            update(Garment).where(Garment.id == gid).values(ai_suggestion=None)
        )
        await s.execute(
            update(Garment).where(Garment.id == gid).values(status=GarmentStatus.PENDING)
        )
        await s.commit()

        # 第二轮：同样的 provider、同样的产物 = 关键回归点
        assert await process_garment(s, gid, matting, tagger) == GarmentStatus.NEEDS_REVIEW

    # 用另一个 session 核实库里真正的状态
    async with session_factory() as verify:
        status, payload = await _raw(verify, gid)
    assert status == GarmentStatus.NEEDS_REVIEW, f"库里状态是 {status}"
    assert payload is not None, "库里 ai_suggestion 是 NULL —— 状态根本没写进去"


async def test_reprocess_does_not_accumulate_cutouts(session_factory, user):
    """连跑三轮，抠图始终只有一张（替换旧的，不是累加）。"""
    matting = _StubMatting()
    tagger = _StubTagger()

    async with session_factory() as s:
        gid = await _new_garment(s, user.id, "抠图累加B")

        for _ in range(3):
            await process_garment(s, gid, matting, tagger)
            await s.execute(
                update(Garment).where(Garment.id == gid).values(status=GarmentStatus.PENDING)
            )
            await s.commit()

    async with session_factory() as verify:
        g = (
            await verify.execute(
                select(Garment).where(Garment.id == gid)
            )
        ).scalar_one()
        await verify.refresh(g, attribute_names=["images"])
        cutouts = [i for i in g.images if i.kind == "cutout"]
        originals = [i for i in g.images if i.kind == "original"]

    assert len(cutouts) == 1, f"抠图有 {len(cutouts)} 张，应只有 1 张"
    assert len(originals) == 1, "原图被吃掉了"


async def test_reprocess_after_review_cycle_stays_consistent(session_factory, user):
    """完整走一遍：pending → needs_review → (复核) ready → pending → needs_review。

    核实每一跳**库里**的状态都对得上，不只是返回值对。
    """
    matting = _StubMatting()
    tagger = _StubTagger()
    gid: int | None = None

    async with session_factory() as s:
        gid = await _new_garment(s, user.id, "完整循环C")
        await process_garment(s, gid, matting, tagger)

    async def status_of() -> str:
        async with session_factory() as v:
            row = (await v.execute(select(Garment.status).where(Garment.id == gid))).one()
        return str(row[0])

    assert await status_of() == GarmentStatus.NEEDS_REVIEW

    # 复核通过
    async with session_factory() as s:
        await s.execute(
            update(Garment)
            .where(Garment.id == gid)
            .values(status=GarmentStatus.READY, ai_suggestion=None)
        )
        await s.commit()
    assert await status_of() == GarmentStatus.READY

    # 再次入队 + 重跑
    async with session_factory() as s:
        await s.execute(
            update(Garment).where(Garment.id == gid).values(status=GarmentStatus.PENDING)
        )
        await s.commit()
        again = await process_garment(s, gid, matting, tagger)
    assert again == GarmentStatus.NEEDS_REVIEW
    assert await status_of() == GarmentStatus.NEEDS_REVIEW
