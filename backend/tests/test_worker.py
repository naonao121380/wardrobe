"""Worker 流水线测试。

provider 全部注入假的 —— 这里测的是**编排逻辑**（状态怎么落、抠图怎么存、出错怎么降级），
不是模型效果；真模型的可用性在 CT202 上单独验。
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.media import store_image
from app.core.time import now_utc
from app.models.garment import Garment, GarmentImage, GarmentStatus
from app.worker.pipeline import _fit_for_ai, process_garment
from app.worker.providers import (
    CandidateTag,
    ClipTagger,
    MattingResult,
    NoOpMatting,
    NoOpTagger,
    TagSuggestion,
    VlmTagger,
    build_matting,
    build_tagger,
)
from app.worker.runner import claim_garment, next_pending_ids, requeue_stuck, run_once


# ---------- 假 provider ----------


class FakeMatting:
    """返回一张固定 RGBA 图（模拟抠图产物）。"""

    name = "fake"

    def __init__(self, image: Image.Image | None = None, error: str | None = None) -> None:
        self._image = image
        self._error = error

    def remove(self, image: Image.Image) -> MattingResult:
        if self._error:
            return MattingResult(image=None, provider=self.name, error=self._error)
        out = self._image or Image.new("RGBA", (32, 32), (10, 20, 30, 0))
        return MattingResult(image=out, provider=self.name)


class FakeTagger:
    """挑前 N 个候选 + 给个名字，模拟 VLM / CLIP。"""

    name = "fake"

    def __init__(self, name: str | None = "黑色针织衫", limit: int = 2, error: str | None = None):
        self._name = name
        self._limit = limit
        self._error = error
        #: 流水线回带给模型的已有名字（断言用）
        self.existing_names: list[str] | None = None

    async def suggest(
        self,
        image: Image.Image,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
    ) -> TagSuggestion:
        self.existing_names = existing_names
        if self._error:
            return TagSuggestion(error=self._error)
        return TagSuggestion(
            name=self._name,
            tag_value_ids=[c.value_id for c in candidates[: self._limit]],
            confidence=0.9,
        )


class RecordingMatting:
    """记下「喂进来的图到底多大」，用来断言抠图前的缩放确实生效。"""

    name = "recording"

    def __init__(self) -> None:
        self.size: tuple[int, int] | None = None

    def remove(self, image: Image.Image) -> MattingResult:
        self.size = image.size
        return MattingResult(image=Image.new("RGBA", (32, 32), (0, 0, 0, 0)), provider=self.name)


class LegacyTagger:
    """老签名：只收 image + candidates（模拟没跟上 existing_names 的自定义 provider）。"""

    name = "legacy"

    async def suggest(self, image: Image.Image, candidates: list[CandidateTag]) -> TagSuggestion:
        return TagSuggestion(name="老接口名字")


# ---------- 夹具 ----------


def _png(color: tuple[int, int, int] = (200, 40, 40), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def _make_garment(
    session: AsyncSession,
    owner_id: int,
    name: str = "测试衣物",
    status: str = GarmentStatus.PENDING,
    with_image: bool = True,
    image_png: bytes | None = None,
) -> Garment:
    garment = Garment(owner_id=owner_id, name=name, status=status)
    session.add(garment)
    await session.flush()

    if with_image:
        meta = store_image(owner_id, image_png or _png(), "image/png")
        session.add(
            GarmentImage(
                garment_id=garment.id,
                owner_id=owner_id,
                kind="original",
                storage_key=str(meta["storage_key"]),
                thumb_small_key=str(meta["thumb_small_key"]),
                thumb_medium_key=str(meta["thumb_medium_key"]),
                width=int(meta["width"]),  # type: ignore[arg-type]
                height=int(meta["height"]),  # type: ignore[arg-type]
                bytes=int(meta["bytes"]),  # type: ignore[arg-type]
                content_type="image/png",
                sort_order=0,
            )
        )
    await session.commit()
    return garment


async def _reload(session: AsyncSession, garment_id: int) -> Garment:
    return (
        await session.execute(
            select(Garment).where(Garment.id == garment_id)
        )
    ).scalar_one()


def _suggestion(garment: Garment) -> dict:
    assert garment.ai_suggestion
    return json.loads(garment.ai_suggestion)


# ---------- 抠图前的缩放（内存安全阀） ----------


def test_fit_for_ai_scales_long_edge_to_limit():
    """3500×4500 的手机原图 → 长边 1600，宽高比不变。"""
    out = _fit_for_ai(Image.new("RGB", (4500, 3500)), 1600)
    assert out.size == (1600, 1244)


def test_fit_for_ai_keeps_small_image_as_is():
    """已经够小的图不该被重新采样（白白损失画质）。"""
    img = Image.new("RGB", (800, 600))
    assert _fit_for_ai(img, 1600) is img


def test_fit_for_ai_zero_disables_scaling():
    img = Image.new("RGB", (4000, 3000))
    assert _fit_for_ai(img, 0) is img


@pytest.mark.parametrize(
    ("src", "limit", "expect"),
    [
        ((6000, 4000), 1600, (1600, 1067)),  # 横幅
        ((3000, 6000), 1600, (800, 1600)),   # 竖幅
        ((1600, 1600), 1600, (1600, 1600)),  # 正好在边界
    ],
)
def test_fit_for_ai_handles_landscape_portrait_and_boundary(src, limit, expect):
    assert _fit_for_ai(Image.new("RGB", src), limit).size == expect


async def test_pipeline_downscales_phone_photo_before_matting(session_factory, user, monkeypatch):
    """★ 15.7 MP 的手机原图必须在进抠图前缩到 1600 长边。

    这是 2026-10-17 那次事故的回归测试：rembg 的 alpha matting 会按**原尺寸**跑
    pymatting，3500×4500 一张就能把 4 GB 内存吃光、连宿主机一起拖到断电。
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "ai_input_max_edge", 1600, raising=False)

    async with session_factory() as s:
        g = await _make_garment(s, user.id, image_png=_png(size=(3500, 4500)))
        rec = RecordingMatting()
        await process_garment(s, g.id, matting=rec)
        assert rec.size == (1244, 1600)


async def test_pipeline_keeps_review_status_after_downscale(session_factory, user):
    """缩放不该影响状态落点：抠出图了就该是 needs_review。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id, image_png=_png(size=(3000, 2000)))
        status = await process_garment(s, g.id, matting=RecordingMatting())
        assert status == GarmentStatus.NEEDS_REVIEW


# ---------- 状态落点 ----------


async def test_no_provider_pending_goes_ready(session_factory, user):
    """provider 都是 none：什么也没产出，直接 ready，不拿空弹窗烦用户。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(s, g.id)

        assert status == GarmentStatus.READY
        fresh = await _reload(s, g.id)
        assert fresh.ai_suggestion is None
        assert fresh.processed_at is not None


async def test_matting_creates_cutout_and_needs_review(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(s, g.id, FakeMatting(), NoOpTagger())

        assert status == GarmentStatus.NEEDS_REVIEW
        fresh = (
            await s.execute(
                select(GarmentImage).where(
                    GarmentImage.garment_id == g.id, GarmentImage.kind == "cutout"
                )
            )
        ).scalars().all()
        assert len(fresh) == 1
        assert fresh[0].sort_order == 1  # 排在原图之后，不抢封面


async def test_tagger_writes_suggestion(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(s, g.id, NoOpMatting(), FakeTagger())

        assert status == GarmentStatus.NEEDS_REVIEW
        data = _suggestion(await _reload(s, g.id))
        assert data["name"] == "黑色针织衫"
        assert len(data["tag_value_ids"]) == 2
        assert data["provider"] == "fake"
        assert data["confidence"] == 0.9


async def test_error_without_output_becomes_failed(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(
            s, g.id, FakeMatting(error="模型没下载下来"), FakeTagger(name=None, error="VLM 超时")
        )

        assert status == GarmentStatus.FAILED
        data = _suggestion(await _reload(s, g.id))
        assert "模型没下载下来" in data["error"]
        assert "VLM 超时" in data["error"]
        assert data["tag_value_ids"] == []


async def test_partial_success_still_needs_review(session_factory, user):
    """抠图成功、打标报错：有产出就给人看，错误信息一并带上。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(s, g.id, FakeMatting(), FakeTagger(name=None, error="VLM 超时"))

        assert status == GarmentStatus.NEEDS_REVIEW
        data = _suggestion(await _reload(s, g.id))
        assert "VLM 超时" in data["error"]


async def test_without_image_no_provider_is_ready(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id, with_image=False)
        status = await process_garment(s, g.id, NoOpMatting(), NoOpTagger())
        assert status == GarmentStatus.READY


async def test_missing_garment_returns_empty(session_factory, user):
    async with session_factory() as s:
        assert await process_garment(s, 999999) == ""


# ---------- 命名：特征 + 避重 ----------


def test_prompt_asks_for_feature_rich_name():
    """名字要能体现特征：提示词里必须给出颜色/图案/材质/结构/品类这些维度。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    prompt = provider._prompt([CandidateTag(1, "品类", "T恤", parent_value="上衣")])

    for keyword in ("颜色", "图案", "材质", "结构细节", "品类", "区分开"):
        assert keyword in prompt, f"提示词缺少「{keyword}」维度"
    assert "8~18" in prompt  # 别再回到「不超过 12 个字」的粗粒度


def test_prompt_passes_existing_names_to_avoid_duplicates():
    """衣橱里已有的名字要回带给模型，并要求它不重复。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    prompt = provider._prompt(
        [CandidateTag(1, "品类", "T恤", parent_value="上衣")],
        ["黑色T恤", "白色衬衫"],
    )
    assert "黑色T恤" in prompt
    assert "白色衬衫" in prompt
    assert "不重复" in prompt


def test_prompt_without_history_is_clean():
    """没有历史名字时不要出现空的「已经有这些名字」段落。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    assert "衣橱里已经有" not in provider._prompt([])
    assert "衣橱里已经有" not in provider._prompt([], [])


async def test_pipeline_hands_existing_names_to_tagger(session_factory, user):
    """流水线要把同 owner 的已有名字传进 provider（排除自己）。"""
    async with session_factory() as s:
        await _make_garment(s, user.id, "黑色针织衫", status=GarmentStatus.READY)
        g = await _make_garment(s, user.id)

        tagger = FakeTagger()
        await process_garment(s, g.id, NoOpMatting(), tagger)

        assert tagger.existing_names is not None
        assert "黑色针织衫" in tagger.existing_names
        assert "测试衣物" not in tagger.existing_names  # 自己不算


async def test_duplicate_ai_name_gets_suffix(session_factory, user):
    """模型没躲开重名：建议名自动加序号，绝不让两件衣服同名。"""
    async with session_factory() as s:
        await _make_garment(s, user.id, "黑色针织衫", status=GarmentStatus.READY)
        second = await _make_garment(s, user.id, "第二件")
        third = await _make_garment(s, user.id, "第三件")

        await process_garment(s, second.id, NoOpMatting(), FakeTagger(name="黑色针织衫"))
        await process_garment(s, third.id, NoOpMatting(), FakeTagger(name="黑色针织衫"))

        assert _suggestion(await _reload(s, second.id))["name"] == "黑色针织衫 (2)"
        assert _suggestion(await _reload(s, third.id))["name"] == "黑色针织衫 (3)"


async def test_unique_ai_name_is_kept(session_factory, user):
    """不重名就原样保留，别乱加后缀。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        await process_garment(s, g.id, NoOpMatting(), FakeTagger(name="米白竖条纹衬衫"))
        assert _suggestion(await _reload(s, g.id))["name"] == "米白竖条纹衬衫"


async def test_rerun_keeps_its_own_name(session_factory, user):
    """回归：重跑自己时，自己的旧名字不算重名（不能给自己加 (2)）。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id, "黑色针织衫")
        tagger = FakeTagger(name="黑色针织衫")

        await process_garment(s, g.id, NoOpMatting(), tagger)
        assert _suggestion(await _reload(s, g.id))["name"] == "黑色针织衫"

        await s.execute(
            update(Garment).where(Garment.id == g.id).values(status=GarmentStatus.PENDING)
        )
        await s.commit()
        await process_garment(s, g.id, NoOpMatting(), tagger)
        assert _suggestion(await _reload(s, g.id))["name"] == "黑色针织衫"


async def test_legacy_tagger_signature_still_works(session_factory, user):
    """只收两个参数的 provider 不能被新参数炸掉（inspect 兜底）。"""
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        status = await process_garment(s, g.id, NoOpMatting(), LegacyTagger())
        assert status == GarmentStatus.NEEDS_REVIEW
        assert _suggestion(await _reload(s, g.id))["name"] == "老接口名字"


# ---------- 队列抢占 ----------


async def test_claim_is_exclusive(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        assert await claim_garment(s, g.id) is True
        assert await claim_garment(s, g.id) is False  # 已经是 processing，抢不到


async def test_claim_rejects_non_pending(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id, status=GarmentStatus.READY)
        assert await claim_garment(s, g.id) is False


async def test_run_once_only_touches_pending(session_factory, user):
    async with session_factory() as s:
        ready = await _make_garment(s, user.id, "已完成", status=GarmentStatus.READY)
        await _make_garment(s, user.id, "待处理一")
        await _make_garment(s, user.id, "待处理二")

        handled = await run_once(s, limit=10)

        assert handled == 2
        assert (await _reload(s, ready.id)).status == GarmentStatus.READY


async def test_run_once_specific_garment(session_factory, user):
    async with session_factory() as s:
        a = await _make_garment(s, user.id, "A")
        b = await _make_garment(s, user.id, "B")

        assert await run_once(s, garment_id=b.id) == 1
        assert (await _reload(s, b.id)).status == GarmentStatus.READY
        assert (await _reload(s, a.id)).status == GarmentStatus.PENDING


async def test_run_once_marks_failed_on_pipeline_error(session_factory, user, monkeypatch):
    """process_garment 自己炸了也不能拖垮循环：单件 failed，其余照跑。"""
    import app.worker.runner as runner

    async def boom(*_args, **_kwargs):
        raise RuntimeError("数据库连接断了")

    monkeypatch.setattr(runner, "process_garment", boom)

    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        assert await run_once(s, limit=1) == 1
        assert (await _reload(s, g.id)).status == GarmentStatus.FAILED


# ---------- 时间列时区语义（PG 专属坑，SQLite 不会报错） ----------


def test_now_naive_utc_has_no_tzinfo():
    """processed_at 是 `timestamp without time zone`，只能写 naive。"""
    from app.core.time import now_naive_utc

    dt = now_naive_utc()
    assert dt.tzinfo is None
    assert dt.year >= 2024


async def test_fail_writes_naive_processed_at_and_aware_updated_at(session_factory, user, monkeypatch):
    """★ 2026-10-17 线上事故回归：`_fail` 里两个时间列的时区语义不能混用。

    现场日志：超时后 `_fail` 写库抛
    `TypeError: can't subtract offset-naive and offset-aware datetimes`
    （asyncpg 对 `TIMESTAMP WITHOUT TIME ZONE` 拒绝 aware 值），异常从 `_fail`
    里冒出去 → 这件衣物永远停在 processing → 30 分钟后回收重跑 → 无限循环。
    SQLite 不校验时区，所以这里靠**写入值来源**来断言：给两个 helper 打上
    不同年份的哨兵，读回来必须是 2001（processed_at）和 2002（updated_at）。
    """
    from datetime import UTC, datetime

    import app.worker.runner as runner

    monkeypatch.setattr(runner, "now_naive_utc", lambda: datetime(2001, 1, 1, 12, 0, 0), raising=False)
    monkeypatch.setattr(runner, "now_utc", lambda: datetime(2002, 2, 2, 12, 0, 0, tzinfo=UTC), raising=False)

    async with session_factory() as s:
        g = await _make_garment(s, user.id, status=GarmentStatus.PROCESSING)
        await runner._fail(s, g.id, "处理超时（>600s）")
        # populate_existing：_fail 是显式 Core UPDATE，identity map 里的对象还是旧的
        row = (
            await s.execute(
                select(Garment).where(Garment.id == g.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        status, processed, updated = row.status, row.processed_at, row.updated_at

    assert status == GarmentStatus.FAILED
    assert processed is not None and processed.year == 2001
    assert updated.year == 2002


async def test_timeout_marks_failed_and_flags_exit(session_factory, user, monkeypatch):
    """超时 → 判 failed（不是留在 processing）+ 打上「该重启进程」的标记。

    推理线程取消不掉，所以 run_once 只打标记、由 run_forever 退出进程；
    这里不跑 run_forever（会 os._exit 掉 pytest），只验标记与落库。
    """
    import app.worker.runner as runner
    from app.config import get_settings

    async def never_returns(*_args, **_kwargs):
        await asyncio.sleep(5)

    monkeypatch.setattr(runner, "process_garment", never_returns)
    monkeypatch.setattr(runner, "_timed_out", False, raising=False)
    monkeypatch.setattr(get_settings(), "worker_job_timeout_seconds", 0.05, raising=False)

    async with session_factory() as s:
        g = await _make_garment(s, user.id)
        assert await runner.run_once(s, limit=1) == 1
        row = (
            await s.execute(
                select(Garment).where(Garment.id == g.id).execution_options(populate_existing=True)
            )
        ).scalar_one()
        status, processed = row.status, row.processed_at

    assert status == GarmentStatus.FAILED
    assert processed is not None
    assert runner._timed_out is True


async def test_requeue_stuck(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id, status=GarmentStatus.PROCESSING)
        g.updated_at = now_utc() - timedelta(minutes=90)
        await s.commit()

        assert await requeue_stuck(s, 30) == 1
        assert (await _reload(s, g.id)).status == GarmentStatus.PENDING


async def test_requeue_keeps_fresh_processing(session_factory, user):
    async with session_factory() as s:
        g = await _make_garment(s, user.id, status=GarmentStatus.PROCESSING)
        assert await requeue_stuck(s, 30) == 0
        assert (await _reload(s, g.id)).status == GarmentStatus.PROCESSING


async def test_next_pending_ids_respects_limit(session_factory, user):
    async with session_factory() as s:
        for i in range(3):
            await _make_garment(s, user.id, f"衣物{i}")
        assert len(await next_pending_ids(s, 2)) == 2


# ---------- provider 组装与降级 ----------


def test_build_unknown_provider_falls_back_to_none():
    assert isinstance(build_matting("不存在"), NoOpMatting)
    assert isinstance(build_tagger("不存在"), NoOpTagger)
    assert isinstance(build_matting("none"), NoOpMatting)


def test_rembg_missing_dependency_degrades():
    """本机没装 rembg：不能抛异常，要给出可读的降级原因。"""
    provider = build_matting("rembg")
    result = provider.remove(Image.new("RGB", (8, 8)))
    assert result.ok is False
    # 装了 rembg 的机器（CI / Worker 镜像）会去加载模型，失败原因同样落在 error 里
    if result.error:
        assert result.provider == "rembg"


def test_clip_without_torch_degrades():
    provider = ClipTagger()
    import asyncio

    result = asyncio.run(
        provider.suggest(Image.new("RGB", (8, 8)), [CandidateTag(1, "颜色", "黑")])
    )
    assert result.tag_value_ids == []
    assert result.error is not None


def test_vlm_without_base_url_degrades():
    provider = VlmTagger(base_url=None)
    import asyncio

    result = asyncio.run(provider.suggest(Image.new("RGB", (8, 8)), []))
    assert result.is_empty
    assert "未配置" in (result.error or "")


def test_vlm_parses_fenced_json():
    provider = VlmTagger(base_url="http://example.invalid/v1")
    result = provider._parse(
        '好的，结果如下：\n```json\n{"name": "米白风衣", "tags": ["米白", "风衣"]}\n```',
        {"米白": 3, "风衣": 7},
    )
    assert result.name == "米白风衣"
    assert result.tag_value_ids == [3, 7]


def test_vlm_ignores_unknown_tags():
    provider = VlmTagger(base_url="http://example.invalid/v1")
    result = provider._parse('{"name": "裙子", "tags": ["不存在", "黑"]}', {"黑": 5})
    assert result.tag_value_ids == [5]
    assert result.name == "裙子"


def test_vlm_rejects_garbage():
    provider = VlmTagger(base_url="http://example.invalid/v1")
    result = provider._parse("这张图我看不懂", {"黑": 5})
    assert result.is_empty
    assert result.error is not None


def test_vlm_repairs_missing_comma():
    """模型写出的 JSON 数组常常少个逗号（CT202 实测复现），不能整条建议作废。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    broken = '{\n "name": "黑色玛丽珍鞋",\n "tags": [\n "鞋子",\n "鞋子/芭蕾鞋"\n "颜色/黑"\n ],\n "confidence": 0.9}'
    result = provider._parse(broken, {"鞋子": 1, "鞋子/芭蕾鞋": 2, "颜色/黑": 3})
    assert result.error is None, result.error
    assert result.name == "黑色玛丽珍鞋"
    assert result.tag_value_ids == [1, 2, 3]


def test_vlm_repairs_trailing_comma_and_curly_quotes():
    provider = VlmTagger(base_url="http://example.invalid/v1")
    result = provider._parse('{"name": "米白风衣", "tags": ["米白",],}', {"米白": 4})
    assert result.name == "米白风衣"
    assert result.tag_value_ids == [4]
    result = provider._parse('{"name": "米白风衣", "tags": [“米白”]}', {"米白": 4})
    assert result.tag_value_ids == [4]


def test_vlm_garbage_still_rejected():
    """宽容是有限度的：真的不是 JSON 就该报错，别瞎猜。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    assert provider._parse("这张图我看不懂", {}).error is not None


def test_vlm_http_error_keeps_upstream_detail():
    """上游的报错说明要带出来 —— 配错模型 / 密钥时用户得看得见原因。

    「model not found」= 模型名写错；「invalid content type」= 填了不支持看图的纯文本模型；
    「insufficient balance」= 没余额。只回一个 400 等于把排查成本丢给用户。
    """
    msg = VlmTagger._http_error(400, '{"error":{"message":"model not found: glm-4-flash"}}')
    assert "400" in msg
    assert "model not found" in msg
    # 换行 / 连续空白折成一行，且截断（别把整页 HTML 塞进提示里）
    long = VlmTagger._http_error(500, "\n".join("x" * 100 for _ in range(10)))
    assert "\n" not in long
    assert len(long) < 240
    assert "（无说明）" in VlmTagger._http_error(502, "   ")


def test_vlm_confidence_only_in_range():
    """置信度只认 0~1 的数字；模型瞎填的当作没给。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    assert provider._parse('{"name": "T恤", "tags": [], "confidence": 0.73}', {}).confidence == 0.73
    assert provider._parse('{"name": "T恤", "confidence": "high"}', {}).confidence is None
    assert provider._parse('{"name": "T恤", "confidence": 42}', {}).confidence is None


def test_vlm_matches_bare_value_name():
    """模型常只回取值名（「T恤」而不是「上衣/T恤」）—— 取值名不重复时要能认出来。

    小类现在整套预置，候选标签里带父值前缀，不兼容裸名会大量丢标签。
    """
    provider = VlmTagger(base_url="http://example.invalid/v1")
    by_label = {"上衣/T恤": 1, "上衣": 2, "黑": 3}
    by_value = {"T恤": 1, "上衣": 2, "黑": 3}
    result = provider._parse(
        '{"name": "黑T恤", "tags": ["上衣", "T恤", "黑"]}', by_label, by_value
    )
    assert result.tag_value_ids == [2, 1, 3]


def test_vlm_strips_category_prefix_from_tag():
    """模型偶尔把类别前缀一起带回来（如「颜色:黑」），要能剥掉后认出来。"""
    provider = VlmTagger(base_url="http://example.invalid/v1")
    result = provider._parse('{"name": "黑西装", "tags": ["颜色:黑"]}', {"黑": 9, "颜色:黑": 1})
    assert result.tag_value_ids == [1]
    result = provider._parse('{"name": "黑西装", "tags": ["颜色: 黑"]}', {"黑": 9})
    assert result.tag_value_ids == [9]


# ---------- 回归：重复处理不能卡在 processing ----------


async def test_reprocess_same_garment_lands_in_needs_review(session_factory, user):
    """回归：同一件衣物重复跑流水线，库里的状态必须落到 needs_review。

    踩过的坑（CT202 实测复现）：Worker 整进程共用一个 AsyncSession
    （db.py 里 expire_on_commit=False），identity map 里缓存的 Garment 对象属性
    **停留在上一轮的终值**。重跑时若本轮结果与上轮完全一致（同一张原图 + 同一个
    模型，几乎必然如此），``garment.status = X`` 会被 SQLAlchemy 判定为「没有变化」，
    UPDATE 语句里不带 status / ai_suggestion 列 —— 而 claim 已经把库里的状态改成了
    processing，于是衣物永久卡在处理中；前端只要看到 processing 就一直轮询，
    表现就是「一直处理中 + 页面不断刷新」。

    这个测试必须用**同一个 session** 连跑两轮，否则复现不出来。
    """
    matting = FakeMatting()
    tagger = FakeTagger()

    async with session_factory() as s:
        garment = await _make_garment(s, user.id, name="重复处理测试")
        gid = garment.id

        # 第一轮：pending → needs_review
        first = await process_garment(s, gid, matting, tagger)
        assert first == GarmentStatus.NEEDS_REVIEW

        # 模拟用户在界面上复核通过 → ready（并清空建议），然后再次点「AI 抠图打标」
        for value in (GarmentStatus.READY, GarmentStatus.PENDING):
            await s.execute(
                update(Garment).where(Garment.id == gid).values(status=value)
            )
        await s.execute(
            update(Garment).where(Garment.id == gid).values(ai_suggestion=None)
        )
        await s.commit()

        # 第二轮：同样的 provider、同样的产物 —— 关键回归点
        second = await process_garment(s, gid, matting, tagger)
        assert second == GarmentStatus.NEEDS_REVIEW

        # ⚠️ 必须核实**库里**的值：这个 bug 的特征就是返回值对、库里不对
        row = (
            await s.execute(
                select(Garment.status, Garment.ai_suggestion).where(Garment.id == gid)
            )
        ).one()
        assert row.status == GarmentStatus.NEEDS_REVIEW
        assert row.ai_suggestion is not None
        assert json.loads(row.ai_suggestion)["provider"] == "fake+fake"


async def test_reprocess_replaces_cutout_not_accumulates(session_factory, user):
    """回归：重跑流水线要替换旧抠图，不能每跑一次就多攒一张。"""
    matting = FakeMatting()
    tagger = FakeTagger()

    async with session_factory() as s:
        garment = await _make_garment(s, user.id, name="抠图累加测试")
        gid = garment.id

        await process_garment(s, gid, matting, tagger)
        after_first = await _reload(s, gid)
        cutouts_1 = [i for i in after_first.images if i.kind == "cutout"]
        assert len(cutouts_1) == 1

        # 再跑两次
        for _ in range(2):
            await s.execute(
                update(Garment).where(Garment.id == gid).values(status=GarmentStatus.PENDING)
            )
            await s.commit()
            await process_garment(s, gid, matting, tagger)

        after_more = await _reload(s, gid)
        cutouts_2 = [i for i in after_more.images if i.kind == "cutout"]
        originals = [i for i in after_more.images if i.kind == "original"]

        # 抠图始终只有一张，原图不会被吃掉
        assert len(cutouts_2) == 1
        assert len(originals) == 1
