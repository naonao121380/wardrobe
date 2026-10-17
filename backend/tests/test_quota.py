"""按用户的配额：衣物 / 搭配 / 标签（三档），批量按整批算，管理员豁免。

两条容易踩的规则钉在这里：

- **预置标签不占名额**。建号时播种 ~130 个标签取值（``TagValue.is_seed``），
  要是把它们算进「可添加标签数量」，任何小于 130 的配额一上来就爆 —— 配额这
  功能就等于没做（冒烟时真撞上过：``tags: {"used": 131, "limit": 3}``）。
- **批量要么全进要么全不进**。上传 30 张而只剩 5 个名额，不能「先存 5 件再报错」。
"""

from __future__ import annotations

import io

from httpx import AsyncClient
from PIL import Image


def _png(color: tuple[int, int, int] = (90, 120, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(buf, format="PNG")
    return buf.getvalue()


async def _quota(client: AsyncClient, headers) -> dict:
    r = await client.get("/api/auth/quota", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _add_garment(client: AsyncClient, headers, name: str):
    return await client.post("/api/garments", json={"name": name}, headers=headers)


async def _first_category_id(client: AsyncClient, headers) -> int:
    cats = (await client.get("/api/tags/categories", headers=headers)).json()
    return cats[0]["id"]


# ---------- 衣物 ----------


async def test_garment_quota_blocks_the_third(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user(garment_quota=2)
    assert (await _add_garment(client, headers, "一")).status_code == 201
    assert (await _add_garment(client, headers, "二")).status_code == 201

    third = await _add_garment(client, headers, "三")
    assert third.status_code == 403, third.text
    # 文案要说清「上限多少、现在多少」——不然用户只会看到一句「被拒绝」
    detail = third.json()["detail"]
    assert "上限" in detail and "2" in detail, detail

    assert (await _quota(client, headers))["garments"] == {"used": 2, "limit": 2}


async def test_garment_quota_none_means_unlimited(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user()
    for i in range(5):
        assert (await _add_garment(client, headers, f"衣{i}")).status_code == 201
    assert (await _quota(client, headers))["garments"] == {"used": 5, "limit": None}


async def test_lowering_quota_keeps_existing_rows(client: AsyncClient, make_user, admin_headers) -> None:
    """上限调小**不会**删掉用户已有的东西，只是拦住新的。

    用户的数据不该因为管理员改了个数字就消失 —— 真删了是不可逆的。
    """
    headers, uid = await make_user(username="quota_user")
    for i in range(3):
        assert (await _add_garment(client, headers, f"旧衣{i}")).status_code == 201

    patched = await client.patch(
        f"/api/admin/users/{uid}", json={"garment_quota": 1}, headers=admin_headers
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["quotas"]["garments"] == {"used": 3, "limit": 1}

    assert (await client.get("/api/garments", headers=headers)).json()["total"] == 3
    assert (await _add_garment(client, headers, "新的")).status_code == 403


async def test_batch_upload_counts_the_whole_batch(client: AsyncClient, make_user) -> None:
    """一批 4 张而只剩 3 个名额 → 整批拒绝，一件都不落库。"""
    headers, _ = await make_user(garment_quota=5)
    for i in range(2):
        assert (await _add_garment(client, headers, f"已有{i}")).status_code == 201

    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", (f"{i}.png", _png(), "image/png")) for i in range(4)],
        headers=headers,
    )
    assert r.status_code == 403, r.text
    assert "4" in r.json()["detail"]  # 「这次要新增 4 个」

    assert (await client.get("/api/garments", headers=headers)).json()["total"] == 2


async def test_batch_upload_passes_when_it_fits(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user(garment_quota=5)
    r = await client.post(
        "/api/garments/batch/upload",
        files=[("files", (f"{i}.png", _png(), "image/png")) for i in range(5)],
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["created"]) == 5
    assert (await _quota(client, headers))["garments"]["used"] == 5


# ---------- 搭配 ----------


async def test_outfit_quota(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user(outfit_quota=1)
    assert (
        await client.post("/api/outfits", json={"name": "搭配一"}, headers=headers)
    ).status_code == 201
    second = await client.post("/api/outfits", json={"name": "搭配二"}, headers=headers)
    assert second.status_code == 403, second.text
    assert "搭配" in second.json()["detail"]


# ---------- 标签 ----------


async def test_tag_quota_ignores_seeded_values(client: AsyncClient, make_user) -> None:
    """建号播种的一百多个取值**不算**用量，配额从 0 起算。

    回归用例：曾经 ``/auth/quota`` 报 ``tags: {"used": 131, "limit": 3}``，
    配额形同虚设。
    """
    headers, _ = await make_user(tag_quota=3)
    assert (await _quota(client, headers))["tags"] == {"used": 0, "limit": 3}

    cat = await _first_category_id(client, headers)
    for i in range(3):
        r = await client.post(
            f"/api/tags/categories/{cat}/values", json={"value": f"自建{i}"}, headers=headers
        )
        assert r.status_code == 201, r.text

    fourth = await client.post(
        f"/api/tags/categories/{cat}/values", json={"value": "自建4"}, headers=headers
    )
    assert fourth.status_code == 403, fourth.text
    assert (await _quota(client, headers))["tags"] == {"used": 3, "limit": 3}


async def test_tag_quota_counts_values_not_categories(client: AsyncClient, make_user) -> None:
    """口径是**取值**而不是类别：建类别不占名额，建取值才占。"""
    headers, _ = await make_user(tag_quota=1)
    made = await client.post(
        "/api/tags/categories", json={"scope": "garment", "name": "自定义轴"}, headers=headers
    )
    assert made.status_code == 201, made.text
    cid = made.json()["id"]

    assert (
        await client.post(f"/api/tags/categories/{cid}/values", json={"value": "A"}, headers=headers)
    ).status_code == 201
    assert (
        await client.post(f"/api/tags/categories/{cid}/values", json={"value": "B"}, headers=headers)
    ).status_code == 403

    # 类别本身不进计数
    assert (await _quota(client, headers))["tags"] == {"used": 1, "limit": 1}


# ---------- 管理员豁免 ----------


async def test_admin_is_exempt_from_quota(client: AsyncClient, admin, admin_headers, session_factory) -> None:
    """管理员不受配额限制。

    否则管理员把标签配额调成 0 就把自己锁在门外，谁也改不回来了。
    """
    from sqlalchemy import update

    from app.models.user import User

    async with session_factory() as s:
        await s.execute(update(User).where(User.id == admin.id).values(garment_quota=0))
        await s.commit()

    r = await _add_garment(client, admin_headers, "管理员照样建")
    assert r.status_code == 201, r.text


async def test_admin_quota_editing_round_trip(client: AsyncClient, make_user, admin_headers) -> None:
    """改配额 / 显式清回「不限」（``clear_*_quota`` 三态开关）。"""
    _, uid = await make_user(username="edit_quota")

    set_to = await client.patch(
        f"/api/admin/users/{uid}",
        json={"garment_quota": 7, "outfit_quota": 0, "tag_quota": 3},
        headers=admin_headers,
    )
    assert set_to.status_code == 200, set_to.text
    assert set_to.json()["quotas"]["garments"]["limit"] == 7
    assert set_to.json()["quotas"]["outfits"]["limit"] == 0

    cleared = await client.patch(
        f"/api/admin/users/{uid}",
        json={"clear_garment_quota": True, "clear_tag_quota": True},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()["quotas"]
    assert body["garments"]["limit"] is None
    assert body["tags"]["limit"] is None
    assert body["outfits"]["limit"] == 0  # 没点到的不动
