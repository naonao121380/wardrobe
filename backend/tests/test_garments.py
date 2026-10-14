"""衣物 CRUD、标签绑定、图片上传、签名媒体 URL。"""

import io

import pytest
from httpx import AsyncClient
from PIL import Image

from app.core.media import MediaError, resolve_key


def make_image_bytes(fmt: str = "PNG", size: tuple[int, int] = (800, 600)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (176, 120, 92)).save(buf, format=fmt)
    return buf.getvalue()


async def _create(client: AsyncClient, headers, name: str = "米白针织开衫") -> dict:
    r = await client.post("/api/garments", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _add_tag_value(client: AsyncClient, headers, category: str, value: str) -> int:
    cats = (await client.get("/api/tags/categories", headers=headers)).json()
    target = next(c for c in cats if c["name"] == category)
    r = await client.post(
        f"/api/tags/categories/{target['id']}/values", json={"value": value}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _upload(client: AsyncClient, headers, garment_id: int, data: bytes, filename="a.png"):
    return await client.post(
        f"/api/garments/{garment_id}/images",
        files=[("files", (filename, data, "image/png"))],
        headers=headers,
    )


# ---------- 基础 CRUD ----------


async def test_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/garments")).status_code == 401


async def test_create_and_list(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    assert garment["name"] == "米白针织开衫"
    assert garment["images"] == []
    assert garment["cover_url"] is None

    listed = await client.get("/api/garments", headers=auth_headers)
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == garment["id"]


async def test_create_rejects_blank_name(client: AsyncClient, auth_headers) -> None:
    r = await client.post("/api/garments", json={"name": ""}, headers=auth_headers)
    assert r.status_code == 422


async def test_update_name_and_notes(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await client.patch(
        f"/api/garments/{garment['id']}",
        json={"name": "灰羊毛大衣", "notes": "冬季"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["name"] == "灰羊毛大衣"
    assert r.json()["notes"] == "冬季"


async def test_get_missing_garment_404(client: AsyncClient, auth_headers) -> None:
    assert (await client.get("/api/garments/9999", headers=auth_headers)).status_code == 404


# ---------- 标签绑定与筛选 ----------


async def test_bind_tags_and_see_them_grouped(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    outer = await _add_tag_value(client, auth_headers, "大类", "外套")
    wool = await _add_tag_value(client, auth_headers, "材质", "羊毛")

    r = await client.patch(
        f"/api/garments/{garment['id']}",
        json={"tag_value_ids": [outer, wool]},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    tags = r.json()["tags"]
    assert sorted((t["category_name"], t["value"]) for t in tags) == [("大类", "外套"), ("材质", "羊毛")]
    assert sorted(r.json()["tag_value_ids"]) == sorted([outer, wool])


async def test_rejects_tag_from_other_scope(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    outfit_cats = (await client.get("/api/tags/categories", params={"scope": "outfit"}, headers=auth_headers)).json()
    outfit_value = await client.post(
        f"/api/tags/categories/{outfit_cats[0]['id']}/values",
        json={"value": "春秋"},
        headers=auth_headers,
    )
    r = await client.patch(
        f"/api/garments/{garment['id']}",
        json={"tag_value_ids": [outfit_value.json()["id"]]},
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_filter_by_tag_takes_intersection(client: AsyncClient, auth_headers) -> None:
    jacket = await _add_tag_value(client, auth_headers, "大类", "外套")
    knit = await _add_tag_value(client, auth_headers, "大类", "针织")
    wool = await _add_tag_value(client, auth_headers, "材质", "羊毛")
    cotton = await _add_tag_value(client, auth_headers, "材质", "棉")

    a = await _create(client, auth_headers, "羊毛外套")
    b = await _create(client, auth_headers, "羊毛针织衫")
    c = await _create(client, auth_headers, "棉质外套")
    for garment, values in ((a, [jacket, wool]), (b, [knit, wool]), (c, [jacket, cotton])):
        await client.patch(
            f"/api/garments/{garment['id']}", json={"tag_value_ids": values}, headers=auth_headers
        )

    async def names(*value_ids: int) -> list[str]:
        r = await client.get(
            "/api/garments",
            params=[("tag_value_ids", v) for v in value_ids],
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        return sorted(i["name"] for i in r.json()["items"])

    assert await names(jacket) == ["棉质外套", "羊毛外套"]
    # 注意：names() 用的是 Python 默认排序（按码点），'外'(U+5916) < '针'(U+9488)
    assert await names(wool) == ["羊毛外套", "羊毛针织衫"]
    assert await names(jacket, wool) == ["羊毛外套"]
    assert await names(jacket, knit) == []


async def test_search_by_name(client: AsyncClient, auth_headers) -> None:
    await _create(client, auth_headers, "米白针织开衫")
    await _create(client, auth_headers, "黑色西装裤")

    r = await client.get("/api/garments", params={"q": "针织"}, headers=auth_headers)
    assert [i["name"] for i in r.json()["items"]] == ["米白针织开衫"]


async def test_pagination(client: AsyncClient, auth_headers) -> None:
    for i in range(5):
        await _create(client, auth_headers, f"衣物{i}")

    page = await client.get(
        "/api/garments", params={"page": 2, "page_size": 2}, headers=auth_headers
    )
    body = page.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["page"] == 2


# ---------- 图片 ----------


async def test_upload_creates_thumbnails(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await _upload(client, auth_headers, garment["id"], make_image_bytes())
    assert r.status_code == 201, r.text

    body = r.json()
    assert len(body["images"]) == 1
    image = body["images"][0]
    assert (image["width"], image["height"]) == (800, 600)
    assert body["cover_url"] == image["thumb_medium_url"]

    small = await client.get(image["thumb_small_url"])
    assert small.status_code == 200
    assert small.headers["content-type"] == "image/webp"
    assert Image.open(io.BytesIO(small.content)).size == (240, 180)

    medium = await client.get(image["thumb_medium_url"])
    assert Image.open(io.BytesIO(medium.content)).size == (720, 540)

    original = await client.get(image["original_url"])
    assert original.status_code == 200
    assert Image.open(io.BytesIO(original.content)).size == (800, 600)


async def test_upload_multiple_files(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await client.post(
        f"/api/garments/{garment['id']}/images",
        files=[
            ("files", ("a.png", make_image_bytes(size=(400, 400)), "image/png")),
            ("files", ("b.png", make_image_bytes(size=(600, 300)), "image/png")),
        ],
        headers=auth_headers,
    )
    assert r.status_code == 201, r.text
    assert len(r.json()["images"]) == 2


async def test_rejects_non_image(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await client.post(
        f"/api/garments/{garment['id']}/images",
        files=[("files", ("notes.txt", b"this is not an image", "text/plain"))],
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_rejects_unsupported_image_format(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    buf = io.BytesIO()
    Image.new("RGB", (50, 50)).save(buf, format="BMP")
    r = await client.post(
        f"/api/garments/{garment['id']}/images",
        files=[("files", ("a.bmp", buf.getvalue(), "image/bmp"))],
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_set_primary_image(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await client.post(
        f"/api/garments/{garment['id']}/images",
        files=[
            ("files", ("a.png", make_image_bytes(size=(400, 400)), "image/png")),
            ("files", ("b.png", make_image_bytes(size=(600, 300)), "image/png")),
        ],
        headers=auth_headers,
    )
    first, second = r.json()["images"]

    promoted = await client.post(
        f"/api/garments/{garment['id']}/images/{second['id']}/primary", headers=auth_headers
    )
    assert promoted.status_code == 200
    images = promoted.json()["images"]
    assert images[0]["id"] == second["id"]
    assert promoted.json()["cover_url"] == images[0]["thumb_medium_url"]
    assert first["id"] in [i["id"] for i in images]


async def test_delete_image_removes_files(client: AsyncClient, auth_headers, media_root) -> None:
    garment = await _create(client, auth_headers)
    r = await _upload(client, auth_headers, garment["id"], make_image_bytes())
    image = r.json()["images"][0]

    keys = [
        line.split("?", 1)[0].removeprefix("/api/media/")
        for line in (image["original_url"], image["thumb_small_url"], image["thumb_medium_url"])
    ]
    for key in keys:
        assert resolve_key(key).is_file()

    deleted = await client.delete(
        f"/api/garments/{garment['id']}/images/{image['id']}", headers=auth_headers
    )
    assert deleted.status_code == 200
    assert deleted.json()["images"] == []
    for key in keys:
        assert not resolve_key(key).is_file()


async def test_delete_garment_removes_files(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await _upload(client, auth_headers, garment["id"], make_image_bytes())
    key = r.json()["images"][0]["original_url"].split("?", 1)[0].removeprefix("/api/media/")
    assert resolve_key(key).is_file()

    assert (await client.delete(f"/api/garments/{garment['id']}", headers=auth_headers)).status_code == 200
    assert not resolve_key(key).is_file()
    assert (await client.get("/api/garments", headers=auth_headers)).json()["total"] == 0


# ---------- 媒体签名 ----------


async def test_media_url_requires_valid_signature(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    r = await _upload(client, auth_headers, garment["id"], make_image_bytes())
    url = r.json()["images"][0]["thumb_small_url"]

    assert (await client.get(url)).status_code == 200
    assert (await client.get(url.split("?", 1)[0])).status_code == 422  # 缺 e / s 参数
    assert (await client.get(url + "tampered")).status_code == 403

    key, query = url.split("?", 1)
    e_value = dict(pair.split("=") for pair in query.split("&"))["e"]
    assert (await client.get(f"{key}?e={int(e_value) - 10_000_000}&s=whatever")).status_code == 403


async def test_resolve_key_blocks_path_traversal() -> None:
    with pytest.raises(MediaError):
        resolve_key("../../etc/passwd")


# ---------- 跨用户隔离 ----------


async def test_garments_are_isolated_between_users(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    mine = await _create(client, auth_headers, "我的外套")

    assert (await client.get("/api/garments", headers=second_headers)).json()["total"] == 0
    assert (await client.get(f"/api/garments/{mine['id']}", headers=second_headers)).status_code == 404
    assert (
        await client.patch(
            f"/api/garments/{mine['id']}", json={"name": "篡改"}, headers=second_headers
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/garments/{mine['id']}", headers=second_headers)
    ).status_code == 404

    # 也不能往别人的衣物里传图
    r = await _upload(client, second_headers, mine["id"], make_image_bytes())
    assert r.status_code == 404


async def test_tag_value_delete_untags_garments(client: AsyncClient, auth_headers) -> None:
    garment = await _create(client, auth_headers)
    value_id = await _add_tag_value(client, auth_headers, "大类", "外套")
    await client.patch(
        f"/api/garments/{garment['id']}", json={"tag_value_ids": [value_id]}, headers=auth_headers
    )

    r = await client.delete(f"/api/tags/values/{value_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["untagged"] == 1

    detail = await client.get(f"/api/garments/{garment['id']}", headers=auth_headers)
    assert detail.json()["tag_value_ids"] == []
