"""批次 5 收尾：删除级联提示、资料更新、运行信息（/meta）。

删衣物牵扯搭配这件事有两面：
- 默认**保留搭配**，只把引用的那几层变成「素材已删除」（保守，删衣橱不该顺手删搭配）；
- 显式 ``cascade_outfits=true`` 才连搭配一起删。
两条路都要能把「影响了什么」如实报回来 —— 前端删完要告诉用户。
"""

import io

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.models.outfit import Outfit, OutfitItem


def png(color: tuple[int, int, int] = (200, 60, 60), size: tuple[int, int] = (400, 500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


async def _garment(client: AsyncClient, headers, name: str) -> dict:
    r = await client.post("/api/garments", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    gid = r.json()["id"]
    up = await client.post(
        f"/api/garments/{gid}/images",
        files=[("files", ("a.png", png(), "image/png"))],
        headers=headers,
    )
    assert up.status_code == 201, up.text
    return up.json()


def _layer(garment: dict) -> dict:
    img = garment["images"][0]
    return {
        "garment_id": garment["id"],
        "garment_image_id": img["id"],
        "x": 0.5,
        "y": 0.5,
        "w_ratio": 0.5,
        "rotation": 0,
        "flip_h": False,
        "opacity": 1,
    }


async def _outfit(client: AsyncClient, headers, name: str, items: list[dict]) -> dict:
    r = await client.post(
        "/api/outfits", json={"name": name, "items": items}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 删单件：默认保留搭配 ----------


async def test_delete_garment_reports_affected_outfits_and_keeps_them(
    client: AsyncClient, auth_headers, session_factory
) -> None:
    g = await _garment(client, auth_headers, "百搭白T")
    await _outfit(client, auth_headers, "搭配一", [_layer(g)])
    await _outfit(client, auth_headers, "搭配二", [_layer(g), _layer(g)])
    await _outfit(client, auth_headers, "没用到它", [])

    r = await client.delete(f"/api/garments/{g['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_images"] == 1
    assert {o["name"]: o["layers"] for o in body["affected_outfits"]} == {
        "搭配一": 1,
        "搭配二": 2,
    }
    assert body["orphaned_layers"] == 3  # 1 + 2
    assert body["deleted_outfits"] == 0

    # 搭配还在，只是引用的层被外键置空了
    async with session_factory() as s:
        outfits = (await s.execute(select(Outfit))).scalars().all()
        assert {o.name for o in outfits} == {"搭配一", "搭配二", "没用到它"}
        items = (await s.execute(select(OutfitItem))).scalars().all()
        assert len(items) == 3
        assert all(i.garment_id is None for i in items)
        # 名字快照留着，界面上还能看出这层原来是什么
        assert all(i.name_snapshot == "百搭白T" for i in items)


async def test_delete_garment_cascade_removes_outfits(
    client: AsyncClient, auth_headers, session_factory
) -> None:
    g = await _garment(client, auth_headers, "要被带走的衣服")
    await _outfit(client, auth_headers, "陪葬一", [_layer(g)])
    await _outfit(client, auth_headers, "无辜的搭配", [])

    r = await client.delete(
        f"/api/garments/{g['id']}?cascade_outfits=true", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted_outfits"] == 1
    assert body["orphaned_layers"] == 0  # 搭配整个没了，就没有「空位」一说
    assert [o["name"] for o in body["affected_outfits"]] == ["陪葬一"]

    async with session_factory() as s:
        assert [o.name for o in (await s.execute(select(Outfit))).scalars().all()] == ["无辜的搭配"]
        assert (await s.execute(select(OutfitItem))).scalars().all() == []


async def test_delete_garment_without_outfits_is_quiet(client: AsyncClient, auth_headers) -> None:
    g = await _garment(client, auth_headers, "孤家寡人")
    body = (await client.delete(f"/api/garments/{g['id']}", headers=auth_headers)).json()
    assert body["affected_outfits"] == []
    assert body["orphaned_layers"] == 0
    assert body["deleted_outfits"] == 0


# ---------- 批量删除 ----------


async def test_batch_delete_reports_affected_outfits(
    client: AsyncClient, auth_headers, session_factory
) -> None:
    a = await _garment(client, auth_headers, "甲")
    b = await _garment(client, auth_headers, "乙")
    c = await _garment(client, auth_headers, "丙")
    await _outfit(client, auth_headers, "甲乙同款", [_layer(a), _layer(b)])
    await _outfit(client, auth_headers, "只有甲", [_layer(a)])

    r = await client.post(
        "/api/garments/batch/delete",
        json={"garment_ids": [a["id"], b["id"], c["id"], 99999]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["deleted"]) == sorted([a["id"], b["id"], c["id"]])
    assert body["skipped"][0]["id"] == 99999
    assert {o["name"]: o["layers"] for o in body["affected_outfits"]} == {
        "甲乙同款": 2,
        "只有甲": 1,
    }
    assert body["orphaned_layers"] == 3
    assert body["deleted_outfits"] == 0

    async with session_factory() as s:
        assert len((await s.execute(select(Outfit))).scalars().all()) == 2


async def test_batch_delete_cascade_removes_outfits(
    client: AsyncClient, auth_headers, session_factory
) -> None:
    a = await _garment(client, auth_headers, "甲")
    b = await _garment(client, auth_headers, "乙")
    await _outfit(client, auth_headers, "用到甲", [_layer(a)])
    await _outfit(client, auth_headers, "用到乙", [_layer(b)])
    await _outfit(client, auth_headers, "谁都没用", [])

    body = (
        await client.post(
            "/api/garments/batch/delete",
            json={"garment_ids": [a["id"], b["id"]], "cascade_outfits": True},
            headers=auth_headers,
        )
    ).json()
    assert body["deleted_outfits"] == 2
    assert body["orphaned_layers"] == 0

    async with session_factory() as s:
        assert [o.name for o in (await s.execute(select(Outfit))).scalars().all()] == ["谁都没用"]


async def test_delete_does_not_touch_other_users_outfits(
    client: AsyncClient, auth_headers, second_headers, session_factory
) -> None:
    """别人的搭配引用了我的衣物？本来就查不到 —— 隔离在 owner_id 上。"""
    mine = await _garment(client, auth_headers, "我的衣服")
    await _outfit(client, auth_headers, "我的搭配", [_layer(mine)])

    # 第二个用户看不到我的衣物
    assert (await client.get(f"/api/garments/{mine['id']}", headers=second_headers)).status_code == 404
    # 也查不到「谁引用了它」
    r = await client.get(
        f"/api/outfits/referencing?garment_id={mine['id']}", headers=second_headers
    )
    assert r.status_code == 200 and r.json() == []

    # 别人的引用清单里当然也不该混进我的数据
    body = (await client.delete(f"/api/garments/{mine['id']}", headers=auth_headers)).json()
    assert [o["name"] for o in body["affected_outfits"]] == ["我的搭配"]


# ---------- 资料更新 ----------


async def test_patch_me_updates_display_name(client: AsyncClient, auth_headers) -> None:
    r = await client.patch("/api/auth/me", json={"display_name": "衣柜主人"}, headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "衣柜主人"
    assert (await client.get("/api/auth/me", headers=auth_headers)).json()["display_name"] == "衣柜主人"


async def test_patch_me_blank_display_name_falls_back_to_username(
    client: AsyncClient, auth_headers
) -> None:
    await client.patch("/api/auth/me", json={"display_name": "   "}, headers=auth_headers)
    assert (await client.get("/api/auth/me", headers=auth_headers)).json()["display_name"] is None


async def test_patch_me_ignores_other_fields(client: AsyncClient, auth_headers) -> None:
    """用户名 / 管理员标记不给改 —— 传了也当没看见。"""
    r = await client.patch(
        "/api/auth/me",
        json={"display_name": "还是我", "username": "hacker", "is_admin": True},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "naonao"
    assert body["is_admin"] is False


async def test_patch_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.patch("/api/auth/me", json={"display_name": "x"})).status_code == 401


# ---------- /meta ----------


async def test_meta_reports_version_and_ai_state(client: AsyncClient, auth_headers) -> None:
    r = await client.get("/api/meta", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["app_name"] == "wardrobe"
    assert body["version"]  # 具体号随发布变，这里只钉「有」
    assert body["matting_provider"] in {"none", "rembg"}
    assert body["tagger_provider"] in {"none", "clip", "vlm"}
    # 没配模型时必须是「不可用」，别把按钮点亮点出个空结果
    if body["tagger_provider"] == "none":
        assert body["tagger_available"] is False
    # 批次 6 起注册模式从库里读（open / invite / closed），不再回环境变量的布尔
    assert body["registration_mode"] in {"open", "invite", "closed"}
    assert isinstance(body["llm_configured"], bool)


async def test_meta_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/meta")).status_code == 401


async def test_healthz_stays_public_and_carries_version(client: AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# ---------- 删文件绝不把请求带崩 ----------


def test_delete_keys_swallows_systemexit(monkeypatch) -> None:
    """执行环境的安全删除钩子拦下删除时是 `raise SystemExit(1)`。

    `SystemExit` 不属于 `Exception`，只写 `except Exception` 接不住 ——
    一个「缓存文件没删掉」的小事会把已经提交完事务的请求变成 500。
    这里把这个契约钉死。
    """
    from app.core import media

    class Boom:
        def unlink(self, missing_ok: bool = False) -> None:  # noqa: ARG002
            raise SystemExit(1)

    monkeypatch.setattr(media, "resolve_key", lambda key: Boom())
    media.delete_keys(["garments/1/a.png"])  # 不抛异常即通过


def test_delete_keys_swallows_ordinary_errors(monkeypatch) -> None:
    from app.core import media

    calls = []

    class Boom:
        def unlink(self, missing_ok: bool = False) -> None:  # noqa: ARG002
            calls.append(1)
            raise PermissionError("文件被占用")

    monkeypatch.setattr(media, "resolve_key", lambda key: Boom())
    media.delete_keys(["a.png", None, "b.png"])
    assert len(calls) == 2  # None 被跳过，另外两个都试过了
