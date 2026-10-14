"""标签体系：默认类别、增删改、两套体系隔离、跨用户隔离。"""

from httpx import AsyncClient


async def _cats(client: AsyncClient, headers: dict[str, str], scope: str = "garment") -> list[dict]:
    r = await client.get("/api/tags/categories", params={"scope": scope}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_default_garment_categories(client: AsyncClient, auth_headers) -> None:
    assert [c["name"] for c in await _cats(client, auth_headers, "garment")] == [
        "大类",
        "小类",
        "颜色",
        "材质",
    ]


async def test_default_outfit_categories(client: AsyncClient, auth_headers) -> None:
    assert [c["name"] for c in await _cats(client, auth_headers, "outfit")] == [
        "季节",
        "风格",
        "色系",
    ]


async def test_two_scopes_are_fully_independent(client: AsyncClient, auth_headers) -> None:
    garment = await _cats(client, auth_headers, "garment")
    outfit = await _cats(client, auth_headers, "outfit")

    assert {c["id"] for c in garment}.isdisjoint({c["id"] for c in outfit})
    assert {c["name"] for c in garment}.isdisjoint({c["name"] for c in outfit})


async def test_create_custom_category_and_value(client: AsyncClient, auth_headers) -> None:
    created = await client.post(
        "/api/tags/categories", json={"scope": "garment", "name": "场合"}, headers=auth_headers
    )
    assert created.status_code == 201, created.text
    category_id = created.json()["id"]

    value = await client.post(
        f"/api/tags/categories/{category_id}/values",
        json={"value": "通勤"},
        headers=auth_headers,
    )
    assert value.status_code == 201, value.text

    target = next(c for c in await _cats(client, auth_headers) if c["id"] == category_id)
    assert [v["value"] for v in target["values"]] == ["通勤"]


async def test_duplicate_category_conflicts(client: AsyncClient, auth_headers) -> None:
    r = await client.post(
        "/api/tags/categories", json={"scope": "garment", "name": "大类"}, headers=auth_headers
    )
    assert r.status_code == 409


async def test_same_name_allowed_across_scopes(client: AsyncClient, auth_headers) -> None:
    r = await client.post(
        "/api/tags/categories", json={"scope": "outfit", "name": "大类"}, headers=auth_headers
    )
    assert r.status_code == 201


async def test_duplicate_value_conflicts(client: AsyncClient, auth_headers) -> None:
    category = (await _cats(client, auth_headers))[0]
    body = {"value": "外套"}
    first = await client.post(
        f"/api/tags/categories/{category['id']}/values", json=body, headers=auth_headers
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/tags/categories/{category['id']}/values", json=body, headers=auth_headers
    )
    assert second.status_code == 409


async def test_rename_and_delete_value(client: AsyncClient, auth_headers) -> None:
    category = (await _cats(client, auth_headers))[0]
    created = await client.post(
        f"/api/tags/categories/{category['id']}/values",
        json={"value": "外套"},
        headers=auth_headers,
    )
    value_id = created.json()["id"]

    renamed = await client.patch(
        f"/api/tags/values/{value_id}", json={"value": "夹克"}, headers=auth_headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["value"] == "夹克"

    assert (await client.delete(f"/api/tags/values/{value_id}", headers=auth_headers)).status_code == 200
    after = next(c for c in await _cats(client, auth_headers) if c["id"] == category["id"])
    assert after["values"] == []


async def test_deleted_categories_are_not_reseeded(client: AsyncClient, auth_headers) -> None:
    """用户把默认类别全删掉后，不应该在下次访问时被重新塞回来。"""
    for category in await _cats(client, auth_headers, "garment"):
        r = await client.delete(f"/api/tags/categories/{category['id']}", headers=auth_headers)
        assert r.status_code == 200

    assert await _cats(client, auth_headers, "garment") == []
    # 再读一次仍为空
    assert await _cats(client, auth_headers, "garment") == []


async def test_categories_are_isolated_between_users(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    mine = (await _cats(client, auth_headers))[0]
    created = await client.post(
        f"/api/tags/categories/{mine['id']}/values",
        json={"value": "我的专属标签"},
        headers=auth_headers,
    )
    assert created.status_code == 201
    value_id = created.json()["id"]

    # 另一个用户看不到、也改不了
    theirs = await _cats(client, second_headers)
    assert all(c["id"] != mine["id"] for c in theirs)
    assert all(v["value"] != "我的专属标签" for c in theirs for v in c["values"])

    assert (
        await client.patch(
            f"/api/tags/values/{value_id}", json={"value": "篡改"}, headers=second_headers
        )
    ).status_code == 404
    assert (
        await client.delete(f"/api/tags/categories/{mine['id']}", headers=second_headers)
    ).status_code == 404


async def test_tags_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/tags/categories")).status_code == 401
