"""标签配置的导出 / 导入。

钉住三件事：

- 导出文件里**只有名字、没有 id** —— id 换个库就全变，只有名字能跨实例落地；
- 导入是**并集**：同名的类别 / 取值跳过，不覆盖、不改名、不删除本地已有的标签；
- 配额按**整批**算 —— 宁可一条都不进，也不要「进了一半再报错」留下半个类别。
"""

from __future__ import annotations

from httpx import AsyncClient

SCOPE = {"scope": "garment"}


async def _cats(client: AsyncClient, headers, scope: str = "garment") -> list[dict]:
    r = await client.get("/api/tags/categories", params={"scope": scope}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _find(cats: list[dict], name: str) -> dict:
    return next(c for c in cats if c["name"] == name)


async def _add_cat(client: AsyncClient, headers, name: str, parent: int | None = None) -> dict:
    r = await client.post(
        "/api/tags/categories",
        json={"scope": "garment", "name": name, "parent_category_id": parent},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _add_val(
    client: AsyncClient, headers, cat_id: int, value: str, parent_value: int | None = None
) -> dict:
    r = await client.post(
        f"/api/tags/categories/{cat_id}/values",
        json={"value": value, "parent_value_id": parent_value},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _export(client: AsyncClient, headers, scope: str = "garment") -> dict:
    r = await client.get("/api/tags/export", params={"scope": scope}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _import(client: AsyncClient, headers, payload: dict, scope: str = "garment"):
    return await client.post(
        "/api/tags/import", json=payload, params={"scope": scope}, headers=headers
    )


# ---------- 导出 ----------


async def test_export_uses_names_and_carries_metadata(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user("exp_shape")
    data = await _export(client, headers)

    assert data["kind"] == "wardrobe.tag-config"
    assert data["version"] == 1
    assert data["scope"] == "garment"
    assert [c["name"] for c in data["categories"]][:4] == ["大类", "小类", "颜色", "材质"]

    # 只带名字，一个 id 都不许有 —— 换库之后 id 全变，没法当锚点
    for cat in data["categories"]:
        assert "id" not in cat
        assert set(cat) == {"name", "sort_order", "parent", "values"}
        for val in cat["values"]:
            assert "id" not in val
            assert set(val) == {"value", "sort_order", "parent"}

    # 类别父子与取值父子都用名字表达
    xiaolei = next(c for c in data["categories"] if c["name"] == "小类")
    assert xiaolei["parent"] == "大类"
    tshirt = next(v for v in xiaolei["values"] if v["value"] == "T恤")
    assert tshirt["parent"] == "上衣"  # 父值在「大类」里，跨类别才解析得到


async def test_export_outfit_scope_has_its_own_tree(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user("exp_outfit")
    data = await _export(client, headers, "outfit")
    assert data["scope"] == "outfit"
    assert [c["name"] for c in data["categories"]] == ["季节", "风格", "色系"]


# ---------- 往返 ----------


async def test_roundtrip_restores_custom_tree_and_cascade(
    client: AsyncClient, make_user
) -> None:
    a, _ = await make_user("exp_full")
    b, _ = await make_user("imp_full")

    ver = await _add_cat(client, a, "版型")
    loose = await _add_val(client, a, ver["id"], "宽松")
    await _add_val(client, a, ver["id"], "修身")
    sub = await _add_cat(client, a, "版型细分", ver["id"])
    await _add_val(client, a, sub["id"], "落肩", loose["id"])

    data = await _export(client, a)
    r = await _import(client, b, data)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["categories_created"] == 2, res
    assert res["values_created"] == 3, res
    assert res["warnings"] == [], res["warnings"]

    cats = await _cats(client, b)
    ver2 = _find(cats, "版型")
    sub2 = _find(cats, "版型细分")
    assert sub2["parent_category_id"] == ver2["id"], "子类别没挂回父类别"
    loose2 = next(v for v in ver2["values"] if v["value"] == "宽松")
    drop = next(v for v in sub2["values"] if v["value"] == "落肩")
    assert drop["parent_value_id"] == loose2["id"], "子取值没挂回父取值"


async def test_import_twice_is_idempotent(client: AsyncClient, make_user) -> None:
    a, _ = await make_user("exp_twice")
    b, _ = await make_user("imp_twice")
    ver = await _add_cat(client, a, "版型")
    await _add_val(client, a, ver["id"], "宽松")
    data = await _export(client, a)

    first = (await _import(client, b, data)).json()
    assert first["categories_created"] == 1
    assert first["values_created"] == 1

    second = (await _import(client, b, data)).json()
    assert second["categories_created"] == 0
    assert second["values_created"] == 0
    assert second["categories_skipped"] >= 5
    # 再导一遍不该让树长胖
    assert len(await _cats(client, b)) == len(await _cats(client, a))


# ---------- 只补不删 ----------


async def test_import_never_touches_local_only_tags(client: AsyncClient, make_user) -> None:
    a, _ = await make_user("exp_keep")
    b, _ = await make_user("imp_keep")
    mine = await _add_cat(client, b, "本地独有")
    await _add_val(client, b, mine["id"], "只此一份")

    data = await _export(client, a)
    assert (await _import(client, b, data)).status_code == 200

    kept = _find(await _cats(client, b), "本地独有")
    assert [v["value"] for v in kept["values"]] == ["只此一份"]


async def test_existing_category_is_not_rewritten(client: AsyncClient, make_user) -> None:
    """本地已有同名类别时，文件里的取值补进来，但类别本身（含父级）不动。"""
    b, _ = await make_user("imp_merge")
    local = await _add_cat(client, b, "版型")
    await _add_val(client, b, local["id"], "宽松")

    payload = {"categories": [{"name": "版型", "values": [{"value": "宽松"}, {"value": "修身"}]}]}
    res = (await _import(client, b, payload)).json()
    assert res["categories_created"] == 0
    assert res["categories_skipped"] == 1
    assert res["values_created"] == 1  # 只补了「修身」
    assert res["values_skipped"] == 1  # 「宽松」已在

    ver = _find(await _cats(client, b), "版型")
    assert {v["value"] for v in ver["values"]} == {"宽松", "修身"}


# ---------- 拒绝与降级 ----------


async def test_import_rejects_foreign_or_too_new_files(
    client: AsyncClient, make_user
) -> None:
    headers, _ = await make_user("imp_bad")

    foreign = {"kind": "something-else", "categories": [{"name": "版型", "values": []}]}
    r = await _import(client, headers, foreign)
    assert r.status_code == 400, r.text
    assert "标签配置" in r.json()["detail"]

    future = {
        "kind": "wardrobe.tag-config",
        "version": 99,
        "categories": [{"name": "版型", "values": []}],
    }
    r = await _import(client, headers, future)
    assert r.status_code == 400, r.text
    assert "99" in r.json()["detail"]


async def test_import_accepts_minimal_handwritten_file(
    client: AsyncClient, make_user
) -> None:
    """手写一份最小的 JSON 也该能导：kind / version / scope 全部缺省。"""
    headers, _ = await make_user("imp_min")
    payload = {"categories": [{"name": "版型", "values": [{"value": "宽松"}]}]}
    r = await _import(client, headers, payload)
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["categories_created"] == 1
    assert res["values_created"] == 1
    assert res["scope"] == "garment"


async def test_unknown_parent_falls_back_to_top_level_with_warning(
    client: AsyncClient, make_user
) -> None:
    headers, _ = await make_user("imp_warn")
    payload = {"categories": [{"name": "子类别", "parent": "并不存在的类别"}]}
    res = (await _import(client, headers, payload)).json()
    assert res["categories_created"] == 1
    assert any("并不存在的类别" in w for w in res["warnings"]), res["warnings"]
    assert _find(await _cats(client, headers), "子类别")["parent_category_id"] is None


# ---------- 配额 ----------


async def test_import_obeys_tag_quota_as_a_whole(client: AsyncClient, make_user) -> None:
    a, _ = await make_user("exp_quota")
    b, _ = await make_user("imp_quota", tag_quota=1)
    ver = await _add_cat(client, a, "版型")
    for name in ("宽松", "修身", "落肩"):
        await _add_val(client, a, ver["id"], name)
    data = await _export(client, a)

    r = await _import(client, b, data)
    assert r.status_code == 403, r.text
    assert "上限" in r.json()["detail"]
    # 整批拒绝 —— 不能留下半个类别
    assert all(c["name"] != "版型" for c in await _cats(client, b))


# ---------- 作用域 ----------


async def test_scopes_stay_independent(client: AsyncClient, make_user) -> None:
    headers, _ = await make_user("imp_scope")
    payload = {"categories": [{"name": "版型", "values": [{"value": "宽松"}]}]}
    assert (await _import(client, headers, payload, scope="outfit")).status_code == 200

    assert all(c["name"] != "版型" for c in await _cats(client, headers, "garment"))
    assert any(c["name"] == "版型" for c in await _cats(client, headers, "outfit"))
