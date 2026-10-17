"""管理员接口：权限边界、用户 CRUD、几条「别把自己锁在门外」的保护、重置密码。

值得单独说的保护有两条：

- **不能删自己 / 停用自己 / 取消自己的管理员** —— 误操作一次就再也进不去了。
- **不能取消最后一个管理员** —— 系统里没有管理员时，注册模式、配额、别人的密码
  就再也改不动了，只能进容器敲 CLI。
"""

from __future__ import annotations

from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD


async def _login(client: AsyncClient, username: str, password: str = TEST_PASSWORD):
    return await client.post("/api/auth/login", json={"username": username, "password": password})


async def _make(client: AsyncClient, admin_headers, username: str = "kid", **extra):
    r = await client.post(
        "/api/admin/users", json={"username": username, "password": TEST_PASSWORD, **extra},
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 权限边界 ----------


async def test_admin_endpoints_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/admin/users")).status_code == 401
    assert (await client.get("/api/admin/settings")).status_code == 401
    assert (await client.get("/api/admin/invite-codes")).status_code == 401


async def test_non_admin_gets_403(client: AsyncClient, auth_headers) -> None:
    """普通用户打管理接口一律 403（**不是** 404 —— 不必藏「这里有管理接口」）。"""
    for path in ("/api/admin/users", "/api/admin/settings", "/api/admin/invite-codes"):
        r = await client.get(path, headers=auth_headers)
        assert r.status_code == 403, f"{path} → {r.status_code}"
        assert r.json()["detail"] == "需要管理员权限"


async def test_non_admin_cannot_create_or_reset(client: AsyncClient, auth_headers, user) -> None:
    r = await client.post(
        "/api/admin/users", json={"username": "x", "password": TEST_PASSWORD}, headers=auth_headers
    )
    assert r.status_code == 403
    r = await client.post(
        f"/api/admin/users/{user.id}/password",
        json={"new_password": "whatever12345"},
        headers=auth_headers,
    )
    assert r.status_code == 403


async def test_admin_reaches_endpoints(client: AsyncClient, admin_headers) -> None:
    assert (await client.get("/api/admin/users", headers=admin_headers)).status_code == 200
    assert (await client.get("/api/admin/settings", headers=admin_headers)).status_code == 200


# ---------- 用户列表与详情 ----------


async def test_list_includes_stats_quotas_and_masked_llm(
    client: AsyncClient, admin_headers, admin
) -> None:
    users = (await client.get("/api/admin/users", headers=admin_headers)).json()
    me = next(u for u in users if u["username"] == "boss")
    assert set(me["stats"]) == {"garments", "outfits", "tags", "sessions"}
    assert set(me["quotas"]) == {"garments", "outfits", "tags"}
    # 密钥只回脱敏串，管理员也看不到完整的
    assert set(me["llm"]) == {"base_url", "model", "api_key_masked", "from_admin", "configured"}
    assert me["llm"]["configured"] is False


async def test_detail_shows_the_whole_picture(client: AsyncClient, admin_headers, make_user) -> None:
    """「查阅普通用户的所有信息」：用量、配额、模型配置、还登着的设备。"""
    headers, uid = await make_user(username="watched")
    await client.post("/api/garments", json={"name": "被看着的衣物"}, headers=headers)

    detail = (await client.get(f"/api/admin/users/{uid}", headers=admin_headers)).json()
    assert detail["username"] == "watched"
    assert detail["stats"]["garments"] == 1
    assert len(detail["sessions"]) == 1
    assert detail["sessions"][0]["id"] > 0
    assert detail["last_seen_at"] is not None
    assert detail["quotas"]["garments"]["limit"] is None


async def test_missing_user_is_404(client: AsyncClient, admin_headers) -> None:
    assert (await client.get("/api/admin/users/9999", headers=admin_headers)).status_code == 404
    r = await client.post(
        "/api/admin/users/9999/password",
        json={"new_password": "whatever12345"},
        headers=admin_headers,
    )
    assert r.status_code == 404


# ---------- 建号 ----------


async def test_create_user_with_quotas_and_preseeded_tags(
    client: AsyncClient, admin_headers
) -> None:
    made = await _make(
        client, admin_headers, "fresh", display_name="新人",
        garment_quota=10, outfit_quota=5, tag_quota=3,
    )
    assert made["quotas"]["garments"] == {"used": 0, "limit": 10}
    assert made["quotas"]["tags"] == {"used": 0, "limit": 3}
    assert made["is_active"] is True and made["is_admin"] is False

    # 默认标签照常播种，否则新用户进去连标签都没有
    login = await _login(client, "fresh")
    assert login.status_code == 200
    h = {"Authorization": f"Bearer {login.json()['access_token']}"}
    cats = (await client.get("/api/tags/categories", headers=h)).json()
    assert len(cats) == 4  # 大类 / 小类 / 颜色 / 材质
    # 播种的取值不占配额
    assert (await client.get("/api/auth/quota", headers=h)).json()["tags"]["used"] == 0


async def test_create_user_defaults_to_seeded_username(client: AsyncClient, admin_headers) -> None:
    made = await _make(client, admin_headers, "nodisplay")
    assert made["display_name"] == "nodisplay"


async def test_create_user_rejects_duplicate_username(client: AsyncClient, admin_headers, admin) -> None:
    r = await client.post(
        "/api/admin/users", json={"username": "boss", "password": TEST_PASSWORD},
        headers=admin_headers,
    )
    assert r.status_code == 409
    assert r.json()["detail"] == "用户名已被占用"


async def test_create_user_rejects_weak_password(client: AsyncClient, admin_headers) -> None:
    r = await client.post(
        "/api/admin/users", json={"username": "weakling", "password": "short"},
        headers=admin_headers,
    )
    assert r.status_code == 422


# ---------- 改用户 ----------


async def test_update_profile_and_role(client: AsyncClient, admin_headers, make_user) -> None:
    _, uid = await make_user(username="promote_me")

    r = await client.patch(
        f"/api/admin/users/{uid}",
        json={"display_name": "升级了", "is_admin": True},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "升级了" and r.json()["is_admin"] is True

    # 有 2 个管理员时可以把其中一个降回去
    back = await client.patch(
        f"/api/admin/users/{uid}", json={"is_admin": False}, headers=admin_headers
    )
    assert back.status_code == 200
    assert back.json()["is_admin"] is False


async def test_deactivating_kicks_devices_immediately(
    client: AsyncClient, admin_headers, make_user
) -> None:
    """停用要**立刻**生效：否则手上那个访问令牌还能用最多 30 分钟。"""
    headers, uid = await make_user(username="banned")
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    r = await client.patch(
        f"/api/admin/users/{uid}", json={"is_active": False}, headers=admin_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    assert r.json()["stats"]["sessions"] == 0  # 设备被顺手踢下线

    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    relogin = await _login(client, "banned")
    assert relogin.status_code == 403
    assert relogin.json()["detail"] == "账号已停用"

    back = await client.patch(
        f"/api/admin/users/{uid}", json={"is_active": True}, headers=admin_headers
    )
    assert back.status_code == 200
    assert (await _login(client, "banned")).status_code == 200


async def test_patch_ignores_untouched_fields(client: AsyncClient, admin_headers, make_user) -> None:
    _, uid = await make_user(username="partial", garment_quota=4)
    r = await client.patch(
        f"/api/admin/users/{uid}", json={"display_name": "只改名字"}, headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["quotas"]["garments"]["limit"] == 4  # 没传的配额不动


# ---------- 保护 ----------


async def test_cannot_delete_demote_or_disable_self(client: AsyncClient, admin_headers, admin) -> None:
    assert (
        await client.delete(f"/api/admin/users/{admin.id}", headers=admin_headers)
    ).status_code == 400
    assert (
        await client.patch(
            f"/api/admin/users/{admin.id}", json={"is_admin": False}, headers=admin_headers
        )
    ).status_code == 400
    assert (
        await client.patch(
            f"/api/admin/users/{admin.id}", json={"is_active": False}, headers=admin_headers
        )
    ).status_code == 400


async def test_second_admin_can_still_be_demoted(client: AsyncClient, admin_headers) -> None:
    """保护不能过度：有第二个管理员时，把其中一个降级是合法操作。"""
    made = await _make(client, admin_headers, "second_admin", is_admin=True)
    r = await client.patch(
        f"/api/admin/users/{made['id']}", json={"is_admin": False}, headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["is_admin"] is False
    # 降完还是能继续管（发起者的管理员身份没被影响）
    assert (await client.get("/api/admin/users", headers=admin_headers)).status_code == 200


async def test_delete_user_removes_data(client: AsyncClient, admin_headers, make_user) -> None:
    headers, uid = await make_user(username="doomed")
    await client.post("/api/garments", json={"name": "要被删掉的衣物"}, headers=headers)

    r = await client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True and body["username"] == "doomed"

    users = (await client.get("/api/admin/users", headers=admin_headers)).json()
    assert all(u["username"] != "doomed" for u in users)
    assert (await _login(client, "doomed")).status_code == 401


async def test_delete_missing_user_is_404(client: AsyncClient, admin_headers) -> None:
    assert (await client.delete("/api/admin/users/9999", headers=admin_headers)).status_code == 404


# ---------- 重置密码 / 踢下线 ----------


async def test_reset_password_revokes_sessions(client: AsyncClient, admin_headers, make_user) -> None:
    headers, uid = await make_user(username="forgetful")
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    r = await client.post(
        f"/api/admin/users/{uid}/password",
        json={"new_password": "brand-new-pass-9"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] >= 1

    # 旧访问令牌立刻作废（JWT 里带 sid，每次都要核对会话行）
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    # 旧密码不行，新密码行
    assert (await _login(client, "forgetful")).status_code == 401
    assert (await _login(client, "forgetful", "brand-new-pass-9")).status_code == 200


async def test_reset_password_rejects_weak(client: AsyncClient, admin_headers, make_user) -> None:
    _, uid = await make_user(username="keepstrong")
    r = await client.post(
        f"/api/admin/users/{uid}/password", json={"new_password": "short"}, headers=admin_headers
    )
    assert r.status_code == 422


async def test_force_logout_without_changing_password(
    client: AsyncClient, admin_headers, make_user
) -> None:
    headers, uid = await make_user(username="kicked")
    r = await client.post(f"/api/admin/users/{uid}/logout", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["revoked"] == 1

    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401
    # 密码没变，还能重新登录
    assert (await _login(client, "kicked")).status_code == 200


async def test_force_logout_is_idempotent(client: AsyncClient, admin_headers, make_user) -> None:
    _, uid = await make_user(username="kicked_twice")
    first = await client.post(f"/api/admin/users/{uid}/logout", headers=admin_headers)
    second = await client.post(f"/api/admin/users/{uid}/logout", headers=admin_headers)
    assert first.json()["revoked"] == 1
    assert second.json()["revoked"] == 0
