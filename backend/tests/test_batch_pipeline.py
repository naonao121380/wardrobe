"""批量入队 / 队列状态 / 「还没抠图」筛选。

前端的批量交互依赖三条接口：

- ``POST /garments/batch/process`` —— 一次把几十件丢进队列，别让前端 N 次往返
- ``GET  /garments/queue-status`` —— 只回计数，供前端判断这批活儿跑完没
- ``GET  /garments?unprocessed=1`` —— 「一键选出未抠图打标」的实现基础

另外这里顺带把**路由顺序**钉死：``/garments/batch/process`` 与
``/garments/{garment_id}/process`` 是同一个形状，``/garments/queue-status`` 与
``/garments/{garment_id}`` 也是。FastAPI 按注册顺序匹配，一旦有人把通配路由挪到
前面，批量接口就会被静默吃掉（表现为 404「衣物不存在」）—— 上面每条用例都
能挡住这种回归。
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.media import store_image
from app.models.garment import Garment, GarmentImage, GarmentStatus


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 40, 40)).save(buf, format="PNG")
    return buf.getvalue()


async def _new(client: AsyncClient, headers, name: str) -> int:
    r = await client.post("/api/garments", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _add_cutout(factory: async_sessionmaker, owner_id: int, garment_id: int) -> None:
    """直接落一条 kind=cutout 的图片记录，模拟 Worker 已经处理过。"""
    async with factory() as s:
        meta = store_image(owner_id, _png(), "image/png")
        n = len(
            (
                await s.execute(
                    select(GarmentImage).where(GarmentImage.garment_id == garment_id)
                )
            )
            .scalars()
            .all()
        )
        s.add(
            GarmentImage(
                garment_id=garment_id,
                owner_id=owner_id,
                kind="cutout",
                storage_key=str(meta["storage_key"]),
                thumb_small_key=str(meta["thumb_small_key"]),
                thumb_medium_key=str(meta["thumb_medium_key"]),
                width=int(meta["width"]),
                height=int(meta["height"]),
                bytes=int(meta["bytes"]),
                content_type="image/png",
                sort_order=n,
            )
        )
        await s.commit()


# ---------- 批量入队 ----------


async def test_batch_process_enqueues_all(client: AsyncClient, auth_headers, session_factory, user):
    ids = [await _new(client, auth_headers, f"批量{i}") for i in range(3)]

    r = await client.post("/api/garments/batch/process", json={"garment_ids": ids}, headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["enqueued"]) == sorted(ids)
    assert body["skipped"] == []

    # 库里确实变成 pending
    async with session_factory() as s:
        rows = (await s.execute(select(Garment.status).where(Garment.id.in_(ids)))).scalars().all()
    assert set(rows) == {GarmentStatus.PENDING}


async def test_batch_process_is_idempotent(client: AsyncClient, auth_headers, session_factory, user):
    """连点两次「一键抠图打标」不该报错，也不该产生第二个排队任务。"""
    gid = await _new(client, auth_headers, "重复入队")

    for _ in range(2):
        r = await client.post(
            "/api/garments/batch/process", json={"garment_ids": [gid]}, headers=auth_headers
        )
        assert r.status_code == 200, r.text
        assert r.json()["enqueued"] == [gid]

    async with session_factory() as s:
        rows = (await s.execute(select(Garment.status).where(Garment.id == gid))).scalars().all()
    assert rows == [GarmentStatus.PENDING]


async def test_batch_process_skips_processing(client: AsyncClient, auth_headers, session_factory, user):
    """Worker 正在跑的那件跳过，但**不连累其它件**。"""
    busy_id = await _new(client, auth_headers, "正在处理")
    ok_id = await _new(client, auth_headers, "可以处理")

    async with session_factory() as s:
        await s.execute(
            update(Garment).where(Garment.id == busy_id).values(status=GarmentStatus.PROCESSING)
        )
        await s.commit()

    r = await client.post(
        "/api/garments/batch/process", json={"garment_ids": [busy_id, ok_id]}, headers=auth_headers
    )
    body = r.json()
    assert body["enqueued"] == [ok_id]
    assert body["skipped"] == [{"id": busy_id, "reason": "正在处理中"}]


async def test_batch_process_skips_missing_and_foreign(
    client: AsyncClient, auth_headers, second_headers, session_factory, user
):
    """不存在的 id 和别人的衣物都只是跳过，整批不失败。"""
    mine = await _new(client, auth_headers, "我的")
    theirs = await _new(client, second_headers, "别人的")
    ghost = 999_999

    r = await client.post(
        "/api/garments/batch/process",
        json={"garment_ids": [mine, theirs, ghost]},
        headers=auth_headers,
    )
    body = r.json()
    assert body["enqueued"] == [mine]
    reasons = {item["id"]: item["reason"] for item in body["skipped"]}
    assert reasons == {theirs: "衣物不存在", ghost: "衣物不存在"}

    # 别人的衣物状态没被改动
    async with session_factory() as s:
        status = (await s.execute(select(Garment.status).where(Garment.id == theirs))).scalar_one()
    assert status == GarmentStatus.READY


async def test_batch_process_rejects_empty(client: AsyncClient, auth_headers):
    r = await client.post("/api/garments/batch/process", json={"garment_ids": []}, headers=auth_headers)
    assert r.status_code == 400, r.text


# ---------- 队列状态 ----------


async def test_queue_status_counts(client: AsyncClient, auth_headers, session_factory, user):
    pending = [await _new(client, auth_headers, f"排队{i}") for i in range(2)]
    review = await _new(client, auth_headers, "待复核")

    async with session_factory() as s:
        for gid, status in [
            (pending[0], GarmentStatus.PENDING),
            (pending[1], GarmentStatus.PENDING),
            (review, GarmentStatus.NEEDS_REVIEW),
        ]:
            await s.execute(update(Garment).where(Garment.id == gid).values(status=status))
        await s.commit()

    r = await client.get("/api/garments/queue-status", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["pending"] == 2
    assert body["needs_review"] == 1
    assert body["processing"] == 0
    assert body["failed"] == 0


async def test_queue_status_isolated_per_user(
    client: AsyncClient, auth_headers, second_headers, session_factory, user
):
    theirs = await _new(client, second_headers, "别人的排队")
    async with session_factory() as s:
        await s.execute(
            update(Garment).where(Garment.id == theirs).values(status=GarmentStatus.PENDING)
        )
        await s.commit()

    r = await client.get("/api/garments/queue-status", headers=auth_headers)
    assert r.json()["pending"] == 0


# ---------- 未抠图筛选 ----------


async def test_unprocessed_filter_only_returns_cutoutless(
    client: AsyncClient, auth_headers, session_factory, user
):
    raw = await _new(client, auth_headers, "没抠过图")
    done = await _new(client, auth_headers, "抠过图了")
    await _add_cutout(session_factory, user.id, done)

    r = await client.get("/api/garments?unprocessed=1", headers=auth_headers)
    assert r.status_code == 200, r.text
    names = {g["name"] for g in r.json()["items"]}
    assert "没抠过图" in names
    assert "抠过图了" not in names

    # 不带参数时照常全量返回
    r2 = await client.get("/api/garments", headers=auth_headers)
    assert {g["name"] for g in r2.json()["items"]} >= {"没抠过图", "抠过图了"}


async def test_unprocessed_ignores_status(client: AsyncClient, auth_headers, session_factory, user):
    """老数据被迁移回填成 ready 但没有抠图 —— 这种也要能被选出来。"""
    gid = await _new(client, auth_headers, "迁移来的老衣物")
    async with session_factory() as s:
        await s.execute(update(Garment).where(Garment.id == gid).values(status=GarmentStatus.READY))
        await s.commit()

    r = await client.get("/api/garments?unprocessed=1", headers=auth_headers)
    assert gid in {g["id"] for g in r.json()["items"]}


async def test_unprocessed_false_behaves_like_all(client: AsyncClient, auth_headers):
    r = await client.get("/api/garments?unprocessed=0", headers=auth_headers)
    assert r.status_code == 200, r.text
