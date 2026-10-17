"""搭配：CRUD、画布图层、成图、跨用户隔离（批次 4）。"""

import io

from httpx import AsyncClient
from PIL import Image
from sqlalchemy import select

from app.api.routes.outfits import MAX_WORN_IMAGES
from app.models.outfit import Outfit, OutfitImage, OutfitItem
from app.worker.providers import CandidateTag, TagSuggestion


def png(color: tuple[int, int, int] = (200, 60, 60), size: tuple[int, int] = (400, 500)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def media_path(url: str) -> str:
    """签名 URL 去掉 query 再比。

    `sign_url` 的 `e` 是**秒级**过期时间戳，两次调用跨过整秒就会不一样 ——
    直接比整串会变成一个随机器负载变化的偶发失败（2026-10-17 在慢机器上撞到过）。
    这里要验的是「封面指向同一个文件」，签名不在比较范围里。
    """
    return (url or "").split("?", 1)[0]


async def _garment_with_cutout(client: AsyncClient, headers, name: str = "酒红针织衫") -> dict:
    """造一件带原图 + 抠图的衣物，返回它的详情（图层素材用得上）。"""
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


def _usable_image(garment: dict) -> dict:
    """素材图：抠图优先，没有抠图就退回原图（测试环境没装 rembg，只有原图）。"""
    cutout = next((i for i in garment["images"] if i["kind"] == "cutout"), None)
    return cutout or garment["images"][0]


async def _outfit_tag(client: AsyncClient, headers, category: str, value: str) -> int:
    """取（或建）搭配侧标签取值 id。季节默认预置了春夏秋冬。"""
    cats = (await client.get("/api/tags/categories?scope=outfit", headers=headers)).json()
    target = next((c for c in cats if c["name"] == category), None)
    assert target is not None, f"搭配侧没有类别「{category}」"
    for v in target["values"]:
        if v["value"] == value:
            return v["id"]
    r = await client.post(
        f"/api/tags/categories/{target['id']}/values", json={"value": value}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _layer(garment: dict, **over) -> dict:
    """一层：默认摆在画布正中、宽度占画布一半。"""
    img = _usable_image(garment)
    base = {
        "garment_id": garment["id"],
        "garment_image_id": img["id"],
        "x": 0.5,
        "y": 0.5,
        "w_ratio": 0.5,
        "rotation": 0,
        "flip_h": False,
        "opacity": 1,
    }
    base.update(over)
    return base


async def _create_outfit(
    client: AsyncClient, headers, name: str = "秋日通勤", items: list[dict] | None = None, **extra
) -> dict:
    body = {"name": name, "items": items or []}
    body.update(extra)
    r = await client.post("/api/outfits", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 基础 CRUD ----------


async def test_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/outfits")).status_code == 401


async def test_create_and_list(client: AsyncClient, auth_headers) -> None:
    created = await _create_outfit(client, auth_headers, "周末休闲")
    assert created["name"] == "周末休闲"
    assert created["items"] == []
    assert created["cover_url"] is None

    listed = (await client.get("/api/outfits", headers=auth_headers)).json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == created["id"]


async def test_rejects_blank_name(client: AsyncClient, auth_headers) -> None:
    r = await client.post("/api/outfits", json={"name": "  "}, headers=auth_headers)
    assert r.status_code == 422


async def test_search_by_name(client: AsyncClient, auth_headers) -> None:
    await _create_outfit(client, auth_headers, "冬日通勤")
    await _create_outfit(client, auth_headers, "海边度假")
    r = await client.get("/api/outfits?q=通勤", headers=auth_headers)
    assert [o["name"] for o in r.json()["items"]] == ["冬日通勤"]


async def test_update_name_and_notes(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.patch(
        f"/api/outfits/{outfit['id']}",
        json={"name": "改过的名字", "notes": "配乐福鞋"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "改过的名字"
    assert r.json()["notes"] == "配乐福鞋"


async def test_delete_outfit(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.delete(f"/api/outfits/{outfit['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert (await client.get("/api/outfits", headers=auth_headers)).json()["total"] == 0


# ---------- 图层 ----------


async def test_items_are_saved_in_submitted_order(client: AsyncClient, auth_headers) -> None:
    """数组顺序即层序：下标就是 z，回显按 z 升序。"""
    g1 = await _garment_with_cutout(client, auth_headers, "上衣A")
    g2 = await _garment_with_cutout(client, auth_headers, "裤子B")

    outfit = await _create_outfit(
        client, auth_headers, items=[_layer(g1), _layer(g2, w_ratio=0.3)]
    )
    items = outfit["items"]
    assert len(items) == 2
    assert [i["z"] for i in items] == [0, 1]
    assert items[0]["garment_name"] == "上衣A"
    assert items[1]["garment_name"] == "裤子B"
    assert items[1]["w_ratio"] == 0.3
    # 素材图可访问（签名 URL）+ 原始尺寸带回来给前端算比例
    assert items[0]["image_url"] and items[0]["image_url"].startswith("/api/media/")
    assert items[0]["width"] == 400 and items[0]["height"] == 500
    assert items[0]["missing"] is False


async def test_items_replace_whole_set(client: AsyncClient, auth_headers) -> None:
    g1 = await _garment_with_cutout(client, auth_headers, "上衣A")
    g2 = await _garment_with_cutout(client, auth_headers, "裤子B")
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g1), _layer(g2)])

    # 只留第二层并挪位置 —— 整份替换，不是增量合并
    kept = _layer(g2, x=0.3, y=0.7, rotation=15, flip_h=True, opacity=0.6)
    r = await client.patch(
        f"/api/outfits/{outfit['id']}", json={"items": [kept]}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert len(items) == 1
    assert (items[0]["x"], items[0]["y"]) == (0.3, 0.7)
    assert items[0]["rotation"] == 15
    assert items[0]["flip_h"] is True
    assert items[0]["opacity"] == 0.6


async def test_items_untouched_when_not_sent(client: AsyncClient, auth_headers) -> None:
    """不传 items 就是「本次不动画布」。"""
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])
    r = await client.patch(f"/api/outfits/{outfit['id']}", json={"name": "改名"}, headers=auth_headers)
    assert len(r.json()["items"]) == 1


async def test_same_garment_can_be_used_twice(client: AsyncClient, auth_headers) -> None:
    """左右两只鞋各摆一层 —— 同一件衣物允许重复引用。"""
    g = await _garment_with_cutout(client, auth_headers, "乐福鞋")
    outfit = await _create_outfit(
        client, auth_headers, items=[_layer(g, x=0.35), _layer(g, x=0.65, flip_h=True)]
    )
    assert len(outfit["items"]) == 2
    assert [i["x"] for i in outfit["items"]] == [0.35, 0.65]


async def test_rejects_foreign_garment_layer(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    theirs = await _garment_with_cutout(client, second_headers, "别人的衣服")
    r = await client.post(
        "/api/outfits", json={"name": "偷穿", "items": [_layer(theirs)]}, headers=auth_headers
    )
    assert r.status_code == 400
    assert "无效的衣物" in r.json()["detail"]


async def test_rejects_out_of_range_transform(client: AsyncClient, auth_headers) -> None:
    g = await _garment_with_cutout(client, auth_headers)
    r = await client.post(
        "/api/outfits", json={"name": "超大", "items": [_layer(g, w_ratio=99)]}, headers=auth_headers
    )
    assert r.status_code == 422


async def test_layer_survives_garment_deletion(client: AsyncClient, auth_headers) -> None:
    """删掉衣物，搭配不能跟着没 —— 那一层退化成「素材已删除」。"""
    g = await _garment_with_cutout(client, auth_headers, "旧针织衫")
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])

    deleted = await client.delete(f"/api/garments/{g['id']}", headers=auth_headers)
    assert deleted.status_code == 200

    detail = (await client.get(f"/api/outfits/{outfit['id']}", headers=auth_headers)).json()
    assert len(detail["items"]) == 1
    item = detail["items"][0]
    assert item["missing"] is True
    assert item["image_url"] is None
    assert item["name_snapshot"] == "旧针织衫"  # 名字快照还在，看得出缺了什么


# ---------- 搭配侧标签 ----------


async def test_outfit_tags_use_outfit_scope(client: AsyncClient, auth_headers) -> None:
    season = await _outfit_tag(client, auth_headers, "季节", "春")
    outfit = await _create_outfit(client, auth_headers, tag_value_ids=[season])
    assert outfit["tag_value_ids"] == [season]
    assert [(t["category_name"], t["value"]) for t in outfit["tags"]] == [("季节", "春")]


async def test_rejects_garment_scope_tag(client: AsyncClient, auth_headers) -> None:
    """两套标签体系互不可见：把衣物侧的「黑」挂到搭配上必须被拒。"""
    cats = (await client.get("/api/tags/categories?scope=garment", headers=auth_headers)).json()
    color = next(c for c in cats if c["name"] == "颜色")
    black = next(v for v in color["values"] if v["value"] == "黑")

    r = await client.post(
        "/api/outfits", json={"name": "串号", "tag_value_ids": [black["id"]]}, headers=auth_headers
    )
    assert r.status_code == 400
    assert "无效的搭配标签" in r.json()["detail"]


async def test_filter_by_tag(client: AsyncClient, auth_headers) -> None:
    spring = await _outfit_tag(client, auth_headers, "季节", "春")
    summer = await _outfit_tag(client, auth_headers, "季节", "夏")
    await _create_outfit(client, auth_headers, "春日", tag_value_ids=[spring])
    await _create_outfit(client, auth_headers, "盛夏", tag_value_ids=[summer])

    r = await client.get(f"/api/outfits?tag_value_ids={spring}", headers=auth_headers)
    assert [o["name"] for o in r.json()["items"]] == ["春日"]


# ---------- 搭配图 / 上身图 ----------


async def test_result_image_replaces_previous(client: AsyncClient, auth_headers) -> None:
    """保存一次合成一张：result 图只留最新的一张，别越攒越多。"""
    outfit = await _create_outfit(client, auth_headers)

    for color in ((200, 60, 60), (60, 120, 200)):
        r = await client.post(
            f"/api/outfits/{outfit['id']}/images",
            files=[("file", ("outfit.png", png(color), "image/png"))],
            data={"kind": "result"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

    images = r.json()["images"]
    assert len(images) == 1
    assert images[0]["kind"] == "result"
    assert r.json()["cover_url"] is not None


async def test_worn_image_is_kept_alongside_result(client: AsyncClient, auth_headers) -> None:
    """搭配图与上身图各管各的：保存合成出来的搭配图不会被上身图顶掉。"""
    outfit = await _create_outfit(client, auth_headers)
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("b.png", png(), "image/png"))],
        data={"kind": "worn"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["images"]] == ["result", "worn"]


async def test_legacy_upload_kind_becomes_worn(client: AsyncClient, auth_headers) -> None:
    """早期的 upload 别名与 worn 同义，写库时归一 —— 老客户端不会写出第三种类别。"""
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "upload"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["images"]] == ["worn"]


async def test_worn_images_are_optional_and_multiple(client: AsyncClient, auth_headers) -> None:
    """上身图可以不传、可以多张，删光了这套搭配照样在。"""
    outfit = await _create_outfit(client, auth_headers)
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )

    # 完全不传上身图：只有一张搭配图，封面有图
    detail = (await client.get(f"/api/outfits/{outfit['id']}", headers=auth_headers)).json()
    assert [i["kind"] for i in detail["images"]] == ["result"]
    assert detail["cover_url"] is not None

    for _ in range(3):
        r = await client.post(
            f"/api/outfits/{outfit['id']}/images",
            files=[("file", ("w.png", png(), "image/png"))],
            data={"kind": "worn"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["images"]] == ["result", "worn", "worn", "worn"]

    # 删光上身图：搭配图和封面都还在
    for image in [i for i in r.json()["images"] if i["kind"] == "worn"]:
        r = await client.delete(
            f"/api/outfits/{outfit['id']}/images/{image['id']}", headers=auth_headers
        )
        assert r.status_code == 200, r.text
    assert [i["kind"] for i in r.json()["images"]] == ["result"]
    assert r.json()["cover_url"] is not None


async def test_worn_image_cap(client: AsyncClient, auth_headers) -> None:
    """上限挡住无限上传；被拒的那张不该在磁盘上留下孤儿文件。"""
    outfit = await _create_outfit(client, auth_headers)
    for _ in range(MAX_WORN_IMAGES):
        r = await client.post(
            f"/api/outfits/{outfit['id']}/images",
            files=[("file", ("w.png", png(), "image/png"))],
            data={"kind": "worn"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("w.png", png(), "image/png"))],
        data={"kind": "worn"},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "最多" in r.json()["detail"]


async def test_set_primary_image(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    for kind in ("result", "worn"):
        r = await client.post(
            f"/api/outfits/{outfit['id']}/images",
            files=[("file", ("a.png", png(), "image/png"))],
            data={"kind": kind},
            headers=auth_headers,
        )
    uploaded = next(i for i in r.json()["images"] if i["kind"] == "worn")

    r = await client.post(
        f"/api/outfits/{outfit['id']}/images/{uploaded['id']}/primary", headers=auth_headers
    )
    assert r.status_code == 200, r.text
    assert media_path(r.json()["cover_url"]) == media_path(uploaded["thumb_medium_url"])


async def test_delete_image(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    image_id = r.json()["images"][0]["id"]
    r = await client.delete(f"/api/outfits/{outfit['id']}/images/{image_id}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["images"] == []


async def test_rejects_unknown_kind(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "whatever"},
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_rejects_non_image(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.txt", b"not an image", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 400


# ---------- 隔离与引用 ----------


async def test_other_users_outfit_is_invisible(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    mine = await _create_outfit(client, auth_headers, "我的搭配")
    assert (await client.get("/api/outfits", headers=second_headers)).json()["total"] == 0

    for method, call in (
        ("GET", client.get(f"/api/outfits/{mine['id']}", headers=second_headers)),
        ("PATCH", client.patch(f"/api/outfits/{mine['id']}", json={"name": "x"},
                               headers=second_headers)),
        ("DELETE", client.delete(f"/api/outfits/{mine['id']}", headers=second_headers)),
    ):
        assert (await call).status_code == 404, method


async def test_referencing_lists_outfits_using_garment(client: AsyncClient, auth_headers) -> None:
    g = await _garment_with_cutout(client, auth_headers, "百搭白T")
    first = await _create_outfit(client, auth_headers, "搭配一", items=[_layer(g)])
    second = await _create_outfit(client, auth_headers, "搭配二", items=[_layer(g), _layer(g)])
    await _create_outfit(client, auth_headers, "没用到它")

    r = await client.get(f"/api/outfits/referencing?garment_id={g['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    refs = {ref["name"]: ref["layers"] for ref in r.json()}
    assert refs == {"搭配一": 1, "搭配二": 2}
    assert {first["id"], second["id"]} == {ref["id"] for ref in r.json()}


async def test_deleting_outfit_cascades_items_and_images(
    client: AsyncClient, auth_headers, session_factory
) -> None:
    g = await _garment_with_cutout(client, auth_headers, "要被摆进搭配的衣服")
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    await client.delete(f"/api/outfits/{outfit['id']}", headers=auth_headers)

    async with session_factory() as s:
        assert (await s.execute(select(OutfitItem))).scalars().all() == []
        assert (await s.execute(select(OutfitImage))).scalars().all() == []
        assert (await s.execute(select(Outfit))).scalars().all() == []
        # 衣物本身不受影响
        assert (await client.get(f"/api/garments/{g['id']}", headers=auth_headers)).status_code == 200


# ---------- AI 推荐命名 + 标签 ----------


class FakeTagger:
    """假打标器：记住自己看到了什么，按脚本返回结果。"""

    name = "fake"
    available = True

    def __init__(
        self,
        name: str = "米白针织通勤两件套",
        value_ids: list[int] | None = None,
        *,
        delay: float = 0.0,
        error: str | None = None,
    ) -> None:
        self._name = name
        self._value_ids = value_ids
        self._delay = delay
        self._error = error
        self.seen_size: tuple[int, int] | None = None
        self.kind: str | None = None
        self.candidates: list[CandidateTag] = []
        self.existing_names: list[str] | None = None

    async def suggest(
        self,
        image: Image.Image,
        candidates: list[CandidateTag],
        existing_names: list[str] | None = None,
        kind: str = "garment",
    ) -> TagSuggestion:
        import asyncio as _asyncio

        if self._delay:
            await _asyncio.sleep(self._delay)
        self.seen_size = (image.width, image.height)
        self.kind = kind
        self.candidates = candidates
        self.existing_names = existing_names
        if self._error:
            return TagSuggestion(error=self._error)
        picked = self._value_ids
        if picked is None:
            picked = [c.value_id for c in candidates[:2]]
        return TagSuggestion(name=self._name, tag_value_ids=picked, confidence=0.77)


def _patch_tagger(monkeypatch, tagger: FakeTagger) -> FakeTagger:
    """把「按用户取 tagger」换成假的。

    批次 6 起模型配置存在各自的用户行上，所以工厂收一个 user 参数 ——
    patch 的签名要跟着对上，否则测试会因为 TypeError 而不是业务逻辑挂掉。
    """
    monkeypatch.setattr("app.api.routes.outfits.tagger_for_user", lambda _user: tagger)
    return tagger


async def test_ai_status_reports_whether_tagging_is_configured(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    r = await client.get("/api/outfits/ai/status", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    # 测试环境没配模型：provider=none，available=false
    assert body["provider"] == "none"
    assert body["available"] is False

    _patch_tagger(monkeypatch, FakeTagger())
    assert (await client.get("/api/outfits/ai/status", headers=auth_headers)).json()["available"] is True


async def test_ai_suggest_without_tagger_explains_itself(client: AsyncClient, auth_headers) -> None:
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] and "打标模型" in body["error"]
    assert body["name"] is None


async def test_ai_suggest_needs_a_picture(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    _patch_tagger(monkeypatch, FakeTagger())
    outfit = await _create_outfit(client, auth_headers)
    r = await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)
    assert "成图" in r.json()["error"]


async def test_ai_suggest_returns_name_and_outfit_tags(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    g = await _garment_with_cutout(client, auth_headers)
    summer = await _outfit_tag(client, auth_headers, "季节", "夏")
    outfit = await _create_outfit(client, auth_headers, "待命名", items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )

    tagger = _patch_tagger(monkeypatch, FakeTagger(value_ids=[summer, 987654]))

    r = await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] is None
    assert body["name"] == "米白针织通勤两件套"
    assert body["confidence"] == 0.77
    # 候选池之外的 id 一律丢掉，别把不存在的标签写进建议里
    assert body["tag_value_ids"] == [summer]
    assert body["tags"][0]["category_name"] == "季节"

    # 模型按「一套搭配」的口径看图，且看到了已有名字用于避重
    assert tagger.kind == "outfit"
    assert tagger.existing_names is not None
    # 候选只来自搭配侧：衣物侧那套分类树不能混进来
    assert {c.category_name for c in tagger.candidates} <= {"季节", "风格", "色系"}


async def test_ai_suggest_prefers_the_canvas_sent_by_the_browser(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    """用户在编辑器里挪了图层还没保存，库里的成图是旧的 —— 传上来的才算数。"""
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("old.png", png(size=(400, 500)), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    tagger = _patch_tagger(monkeypatch, FakeTagger())

    r = await client.post(
        f"/api/outfits/{outfit['id']}/ai-suggest",
        files=[("file", ("canvas.png", png(size=(300, 400)), "image/png"))],
        headers=auth_headers,
    )
    assert r.json()["error"] is None
    assert tagger.seen_size == (300, 400)


async def test_ai_suggest_dedupes_against_existing_outfit_names(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    await _create_outfit(client, auth_headers, "黑白极简通勤")
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, "随便", items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    _patch_tagger(monkeypatch, FakeTagger(name="黑白极简通勤"))

    r = await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)
    assert r.json()["name"] == "黑白极简通勤 (2)"


async def test_ai_suggest_reports_timeouts_and_failures(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )

    _patch_tagger(monkeypatch, FakeTagger(delay=0.3))
    monkeypatch.setattr("app.api.routes.outfits.SUGGEST_TIMEOUT", 0.05)
    body = (await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)).json()
    assert "没回话" in (body["error"] or "")

    _patch_tagger(monkeypatch, FakeTagger(error="VLM 调用失败：boom"))
    body = (await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)).json()
    assert "boom" in (body["error"] or "")


async def test_ai_suggest_only_suggests(client: AsyncClient, auth_headers, monkeypatch) -> None:
    """推荐不落库：名字和标签要等用户在界面上点了保存才生效。"""
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, "原名", items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )
    _patch_tagger(monkeypatch, FakeTagger(name="AI 起的新名字"))
    await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)

    after = (await client.get(f"/api/outfits/{outfit['id']}", headers=auth_headers)).json()
    assert after["name"] == "原名"
    assert after["tag_value_ids"] == []


async def test_ai_suggest_hints_when_outfit_has_no_tags(
    client: AsyncClient, auth_headers, monkeypatch
) -> None:
    """搭配侧一个取值都没有时，至少名字还能给，并提示用户去补标签。"""
    g = await _garment_with_cutout(client, auth_headers)
    outfit = await _create_outfit(client, auth_headers, items=[_layer(g)])
    await client.post(
        f"/api/outfits/{outfit['id']}/images",
        files=[("file", ("a.png", png(), "image/png"))],
        data={"kind": "result"},
        headers=auth_headers,
    )

    tagger = _patch_tagger(monkeypatch, FakeTagger())
    async def _no_candidates(db, owner_id):  # noqa: ANN001, ARG001 - 替身只要签名对得上
        return []

    monkeypatch.setattr("app.api.routes.outfits._outfit_candidate_tags", _no_candidates)
    body = (await client.post(f"/api/outfits/{outfit['id']}/ai-suggest", headers=auth_headers)).json()
    assert body["name"] == "米白针织通勤两件套"
    assert body["tag_value_ids"] == []
    assert body["hint"] and "还没有标签" in body["hint"]
    assert tagger.candidates == []
