"""批量删除 / 批量改标签 / 批量上传。

三条接口都服务于同一个场景：**一次收拾一大堆衣服**。所以它们共享同一套原则 ——
单个成员出问题只是跳过并说明，不整批失败；跨用户 / 不存在的 id 一律当作没看见。

⚠️ 路由顺序同样钉在这里：``/garments/batch/*`` 必须注册在 ``/{garment_id}`` 之前，
否则会被通配路由吃掉（表现为 404「衣物不存在」）。
"""

from __future__ import annotations

import io

import pytest
from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.models.garment import Garment, GarmentImage, GarmentStatus


def _png(color: tuple[int, int, int] = (200, 40, 40), size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def _new(client: AsyncClient, headers, name: str) -> int:
    r = await client.post("/api/garments", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return int(r.json()["id"])


async def _tag_ids(client: AsyncClient, headers, *names: str) -> list[int]:
    """按取值名取 id（默认分类树里都有）。"""
    r = await client.get("/api/tags/categories?scope=garment", headers=headers)
    assert r.status_code == 200, r.text
    out = []
    for name in names:
        hit = [
            v["id"]
            for c in r.json()
            for v in c["values"]
            if v["value"] == name
        ]
        assert hit, f"默认标签里没有 {name}"
        out.append(hit[0])
    return out


# ---------- 批量删除 ----------


async def test_batch_delete_removes_garments_and_files(
    client: AsyncClient, auth_headers, session_factory, user
):
    keep = await _new(client, auth_headers, "留下")
    gone = [await _new(client, auth_headers, f"删掉{i}") for i in range(2)]

    # 给其中一件挂张图，确认图片记录也跟着走
    r = await client.post(
        f"/api/garments/{gone[0]}/images",
        files={"files": ("a.png", _png(), "image/png")},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text

    r = await client.post(
        "/api/garments/batch/delete", json={"garment_ids": gone}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["deleted"]) == sorted(gone)
    assert body["deleted_images"] == 1
    assert body["skipped"] == []

    async with session_factory() as s:
        left = (await s.execute(select(Garment.id).where(Garment.owner_id == user.id))).scalars().all()
        imgs = (
            await s.execute(select(GarmentImage.id).where(GarmentImage.garment_id.in_(gone)))
        ).scalars().all()
    assert keep in left
    assert set(gone).isdisjoint(left)
    assert imgs == []


async def test_batch_delete_skips_missing_and_foreign(
    client: AsyncClient, auth_headers, second_headers, session_factory, user
):
    mine = await _new(client, auth_headers, "我的")
    theirs = await _new(client, second_headers, "别人的")
    ghost = 999_999

    r = await client.post(
        "/api/garments/batch/delete",
        json={"garment_ids": [mine, theirs, ghost]},
        headers=auth_headers,
    )
    body = r.json()
    assert body["deleted"] == [mine]
    assert {i["id"] for i in body["skipped"]} == {theirs, ghost}

    async with session_factory() as s:
        still = (await s.execute(select(Garment.id).where(Garment.id == theirs))).scalar_one_or_none()
    assert still == theirs  # 别人的没被删


async def test_batch_delete_is_idempotent(client: AsyncClient, auth_headers):
    gid = await _new(client, auth_headers, "删两次")
    first = await client.post(
        "/api/garments/batch/delete", json={"garment_ids": [gid]}, headers=auth_headers
    )
    assert first.json()["deleted"] == [gid]
    second = await client.post(
        "/api/garments/batch/delete", json={"garment_ids": [gid]}, headers=auth_headers
    )
    assert second.status_code == 200
    assert second.json()["deleted"] == []
    assert second.json()["skipped"][0]["id"] == gid


async def test_batch_delete_rejects_empty(client: AsyncClient, auth_headers):
    r = await client.post("/api/garments/batch/delete", json={"garment_ids": []}, headers=auth_headers)
    assert r.status_code == 400, r.text


# ---------- 批量改标签 ----------


async def test_batch_tags_appends(client: AsyncClient, auth_headers):
    ids = [await _new(client, auth_headers, f"加标签{i}") for i in range(2)]
    (top,) = await _tag_ids(client, auth_headers, "上衣")

    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": ids, "tag_value_ids": [top]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert sorted(r.json()["updated"]) == sorted(ids)

    for gid in ids:
        g = await client.get(f"/api/garments/{gid}", headers=auth_headers)
        assert top in g.json()["tag_value_ids"]


async def test_batch_tags_keeps_existing(client: AsyncClient, auth_headers):
    """追加模式不能把已有标签冲掉。"""
    gid = await _new(client, auth_headers, "已有标签的")
    black, top = await _tag_ids(client, auth_headers, "黑", "上衣")

    r = await client.patch(
        f"/api/garments/{gid}", json={"tag_value_ids": [black]}, headers=auth_headers
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": [gid], "tag_value_ids": [top]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text

    g = await client.get(f"/api/garments/{gid}", headers=auth_headers)
    assert set(g.json()["tag_value_ids"]) == {black, top}


async def test_batch_tags_remove_mode(client: AsyncClient, auth_headers):
    gid = await _new(client, auth_headers, "待删标签")
    black, top = await _tag_ids(client, auth_headers, "黑", "上衣")
    await client.patch(
        f"/api/garments/{gid}", json={"tag_value_ids": [black, top]}, headers=auth_headers
    )

    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": [gid], "tag_value_ids": [top], "mode": "remove"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    g = await client.get(f"/api/garments/{gid}", headers=auth_headers)
    assert g.json()["tag_value_ids"] == [black]


async def test_batch_tags_replace_mode(client: AsyncClient, auth_headers):
    gid = await _new(client, auth_headers, "待覆盖标签")
    black, top = await _tag_ids(client, auth_headers, "黑", "上衣")
    await client.patch(f"/api/garments/{gid}", json={"tag_value_ids": [black]}, headers=auth_headers)

    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": [gid], "tag_value_ids": [top], "mode": "replace"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    g = await client.get(f"/api/garments/{gid}", headers=auth_headers)
    assert g.json()["tag_value_ids"] == [top]


async def test_batch_tags_skips_foreign_garment(
    client: AsyncClient, auth_headers, second_headers
):
    mine = await _new(client, auth_headers, "我的")
    theirs = await _new(client, second_headers, "别人的")
    (top,) = await _tag_ids(client, auth_headers, "上衣")

    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": [mine, theirs], "tag_value_ids": [top]},
        headers=auth_headers,
    )
    body = r.json()
    assert body["updated"] == [mine]
    assert body["skipped"][0]["id"] == theirs

    # 别人的衣物不能被打上我的标签
    r = await client.get("/api/garments", headers=second_headers)
    assert r.status_code == 200


@pytest.mark.parametrize("payload", [
    {"garment_ids": [], "tag_value_ids": [1]},
    {"garment_ids": [1], "tag_value_ids": []},
    {"garment_ids": [1], "tag_value_ids": [1], "mode": "wat"},
])
async def test_batch_tags_rejects_bad_payload(client: AsyncClient, auth_headers, payload):
    r = await client.post("/api/garments/batch/tags", json=payload, headers=auth_headers)
    assert r.status_code == 400, r.text


async def test_batch_tags_rejects_invalid_tag(client: AsyncClient, auth_headers):
    gid = await _new(client, auth_headers, "无效标签")
    r = await client.post(
        "/api/garments/batch/tags",
        json={"garment_ids": [gid], "tag_value_ids": [999_999]},
        headers=auth_headers,
    )
    assert r.status_code == 400, r.text


# ---------- 批量上传 ----------


async def test_batch_upload_creates_one_garment_per_file(
    client: AsyncClient, auth_headers, session_factory, user
):
    files = [("files", (f"{i}.png", _png(), "image/png")) for i in range(3)]
    r = await client.post(
        "/api/garments/batch/upload",
        files=files,
        data={"name": "2026-10-15 09:20"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["created"]) == 3
    assert body["skipped"] == []
    # 名字带序号，免得一整墙同名
    assert [g["name"] for g in body["created"]] == [
        "2026-10-15 09:20",
        "2026-10-15 09:20 (2)",
        "2026-10-15 09:20 (3)",
    ]

    async with session_factory() as s:
        imgs = (
            await s.execute(
                select(GarmentImage.id).where(
                    GarmentImage.garment_id.in_([g["id"] for g in body["created"]])
                )
            )
        ).scalars().all()
    assert len(imgs) == 3
    for g in body["created"]:
        assert g["cover_url"] is not None


async def test_batch_upload_start_index_keeps_numbering_across_batches(
    client: AsyncClient, auth_headers, session_factory, user
):
    """前端按请求体大小分批发，``start_index`` 要让命名**跨批连续**。

    不传的话第二批又从 ``(2)`` 开始，一整墙衣服会出现一堆同名 —— 用户按名字
    找衣服就废了。这里模拟「3 张一批 + 2 张一批」，按前端的方式累加已建件数。
    """
    seen: list[str] = []
    created_total = 0
    for batch in (3, 2):
        r = await client.post(
            "/api/garments/batch/upload",
            files=[("files", (f"{i}.png", _png(), "image/png")) for i in range(batch)],
            data={"name": "2026-10-15 09:20", "start_index": str(created_total)},
            headers=auth_headers,
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert len(body["created"]) == batch
        seen += [g["name"] for g in body["created"]]
        created_total += len(body["created"])

    assert seen == [
        "2026-10-15 09:20",
        "2026-10-15 09:20 (2)",
        "2026-10-15 09:20 (3)",
        "2026-10-15 09:20 (4)",
        "2026-10-15 09:20 (5)",
    ]


async def test_batch_upload_rejects_negative_start_index(client: AsyncClient, auth_headers):
    """负数序号是客户端算错了，直接 422，别默默按 0 处理成重名。"""
    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
        data={"start_index": "-1"},
        headers=auth_headers,
    )
    assert r.status_code == 422, r.text


async def test_batch_upload_defaults_name_to_now(client: AsyncClient, auth_headers):
    """不传 name 时后端自己取当前时间，格式 YYYY-MM-DD HH:MM。"""
    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    name = r.json()["created"][0]["name"]
    assert len(name) == 16 and name[4] == "-" and name[7] == "-" and name[10] == " ", name


async def test_batch_upload_can_enqueue(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
        data={"enqueue": "true"},
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["created"][0]["status"] == GarmentStatus.PENDING


async def test_batch_upload_status_ready_without_enqueue(client: AsyncClient, auth_headers):
    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", ("a.png", _png(), "image/png"))],
        headers=auth_headers,
    )
    assert r.json()["created"][0]["status"] == GarmentStatus.READY


async def test_batch_upload_skips_bad_file_but_keeps_others(
    client: AsyncClient, auth_headers, monkeypatch
):
    """一张糊了 / 超了，其余照常建 —— 拍 30 张不该因为一张全军覆没。"""
    from app.config import get_settings

    small = _png(size=(1, 1))
    big = _png(size=(96, 96))
    assert len(big) > len(small)
    # 卡在 small 的大小上：small 恰好通过，big 超限
    monkeypatch.setattr(get_settings(), "max_upload_bytes", len(small), raising=False)

    r = await client.post(
        "/api/garments/batch/upload",
        files=[
            ("files", ("ok.png", small, "image/png")),
            ("files", ("big.png", big, "image/png")),
        ],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["created"]) == 1  # 只有 small 建成了
    assert body["created"][0]["cover_url"] is not None
    assert len(body["skipped"]) == 1
    assert body["skipped"][0]["file"] == "big.png"
    assert "上限" in body["skipped"][0]["reason"]


async def test_batch_upload_rejects_no_files(client: AsyncClient, auth_headers):
    r = await client.post("/api/garments/batch/upload", files=[], headers=auth_headers)
    assert r.status_code in (400, 422), r.text
