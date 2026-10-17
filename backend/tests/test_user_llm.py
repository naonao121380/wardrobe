"""按用户的大模型配置，以及「访问令牌绑定会话」这条安全边。

需求里的三句话对应三组断言：

1. **不默认注入** —— 新用户没有任何模型配置，``/meta`` 报 ``llm_configured=false``。
2. **不同用户互不通用** —— A 配了模型不代表 B 能用；A 的 Key 不会被拿去处理 B 的衣物。
3. **用户没填时管理员可代设** —— ``llm_from_admin`` 记来源，界面能看出这份配置是谁填的。

外加一组回归：**没有 ``sid`` 的旧令牌一律当无效**，否则删掉 ``sid`` 校验的那天，
「重置密码 / 吊销设备」就会退化成「等 30 分钟自然过期」。
"""

from __future__ import annotations

from datetime import timedelta

import jwt
from httpx import AsyncClient

from app.config import get_settings
from app.core.time import now_utc
from tests.conftest import TEST_PASSWORD

GOOD = {
    "base_url": "https://api.example.com/v1",
    "model": "qwen-vl-max",
    "api_key": "sk-1234567890abcdefghij",
}


async def _read(client: AsyncClient, headers) -> dict:
    r = await client.get("/api/auth/llm", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _save(client: AsyncClient, headers, **body) -> dict:
    r = await client.put("/api/auth/llm", json=body, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _meta(client: AsyncClient, headers) -> dict:
    r = await client.get("/api/meta", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- 1. 不默认注入 ----------


async def test_new_user_has_no_model_config(client: AsyncClient, auth_headers) -> None:
    body = await _read(client, auth_headers)
    assert body == {
        "base_url": None,
        "model": None,
        "api_key_masked": "",
        "from_admin": False,
        "configured": False,
    }
    meta = await _meta(client, auth_headers)
    assert meta["llm_configured"] is False
    assert meta["tagger_provider"] == "none"
    assert meta["tagger_available"] is False


async def test_saving_config_turns_ai_on(client: AsyncClient, auth_headers) -> None:
    saved = await _save(client, auth_headers, **GOOD)
    assert saved["configured"] is True
    assert saved["from_admin"] is False  # 自己填的
    assert saved["api_key_masked"] == "sk-123…ghij"

    meta = await _meta(client, auth_headers)
    assert meta["llm_configured"] is True
    assert meta["tagger_provider"] == "vlm"
    assert meta["tagger_available"] is True


async def test_key_is_never_returned_in_clear(client: AsyncClient, auth_headers) -> None:
    saved = await _save(client, auth_headers, **GOOD)
    assert GOOD["api_key"] not in str(saved)

    detail = (await _read(client, auth_headers))["api_key_masked"]
    assert GOOD["api_key"] not in detail


async def test_short_key_is_masked_wholesale(client: AsyncClient, auth_headers) -> None:
    """太短的密钥整体打码，别露出前 6 位就是半个密钥。"""
    saved = await _save(client, auth_headers, base_url="https://x.invalid/v1", model="m", api_key="sk-1234")
    assert saved["api_key_masked"] == "•" * 7
    assert "1" not in saved["api_key_masked"]


async def test_base_url_and_model_both_required(client: AsyncClient, auth_headers) -> None:
    """地址与模型名少一个都调不通，当作「没配」。"""
    only_url = await _save(client, auth_headers, base_url="https://api.example.com/v1")
    assert only_url["configured"] is False

    only_model = await _save(client, auth_headers, model="qwen-vl-max")
    assert only_model["configured"] is False
    assert (await _meta(client, auth_headers))["tagger_provider"] == "none"


# ---------- api_key 三态 ----------


async def test_omitting_key_keeps_it(client: AsyncClient, auth_headers) -> None:
    """只改模型名时不必把 Key 重新粘一遍。"""
    first = await _save(client, auth_headers, **GOOD)
    again = await _save(client, auth_headers, base_url=GOOD["base_url"], model="glm-4v-flash")
    assert again["model"] == "glm-4v-flash"
    assert again["api_key_masked"] == first["api_key_masked"]


async def test_empty_string_clears_key(client: AsyncClient, auth_headers) -> None:
    await _save(client, auth_headers, **GOOD)
    cleared = await _save(client, auth_headers, base_url=GOOD["base_url"], model=GOOD["model"], api_key="")
    assert cleared["api_key_masked"] == ""
    assert cleared["configured"] is True  # 地址与模型还在，只是没密钥


async def test_clearing_both_url_and_model_drops_the_key(client: AsyncClient, auth_headers) -> None:
    """整份作废时别留一个孤零零的 Key 在库里。"""
    await _save(client, auth_headers, **GOOD)
    wiped = await _save(client, auth_headers, base_url="", model="")
    assert wiped["configured"] is False
    assert wiped["api_key_masked"] == ""
    assert wiped["from_admin"] is False


async def test_delete_clears_everything(client: AsyncClient, auth_headers) -> None:
    await _save(client, auth_headers, **GOOD)
    r = await client.delete("/api/auth/llm", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["configured"] is False
    assert (await _read(client, auth_headers))["base_url"] is None


# ---------- 2. 用户间隔离 ----------


async def test_config_is_not_shared_between_users(client: AsyncClient, auth_headers, second_headers) -> None:
    await _save(client, auth_headers, **GOOD)

    assert (await _read(client, second_headers))["configured"] is False
    assert (await _meta(client, second_headers))["llm_configured"] is False
    assert (await _meta(client, second_headers))["tagger_provider"] == "none"

    # 反过来：给第二个用户配一套，不影响第一个
    await _save(client, second_headers, base_url="https://other.invalid/v1", model="other-model", api_key="sk-other-0000000000")
    assert (await _read(client, auth_headers))["base_url"] == GOOD["base_url"]
    assert (await _read(client, second_headers))["base_url"] == "https://other.invalid/v1"


async def test_tagger_is_built_from_the_owner_not_a_shared_cache(
    client: AsyncClient, auth_headers, second_headers, session_factory
) -> None:
    """打标 provider 按 owner 现造，不会把 A 的 Key 缓存给 B。

    回归用例：VLM 曾经是个全局单例，第一个用户的 Key 会被所有人生效。
    """
    from app.models.user import User
    from app.services.llm import tagger_for_user

    await _save(client, auth_headers, **GOOD)
    await _save(client, second_headers, base_url="https://other.invalid/v1", model="other-model", api_key="sk-other-0000000000")

    from sqlalchemy import select

    async with session_factory() as s:
        naonao = (await s.execute(select(User).where(User.username == "naonao"))).scalar_one()
        lily = (await s.execute(select(User).where(User.username == "lily"))).scalar_one()

    tagger_a = tagger_for_user(naonao)
    tagger_b = tagger_for_user(lily)
    assert tagger_a is not tagger_b
    assert tagger_a.name == tagger_b.name == "vlm"
    assert tagger_a.api_key != tagger_b.api_key
    assert tagger_b.base_url == "https://other.invalid/v1"


# ---------- 3. 管理员代设 ----------


async def test_admin_can_set_model_for_a_user(client: AsyncClient, admin_headers, make_user) -> None:
    headers, uid = await make_user(username="no_model")

    r = await client.put(
        f"/api/admin/users/{uid}/llm",
        json={"base_url": "https://admin.invalid/v1", "model": "admin-picked", "api_key": "sk-admin-0000000000"},
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()["llm"]
    assert body["configured"] is True and body["from_admin"] is True
    assert "sk-admin-0000000000" not in str(r.json())

    # 用户自己看到的是同一份，标记为「管理员代设」
    mine = await _read(client, headers)
    assert mine["base_url"] == "https://admin.invalid/v1"
    assert mine["from_admin"] is True
    assert (await _meta(client, headers))["tagger_provider"] == "vlm"


async def test_user_taking_over_clears_the_admin_flag(client: AsyncClient, admin_headers, make_user) -> None:
    headers, uid = await make_user(username="takeover")
    await client.put(
        f"/api/admin/users/{uid}/llm",
        json={"base_url": "https://admin.invalid/v1", "model": "admin-picked", "api_key": "sk-admin-0000000000"},
        headers=admin_headers,
    )
    # 改之前先留一份代设的脱敏串
    before = await _read(client, headers)

    after = await _save(client, headers, base_url="https://mine.invalid/v1", model="my-model")
    assert after["from_admin"] is False
    # 不传 api_key 时保留管理员代设的那把 Key
    assert after["api_key_masked"] == before["api_key_masked"]


async def test_admin_can_create_user_with_model(client: AsyncClient, admin_headers) -> None:
    r = await client.post(
        "/api/admin/users",
        json={
            "username": "preloaded",
            "password": TEST_PASSWORD,
            "llm_base_url": "https://admin.invalid/v1",
            "llm_model": "admin-picked",
            "llm_api_key": "sk-admin-0000000000",
        },
        headers=admin_headers,
    )
    assert r.status_code == 201, r.text
    assert r.json()["llm"]["configured"] is True and r.json()["llm"]["from_admin"] is True


async def test_admin_can_clear_user_model(client: AsyncClient, admin_headers, make_user) -> None:
    headers, uid = await make_user(username="clear_me")
    await _save(client, headers, **GOOD)

    r = await client.delete(f"/api/admin/users/{uid}/llm", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert r.json()["llm"]["configured"] is False
    assert (await _read(client, headers))["configured"] is False


async def test_non_admin_cannot_set_others_model(client: AsyncClient, auth_headers, user) -> None:
    r = await client.put(
        f"/api/admin/users/{user.id}/llm",
        json={"base_url": "https://x.invalid/v1", "model": "m"},
        headers=auth_headers,
    )
    assert r.status_code == 403


# ---------- 访问令牌必须绑会话 ----------


async def test_legacy_token_without_sid_is_rejected(client: AsyncClient, user) -> None:
    """批次 6 之前签发的令牌（payload 里没有 ``sid``）一律当无效。

    留着它就是留了一条「旧令牌绕过会话吊销」的后门 —— 一次性重新登录更划算。
    """
    s = get_settings()
    now = now_utc()
    legacy = jwt.encode(
        {
            "sub": str(user.id),
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(minutes=30)).timestamp()),
            "typ": "access",
        },
        s.jwt_secret,
        algorithm=s.jwt_algorithm,
    )
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {legacy}"})
    assert r.status_code == 401
    assert "重新登录" in r.json()["detail"]


async def test_token_from_a_revoked_session_dies_at_once(client: AsyncClient, user) -> None:
    """吊销设备后，那个访问令牌**立刻**失效，不用等它自己过期。"""
    login = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    body = login.json()
    headers = {"Authorization": f"Bearer {body['access_token']}"}
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 200

    killed = await client.delete(f"/api/auth/sessions/{body['session_id']}", headers=headers)
    assert killed.status_code == 200
    assert (await client.get("/api/auth/me", headers=headers)).status_code == 401


async def test_token_from_another_users_session_is_rejected(client: AsyncClient, user, session_factory) -> None:
    """拿 A 的令牌配上 B 的 sid 也不行（sid 与 sub 必须同属一个人）。"""
    from sqlalchemy import select

    from app.core.security import create_access_token, hash_password
    from app.models.user import User
    from app.services.tags import seed_default_categories

    async with session_factory() as s:
        other = User(username="crossed", password_hash=hash_password(TEST_PASSWORD))
        s.add(other)
        await s.flush()
        await seed_default_categories(s, other.id)
        await s.commit()

    login = await client.post(
        "/api/auth/login", json={"username": "crossed", "password": TEST_PASSWORD}
    )
    other_sid = login.json()["session_id"]
    assert other_sid > 0

    forged, _ = create_access_token(user.id, other_sid)
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401

    # 反过来：B 的 uid 配上 A 的 sid 同样不行
    async with session_factory() as s:
        other_id = (await s.execute(select(User.id).where(User.username == "crossed"))).scalar_one()
    mine_sid = (
        await client.post("/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD})
    ).json()["session_id"]
    forged2, _ = create_access_token(other_id, mine_sid)
    r2 = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged2}"})
    assert r2.status_code == 401
