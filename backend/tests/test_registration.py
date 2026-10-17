"""注册三模式（open / invite / closed）与邀请码。

架构上有一条容易做错的：注册模式的初始值取自环境变量
（``WARDROBE_ALLOW_REGISTRATION``），但那**只在库里还没有设置行时**生效。
之后就以库里的为准 —— 否则管理员在界面上开了注册，服务重启一次又被盖回去。

另一条是邀请码的占用必须是原子的：两个人同时用同一枚「一人一码」注册，
只能有一个成功（和刷新令牌轮换同一类并发坑）。
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from httpx import AsyncClient

from app.core.time import now_utc
from app.models.system import InviteCode
from tests.conftest import TEST_PASSWORD


async def _policy(client: AsyncClient) -> dict:
    r = await client.get("/api/auth/registration")
    assert r.status_code == 200, r.text
    return r.json()


async def _register(client: AsyncClient, username: str, invite: str | None = None):
    body: dict = {"username": username, "password": "password123"}
    if invite is not None:
        body["invite_code"] = invite
    return await client.post("/api/auth/register", json=body)


async def _mode(client: AsyncClient, admin_headers, mode: str) -> dict:
    r = await client.patch("/api/admin/settings", json={"mode": mode}, headers=admin_headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _codes(client: AsyncClient, admin_headers, **payload):
    r = await client.post("/api/admin/invite-codes", json=payload, headers=admin_headers)
    assert r.status_code == 201, r.text
    return r.json()


# ---------- 注册策略端点 ----------


async def test_policy_endpoint_is_public(client: AsyncClient, admin) -> None:
    """登录页在拿到令牌**之前**就要知道注册开不开，所以这个端点不鉴权。"""
    r = await client.get("/api/auth/registration")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"mode", "invite_required"}
    assert body["mode"] in {"open", "invite", "closed"}
    assert body["invite_required"] is (body["mode"] == "invite")


async def test_policy_defaults_to_closed_without_env(client: AsyncClient, admin) -> None:
    """测试环境没开 ``WARDROBE_ALLOW_REGISTRATION``，初始模式是 closed。"""
    assert (await _policy(client))["mode"] == "closed"


async def test_policy_defaults_to_open_with_env(client: AsyncClient, admin, allow_registration) -> None:
    assert (await _policy(client))["mode"] == "open"


# ---------- closed ----------


async def test_closed_mode_rejects_registration(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "closed")
    r = await _register(client, "wannabe")
    assert r.status_code == 403
    assert "未开放注册" in r.json()["detail"]


# ---------- open ----------


async def test_open_mode_registers_without_invite(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "open")
    r = await _register(client, "freebird")
    assert r.status_code == 201, r.text
    assert r.json()["user"]["username"] == "freebird"
    assert r.json()["user"]["is_admin"] is False
    assert r.json()["access_token"]
    # 注册完立刻能用自己的令牌（顺带验证播种的标签不占配额）
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    assert (await client.get("/api/auth/quota", headers=h)).json()["tags"]["used"] == 0


async def test_open_mode_still_rejects_duplicates(client: AsyncClient, admin_headers, admin) -> None:
    await _mode(client, admin_headers, "open")
    r = await _register(client, "boss")
    assert r.status_code == 409


# ---------- invite ----------


async def test_invite_mode_requires_a_code(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "invite")
    r = await _register(client, "codeless")
    assert r.status_code == 400
    assert "邀请码" in r.json()["detail"]


async def test_invite_mode_rejects_unknown_code(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "invite")
    r = await _register(client, "clueless", "ZZZZ-ZZZZ")
    assert r.status_code == 400
    assert "无效" in r.json()["detail"]


async def test_invite_code_is_case_and_dash_insensitive(client: AsyncClient, admin_headers) -> None:
    """用户手抄邀请码时可能写小写、漏短横、带空格 —— 都该当成同一个码。"""
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1, max_uses=1))[0]["code"]

    messy = " " + code.lower().replace("-", "") + " "
    r = await _register(client, "sloppy", messy)
    assert r.status_code == 201, r.text


async def test_one_use_code_is_spent_after_one_registration(
    client: AsyncClient, admin_headers
) -> None:
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1, max_uses=1))[0]
    assert (await _register(client, "first", code["code"])).status_code == 201

    assert (await _register(client, "second", code["code"])).status_code == 400

    listed = (await client.get("/api/admin/invite-codes", headers=admin_headers)).json()
    row = next(c for c in listed if c["id"] == code["id"])
    assert row["used_count"] == 1 and row["remaining"] == 0 and row["usable"] is False


async def test_multi_use_code_can_register_several(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1, max_uses=3))[0]

    for i in range(3):
        assert (await _register(client, f"batch{i}", code["code"])).status_code == 201
    assert (await _register(client, "batch3", code["code"])).status_code == 400


async def test_unlimited_code(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1))[0]  # max_uses 不传 = 不限
    assert code["max_uses"] is None and code["remaining"] is None

    for i in range(4):
        assert (await _register(client, f"many{i}", code["code"])).status_code == 201


async def test_revoked_code_stops_working(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1))[0]

    killed = await client.delete(f"/api/admin/invite-codes/{code['id']}", headers=admin_headers)
    assert killed.status_code == 200, killed.text
    assert killed.json()["revoked_at"] is not None
    assert killed.json()["usable"] is False

    assert (await _register(client, "toolate", code["code"])).status_code == 400


async def test_expired_code_is_rejected(client: AsyncClient, admin_headers, session_factory) -> None:
    """过期判断靠时间戳，所以直接在库里塞一个昨天就该过期的码。"""
    await _mode(client, admin_headers, "invite")
    expired = "AAAA-OLD1"
    async with session_factory() as s:
        s.add(
            InviteCode(
                code=expired,
                max_uses=None,
                expires_at=now_utc() - timedelta(days=1),
            )
        )
        await s.commit()

    listed = (await client.get("/api/admin/invite-codes", headers=admin_headers)).json()
    row = next(c for c in listed if c["code"] == expired)
    assert row["expires_at"] is not None and row["usable"] is False

    assert (await _register(client, "latecomer", expired)).status_code == 400


async def test_revoking_missing_code_is_404(client: AsyncClient, admin_headers) -> None:
    assert (
        await client.delete("/api/admin/invite-codes/9999", headers=admin_headers)
    ).status_code == 404


async def test_same_code_cannot_register_two_people_concurrently(
    client: AsyncClient, admin_headers
) -> None:
    """同一枚「一人一码」被两个人同时用：只能一个成功。

    回归用例：占用写成「先 SELECT 判断 used_count，再 UPDATE」时，两个并发请求会
    双双通过判断、各建一个账号（和刷新令牌轮换是同一类坑）。
    """
    await _mode(client, admin_headers, "invite")
    code = (await _codes(client, admin_headers, count=1, max_uses=1))[0]["code"]

    a, b = await asyncio.gather(
        _register(client, "racer_a", code),
        _register(client, "racer_b", code),
    )
    assert sorted([a.status_code, b.status_code]) == [201, 409], (a.text, b.text)

    listed = (await client.get("/api/admin/invite-codes", headers=admin_headers)).json()
    row = next(c for c in listed if c["code"] == code)
    assert row["used_count"] == 1


# ---------- 邀请码管理 ----------


async def test_generate_batch_and_list(client: AsyncClient, admin_headers) -> None:
    made = await _codes(client, admin_headers, count=5, note="给一个班", max_uses=1)
    assert len(made) == 5
    assert len({c["code"] for c in made}) == 5  # 互不重复
    assert all(c["note"] == "给一个班" for c in made)
    # 字母表去掉了 0/O/1/I/L 这些看起来一样的字符，方便手抄
    for c in made:
        assert len(c["code"]) == 9 and c["code"][4] == "-"
        assert not (set(c["code"]) & set("0O1IL"))

    listed = (await client.get("/api/admin/invite-codes", headers=admin_headers)).json()
    assert {c["id"] for c in made} <= {c["id"] for c in listed}
    assert listed[0]["id"] == max(c["id"] for c in listed)  # 新的在前


async def test_usable_counter_reflects_actions(client: AsyncClient, admin_headers) -> None:
    """设置页那个「可用 N 枚」的计数要跟着生成 / 用掉 / 吊销动。"""
    base = (await client.get("/api/admin/settings", headers=admin_headers)).json()
    assert base["invite_codes_usable"] == 0

    made = await _codes(client, admin_headers, count=2, max_uses=1)
    after_create = (await client.get("/api/admin/settings", headers=admin_headers)).json()
    assert after_create["invite_codes_usable"] == 2

    await client.delete(f"/api/admin/invite-codes/{made[0]['id']}", headers=admin_headers)
    after_revoke = (await client.get("/api/admin/settings", headers=admin_headers)).json()
    assert after_revoke["invite_codes_usable"] == 1


async def test_generate_count_bounds(client: AsyncClient, admin_headers) -> None:
    r = await client.post(
        "/api/admin/invite-codes", json={"count": 0}, headers=admin_headers
    )
    assert r.status_code == 422
    r = await client.post(
        "/api/admin/invite-codes", json={"count": 999}, headers=admin_headers
    )
    assert r.status_code == 422


async def test_settings_rejects_unknown_mode(client: AsyncClient, admin_headers) -> None:
    r = await client.patch(
        "/api/admin/settings", json={"mode": "半开放"}, headers=admin_headers
    )
    assert r.status_code == 422


async def test_mode_change_is_not_undone_by_settings_reload(
    client: AsyncClient, admin_headers, allow_registration
) -> None:
    """环境变量开了注册，但管理员在库里选 closed —— 库里的说了算。

    否则界面上关掉注册、服务重启一次又被环境变量盖回来（静默地）。
    """
    await _mode(client, admin_headers, "closed")
    assert (await _policy(client))["mode"] == "closed"
    assert (await _register(client, "still_closed")).status_code == 403


async def test_password_policy_applies_to_registration(client: AsyncClient, admin_headers) -> None:
    await _mode(client, admin_headers, "open")
    r = await client.post(
        "/api/auth/register", json={"username": "weakpass", "password": "short"}
    )
    assert r.status_code == 422
