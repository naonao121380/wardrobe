"""标签体系：默认骨架、级联、增删改、两套体系隔离、跨用户隔离。"""

from httpx import AsyncClient

from app.services.tags import DEFAULT_CASCADE_VALUES, DEFAULT_VALUES, reset_tags

GARMENT_CATEGORIES = ["大类", "小类", "颜色", "材质"]
OUTFIT_CATEGORIES = ["季节", "风格", "色系"]
#: 大类取值名（用户指定的分类树）
MAJOR_VALUES = DEFAULT_VALUES["garment"]["大类"]


async def _cats(client: AsyncClient, headers: dict[str, str], scope: str = "garment") -> list[dict]:
    r = await client.get("/api/tags/categories", params={"scope": scope}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _cat(client: AsyncClient, headers: dict[str, str], name: str, scope: str = "garment") -> dict:
    found = next((c for c in await _cats(client, headers, scope) if c["name"] == name), None)
    assert found is not None, f"{scope} 下没有类别「{name}」"
    return found


async def _ensure_category(
    client: AsyncClient, headers: dict[str, str], name: str, scope: str = "garment"
) -> dict:
    """取该 scope 下的类别；不存在就建。默认骨架已建齐，通常直接命中。"""
    existing = next((c for c in await _cats(client, headers, scope) if c["name"] == name), None)
    if existing is not None:
        return existing
    r = await client.post(
        "/api/tags/categories", json={"scope": scope, "name": name}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _add_value(
    client: AsyncClient,
    headers: dict[str, str],
    category: dict,
    value: str,
    parent_value_id: int | None = None,
) -> dict:
    body: dict = {"value": value}
    if parent_value_id is not None:
        body["parent_value_id"] = parent_value_id
    r = await client.post(
        f"/api/tags/categories/{category['id']}/values", json=body, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


def _values(category: dict) -> list[str]:
    return [v["value"] for v in category["values"]]


# ---------- 默认骨架 ----------


async def test_default_garment_categories(client: AsyncClient, auth_headers) -> None:
    """骨架建齐（大类/小类/颜色/材质），但只有大类与颜色带预置取值。"""
    assert [c["name"] for c in await _cats(client, auth_headers, "garment")] == GARMENT_CATEGORIES


async def test_default_outfit_categories(client: AsyncClient, auth_headers) -> None:
    assert [c["name"] for c in await _cats(client, auth_headers, "outfit")] == OUTFIT_CATEGORIES


async def test_default_values_are_preseeded(client: AsyncClient, auth_headers) -> None:
    by_name = {c["name"]: _values(c) for c in await _cats(client, auth_headers)}
    assert by_name["大类"] == MAJOR_VALUES
    # 颜色预置一套标准词表，前端按色名渲染色块
    assert by_name["颜色"][:4] == ["黑", "白", "灰", "米白"]
    assert len(by_name["颜色"]) == 20
    assert len(set(by_name["颜色"])) == 20


async def test_minor_values_are_preseeded_per_major(client: AsyncClient, auth_headers) -> None:
    """小类整套预置，且按大类分组挂好父值（选了上衣就只列上装那 10 个）。"""
    major = await _cat(client, auth_headers, "大类")
    major_ids = {v["value"]: v["id"] for v in major["values"]}
    minor = await _cat(client, auth_headers, "小类")

    expected = DEFAULT_CASCADE_VALUES["garment"]["小类"]
    assert set(expected) == set(MAJOR_VALUES), "每个大类都要有一组小类"

    grouped: dict[int | None, list[str]] = {}
    for v in minor["values"]:
        grouped.setdefault(v["parent_value_id"], []).append(v["value"])

    assert len(minor["values"]) == sum(len(v) for v in expected.values())
    for major_name, children in expected.items():
        assert grouped[major_ids[major_name]] == children, f"{major_name} 的小类挂错了"


async def test_minor_tree_matches_the_agreed_catalogue(client: AsyncClient, auth_headers) -> None:
    """钉死用户敲定的分类树 —— 改这里等于改衣橱的分类口径，必须是有意为之。

    各大类的小类数量不等（上衣 12、连衣裙 9，其余各 10）是**有意的**：按真实款式
    铺开，不强凑成每类 10 个。
    """
    expected = DEFAULT_CASCADE_VALUES["garment"]["小类"]

    assert expected["上衣"] == [
        "T恤", "衬衫", "针织衫", "卫衣", "吊带", "马甲",
        "西装", "风衣", "大衣", "羽绒服", "外套", "内搭",
    ]
    assert expected["连衣裙"] == [
        "衬衫裙", "针织裙", "吊带裙", "小黑裙", "娃娃裙",
        "收腰裙", "廓形裙", "礼服裙", "法式裙",
    ]
    assert len(expected["裤子"]) == 10
    assert len(expected["半身裙"]) == 10
    assert len(expected["鞋子"]) == 10
    assert len(expected["袜子"]) == 10
    assert len(expected["包包"]) == 10
    assert len(expected["配饰"]) == 10

    minor = await _cat(client, auth_headers, "小类")
    assert len(minor["values"]) == sum(len(v) for v in expected.values()) == 81
    # 全树不许重名：重名会让 VLM 的裸名 fallback 指错标签
    assert len(set(_values(minor))) == 81


async def test_material_axis_starts_empty(client: AsyncClient, auth_headers) -> None:
    """材质只建类别，取值留给用户自己加 —— 材质五花八门，预置只会变成噪音。"""
    assert _values(await _cat(client, auth_headers, "材质")) == []


async def test_outfit_style_and_palette_are_preset(client: AsyncClient, auth_headers) -> None:
    """风格 / 色系预置一套常用词：AI 推荐搭配标签时得有候选可挑。

    与材质相反 —— 这两个维度相对收敛，预置能省掉用户从零建词表的功夫。
    """
    style = _values(await _cat(client, auth_headers, "风格", "outfit"))
    palette = _values(await _cat(client, auth_headers, "色系", "outfit"))
    assert "通勤" in style and "休闲" in style
    assert "黑白灰" in palette and "大地色" in palette
    # 不许重名：重名会让 VLM 的裸名 fallback 指错标签
    assert len(set(style + palette)) == len(style) + len(palette)


async def test_outfit_presets_season(client: AsyncClient, auth_headers) -> None:
    assert _values(await _cat(client, auth_headers, "季节", "outfit")) == ["春", "夏", "秋", "冬"]


async def test_cascade_wiring_matches_defaults(client: AsyncClient, auth_headers) -> None:
    """级联：小类 → 大类；颜色/材质/风格/色系都是顶层。"""
    major = await _cat(client, auth_headers, "大类")
    assert (await _cat(client, auth_headers, "小类"))["parent_category_id"] == major["id"]

    assert (await _cat(client, auth_headers, "颜色"))["parent_category_id"] is None
    assert (await _cat(client, auth_headers, "材质"))["parent_category_id"] is None
    for name in ("季节", "风格", "色系"):
        assert (await _cat(client, auth_headers, name, "outfit"))["parent_category_id"] is None


async def test_two_scopes_are_fully_independent(client: AsyncClient, auth_headers) -> None:
    garment = await _cats(client, auth_headers, "garment")
    outfit = await _cats(client, auth_headers, "outfit")

    assert {c["id"] for c in garment}.isdisjoint({c["id"] for c in outfit})
    assert {c["name"] for c in garment}.isdisjoint({c["name"] for c in outfit})


# ---------- 级联行为 ----------


async def test_minor_value_can_be_parented_to_major_value(client: AsyncClient, auth_headers) -> None:
    major = await _cat(client, auth_headers, "大类")
    top = next(v for v in major["values"] if v["value"] == "上衣")
    minor = await _cat(client, auth_headers, "小类")

    coat = await _add_value(client, auth_headers, minor, "测试马甲", parent_value_id=top["id"])
    assert coat["parent_value_id"] == top["id"]

    refreshed = await _cat(client, auth_headers, "小类")
    pairs = [(v["value"], v["parent_value_id"]) for v in refreshed["values"]]
    assert ("测试马甲", top["id"]) in pairs


async def test_minor_value_may_stay_uncategorized(client: AsyncClient, auth_headers) -> None:
    """小类取值允许不挂父值（前端归到「未归类」一档）。"""
    minor = await _cat(client, auth_headers, "小类")
    created = await _add_value(client, auth_headers, minor, "说不清")
    assert created["parent_value_id"] is None


async def test_minor_value_rejects_parent_from_other_category(
    client: AsyncClient, auth_headers
) -> None:
    color = await _cat(client, auth_headers, "颜色")
    black = next(v for v in color["values"] if v["value"] == "黑")
    minor = await _cat(client, auth_headers, "小类")

    r = await client.post(
        f"/api/tags/categories/{minor['id']}/values",
        json={"value": "跨类别父值", "parent_value_id": black["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "父类别" in r.json()["detail"]


async def test_top_level_value_cannot_have_parent(client: AsyncClient, auth_headers) -> None:
    major = await _cat(client, auth_headers, "大类")
    outer = next(v for v in major["values"] if v["value"] == "上衣")
    color = await _cat(client, auth_headers, "颜色")

    r = await client.post(
        f"/api/tags/categories/{color['id']}/values",
        json={"value": "藕荷", "parent_value_id": outer["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "顶层类别" in r.json()["detail"]


async def test_minor_value_rejects_parent_from_other_user(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    theirs_major = await _cat(client, second_headers, "大类")
    their_value = theirs_major["values"][0]["id"]
    minor = await _cat(client, auth_headers, "小类")

    r = await client.post(
        f"/api/tags/categories/{minor['id']}/values",
        json={"value": "跨用户父值", "parent_value_id": their_value},
        headers=auth_headers,
    )
    assert r.status_code == 404


async def test_category_parent_must_be_same_scope(client: AsyncClient, auth_headers) -> None:
    garment_major = await _cat(client, auth_headers, "大类")
    r = await client.post(
        "/api/tags/categories",
        json={"scope": "outfit", "name": "小类", "parent_category_id": garment_major["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "作用域" in r.json()["detail"]


async def test_only_two_layers_allowed(client: AsyncClient, auth_headers) -> None:
    """父类别本身必须是顶层 —— 「小类」不能再当别人的父。"""
    minor = await _cat(client, auth_headers, "小类")
    r = await client.post(
        "/api/tags/categories",
        json={"scope": "garment", "name": "小小类", "parent_category_id": minor["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "两层" in r.json()["detail"]


async def test_category_cannot_be_its_own_parent(client: AsyncClient, auth_headers) -> None:
    color = await _cat(client, auth_headers, "颜色")
    r = await client.patch(
        f"/api/tags/categories/{color['id']}",
        json={"parent_category_id": color["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_value_cannot_be_its_own_parent(client: AsyncClient, auth_headers) -> None:
    minor = await _cat(client, auth_headers, "小类")
    created = await _add_value(client, auth_headers, minor, "测试自引用值")
    r = await client.patch(
        f"/api/tags/values/{created['id']}",
        json={"parent_value_id": created["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400


async def test_category_with_values_cannot_gain_parent(client: AsyncClient, auth_headers) -> None:
    """带取值的顶层类别再挂父级会变成三层 —— 拒绝。"""
    major = await _cat(client, auth_headers, "大类")
    created = await client.post(
        "/api/tags/categories", json={"scope": "garment", "name": "版型"}, headers=auth_headers
    )
    assert created.status_code == 201, created.text

    r = await client.patch(
        f"/api/tags/categories/{major['id']}",
        json={"parent_category_id": created.json()["id"]},
        headers=auth_headers,
    )
    assert r.status_code == 400
    assert "三层" in r.json()["detail"]


async def test_deleting_major_value_demotes_minor_value(client: AsyncClient, auth_headers) -> None:
    """删掉某个大类取值，挂在它下面的小类降级为未归类，而不是被连带删除。"""
    major = await _cat(client, auth_headers, "大类")
    outer = await _add_value(client, auth_headers, major, "临时大类")
    minor = await _cat(client, auth_headers, "小类")
    coat = await _add_value(client, auth_headers, minor, "临时小类", parent_value_id=outer["id"])

    assert (await client.delete(f"/api/tags/values/{outer['id']}", headers=auth_headers)).status_code == 200

    after = await _cat(client, auth_headers, "小类")
    assert (coat["value"], None) in [(v["value"], v["parent_value_id"]) for v in after["values"]]
    assert coat["id"] in [v["id"] for v in after["values"]]
    # 预置的「风衣」仍好好挂在上衣下，不受影响
    preset = next(v for v in after["values"] if v["value"] == "风衣")
    top = next(v for v in major["values"] if v["value"] == "上衣")
    assert preset["parent_value_id"] == top["id"]


async def test_deleting_major_category_demotes_minor_category(
    client: AsyncClient, auth_headers
) -> None:
    """删掉「大类」类别，「小类」降级为顶层类别，取值一个不丢。"""
    major = await _cat(client, auth_headers, "大类")
    outer = await _add_value(client, auth_headers, major, "临时大类2")
    minor = await _cat(client, auth_headers, "小类")
    await _add_value(client, auth_headers, minor, "临时小类2", parent_value_id=outer["id"])

    deleted = await client.delete(f"/api/tags/categories/{major['id']}", headers=auth_headers)
    assert deleted.status_code == 200
    assert deleted.json()["demoted_categories"] == 1

    after = await _cat(client, auth_headers, "小类")
    assert after["parent_category_id"] is None
    # 预置的 80 个一个不丢（父类别没了，父值也就无从挂起，全部降级为未归类）
    assert len(after["values"]) == sum(
        len(v) for v in DEFAULT_CASCADE_VALUES["garment"]["小类"].values()
    ) + 1
    assert all(v["parent_value_id"] is None for v in after["values"])


# ---------- 增删改 ----------


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
    assert _values(target) == ["通勤"]


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
    category = await _ensure_category(client, auth_headers, "材质")
    body = {"value": "测试取值"}
    first = await client.post(
        f"/api/tags/categories/{category['id']}/values", json=body, headers=auth_headers
    )
    assert first.status_code == 201
    second = await client.post(
        f"/api/tags/categories/{category['id']}/values", json=body, headers=auth_headers
    )
    assert second.status_code == 409


async def test_rename_and_delete_value(client: AsyncClient, auth_headers) -> None:
    category = await _ensure_category(client, auth_headers, "材质")
    created = await _add_value(client, auth_headers, category, "羊毛")

    renamed = await client.patch(
        f"/api/tags/values/{created['id']}", json={"value": "羊毛混纺"}, headers=auth_headers
    )
    assert renamed.status_code == 200
    assert renamed.json()["value"] == "羊毛混纺"

    assert (await client.delete(f"/api/tags/values/{created['id']}", headers=auth_headers)).status_code == 200
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


# ---------- 重置 ----------


def _preset_total() -> int:
    total = sum(len(v) for scope in DEFAULT_VALUES.values() for v in scope.values())
    for scope in DEFAULT_CASCADE_VALUES.values():
        total += sum(len(v) for groups in scope.values() for v in groups.values())
    return total


async def test_reset_tags_rebuilds_defaults_and_untags(
    client: AsyncClient, auth_headers, user, session_factory
) -> None:
    """重置标签体系：衣物保留，标签关联解除，默认骨架按当前方案重建。"""
    major = await _cat(client, auth_headers, "大类")
    created = await client.post(
        "/api/garments",
        json={"name": "待重置的衣物", "tag_value_ids": [major["values"][0]["id"]]},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    garment_id = created.json()["id"]
    assert len(created.json()["tags"]) == 1

    async with session_factory() as s:
        stats = await reset_tags(s, user.id)
        await s.commit()

    assert stats["untagged"] == 1
    assert stats["removed_categories"] == len(GARMENT_CATEGORIES) + len(OUTFIT_CATEGORIES)
    assert stats["created_categories"] == len(GARMENT_CATEGORIES) + len(OUTFIT_CATEGORIES)
    assert stats["created_values"] == _preset_total()

    # 衣物还在，只是没标签了
    after = await client.get(f"/api/garments/{garment_id}", headers=auth_headers)
    assert after.status_code == 200
    assert after.json()["name"] == "待重置的衣物"
    assert after.json()["tags"] == []

    # 重建后的默认项与全新用户一致，级联关系也重建了
    cats = await _cats(client, auth_headers)
    assert [c["name"] for c in cats] == GARMENT_CATEGORIES
    assert _values(cats[0]) == MAJOR_VALUES
    assert cats[1]["parent_category_id"] == cats[0]["id"]


async def test_reset_tags_is_idempotent(client: AsyncClient, auth_headers, user, session_factory) -> None:
    """连着重置两次，结果一致（不会越删越多 / 重复播种）。"""
    for _ in range(2):
        async with session_factory() as s:
            await reset_tags(s, user.id)
            await s.commit()

    cats = await _cats(client, auth_headers)
    assert [c["name"] for c in cats] == GARMENT_CATEGORIES
    assert [c["name"] for c in await _cats(client, auth_headers, "outfit")] == OUTFIT_CATEGORIES
    assert sum(len(_values(c)) for c in cats) == len(DEFAULT_VALUES["garment"]["大类"]) + len(
        DEFAULT_VALUES["garment"]["颜色"]
    ) + sum(len(v) for v in DEFAULT_CASCADE_VALUES["garment"]["小类"].values())


# ---------- 引用计数 ----------


async def test_usage_count_tracks_garments(client: AsyncClient, auth_headers) -> None:
    """被衣物引用的取值 usage_count > 0，其余为 0 —— 前端据此决定删前要不要确认。"""
    material = await _cat(client, auth_headers, "材质")
    assert material["usage_count"] == 0
    assert all(v["usage_count"] == 0 for v in material["values"])

    wool = await _add_value(client, auth_headers, material, "羊毛")
    assert wool["usage_count"] == 0

    # 建两件衣物，都打上「羊毛」
    for name in ("羊毛大衣", "羊毛围巾"):
        garment = await client.post("/api/garments", json={"name": name}, headers=auth_headers)
        assert garment.status_code == 201, garment.text
        r = await client.patch(
            f"/api/garments/{garment.json()['id']}",
            json={"tag_value_ids": [wool["id"]]},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text

    after = await _cat(client, auth_headers, "材质")
    used = next(v for v in after["values"] if v["id"] == wool["id"])
    assert used["usage_count"] == 2
    assert after["usage_count"] == 2

    # 删掉一件衣物 → 计数回落
    gone = await client.delete(f"/api/garments/{garment.json()['id']}", headers=auth_headers)
    assert gone.status_code == 200
    after = await _cat(client, auth_headers, "材质")
    assert next(v for v in after["values"] if v["id"] == wool["id"])["usage_count"] == 1


async def test_usage_count_is_owner_scoped(
    client: AsyncClient, auth_headers, second_headers
) -> None:
    """别人的衣物不能给自己的标签计数 —— 否则「没人用」会误判成「有人用」。"""
    mine = await _cat(client, auth_headers, "材质")
    wool = await _add_value(client, auth_headers, mine, "羊绒")

    their_garment = await client.post("/api/garments", json={"name": "别人的"}, headers=second_headers)
    assert their_garment.status_code == 201
    their_value = await _add_value(client, second_headers,
                                   await _cat(client, second_headers, "材质"), "羊绒")
    r = await client.patch(
        f"/api/garments/{their_garment.json()['id']}",
        json={"tag_value_ids": [their_value["id"]]},
        headers=second_headers,
    )
    assert r.status_code == 200, r.text

    # 我自己的同名取值仍然是 0，别人的才是 1
    after_mine = await _cat(client, auth_headers, "材质")
    assert next(v for v in after_mine["values"] if v["id"] == wool["id"])["usage_count"] == 0
    after_theirs = await _cat(client, second_headers, "材质")
    assert next(v for v in after_theirs["values"] if v["id"] == their_value["id"])["usage_count"] == 1


async def test_outfit_scope_has_no_usage(client: AsyncClient, auth_headers) -> None:
    """outfit 作用域还没有关联表，计数一律 0。"""
    for cat in await _cats(client, auth_headers, "outfit"):
        assert cat["usage_count"] == 0
        assert all(v["usage_count"] == 0 for v in cat["values"])
