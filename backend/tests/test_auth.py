"""鉴权链路测试：登录、刷新轮换、吊销、设备管理、注册开关。"""

from httpx import AsyncClient

from tests.conftest import TEST_PASSWORD


async def test_healthz(client: AsyncClient) -> None:
    r = await client.get("/api/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True


async def test_readyz(client: AsyncClient) -> None:
    r = await client.get("/api/readyz")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "database": True}


async def test_login_success(client: AsyncClient, user) -> None:
    r = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["user"]["username"] == "naonao"
    assert body["session_id"] > 0


async def test_login_wrong_password(client: AsyncClient, user) -> None:
    r = await client.post("/api/auth/login", json={"username": "naonao", "password": "wrong-pass"})
    assert r.status_code == 401


async def test_login_unknown_user(client: AsyncClient, user) -> None:
    r = await client.post("/api/auth/login", json={"username": "ghost", "password": TEST_PASSWORD})
    assert r.status_code == 401
    # 与密码错误返回同样的文案，避免用户名枚举
    assert r.json()["detail"] == "用户名或密码错误"


async def test_me_requires_token(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_me_rejects_garbage_token(client: AsyncClient) -> None:
    r = await client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert r.status_code == 401


async def test_me_with_token(client: AsyncClient, auth_headers) -> None:
    r = await client.get("/api/auth/me", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["display_name"] == "闹闹"


async def test_refresh_rotates_and_invalidates_old(client: AsyncClient, user) -> None:
    login = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    old_refresh = login.json()["refresh_token"]

    first = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert first.status_code == 200, first.text
    new_refresh = first.json()["refresh_token"]
    assert new_refresh != old_refresh

    # 旧刷新令牌已被吊销，重放必须失败
    replay = await client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401

    # 新令牌可用
    again = await client.post("/api/auth/refresh", json={"refresh_token": new_refresh})
    assert again.status_code == 200


async def test_logout_revokes_refresh_token(client: AsyncClient, user) -> None:
    login = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    refresh = login.json()["refresh_token"]

    out = await client.post("/api/auth/logout", json={"refresh_token": refresh})
    assert out.status_code == 200
    assert out.json() == {"ok": True}

    after = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert after.status_code == 401


async def test_sessions_listing_and_single_revoke(client: AsyncClient, user) -> None:
    first = await client.post(
        "/api/auth/login",
        json={"username": "naonao", "password": TEST_PASSWORD, "device_label": "书房台式机"},
    )
    await client.post(
        "/api/auth/login",
        json={"username": "naonao", "password": TEST_PASSWORD, "device_label": "手机"},
    )
    headers = {"Authorization": f"Bearer {first.json()['access_token']}"}

    listed = await client.get("/api/auth/sessions", headers=headers)
    assert listed.status_code == 200
    sessions = listed.json()
    assert len(sessions) == 2
    labels = {s["device_label"] for s in sessions}
    assert labels == {"书房台式机", "手机"}

    victim = next(s for s in sessions if s["device_label"] == "手机")
    killed = await client.delete(f"/api/auth/sessions/{victim['id']}", headers=headers)
    assert killed.status_code == 200

    remaining = (await client.get("/api/auth/sessions", headers=headers)).json()
    assert len(remaining) == 1
    assert remaining[0]["device_label"] == "书房台式机"


async def test_revoke_other_users_session_is_404(client: AsyncClient, session_factory, user) -> None:
    from app.core.security import hash_password
    from app.models.user import User

    async with session_factory() as s:
        other = User(username="other", password_hash=hash_password(TEST_PASSWORD))
        s.add(other)
        await s.commit()
        await s.refresh(other)
        other_id = other.id

    login = await client.post(
        "/api/auth/login", json={"username": "other", "password": TEST_PASSWORD}
    )
    other_session_id = login.json()["session_id"]
    assert other_session_id > 0
    assert other_id > 0

    mine = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {mine.json()['access_token']}"}

    r = await client.delete(f"/api/auth/sessions/{other_session_id}", headers=headers)
    assert r.status_code == 404


async def test_logout_all(client: AsyncClient, user) -> None:
    a = await client.post("/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD})
    b = await client.post("/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD})
    headers = {"Authorization": f"Bearer {a.json()['access_token']}"}

    r = await client.post("/api/auth/logout-all", headers=headers)
    assert r.status_code == 200
    assert r.json()["revoked"] == 2

    assert (
        await client.post("/api/auth/refresh", json={"refresh_token": a.json()["refresh_token"]})
    ).status_code == 401
    assert (
        await client.post("/api/auth/refresh", json={"refresh_token": b.json()["refresh_token"]})
    ).status_code == 401


async def test_register_disabled_by_default(client: AsyncClient) -> None:
    r = await client.post(
        "/api/auth/register", json={"username": "newbie", "password": "password123"}
    )
    assert r.status_code == 403


async def test_register_when_enabled(client: AsyncClient, allow_registration) -> None:
    r = await client.post(
        "/api/auth/register",
        json={"username": "newbie", "password": "password123", "display_name": "新人"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["username"] == "newbie"


async def test_register_rejects_weak_password(client: AsyncClient, allow_registration) -> None:
    r = await client.post("/api/auth/register", json={"username": "newbie", "password": "short"})
    assert r.status_code == 422


async def test_register_rejects_duplicate(client: AsyncClient, user, allow_registration) -> None:
    r = await client.post(
        "/api/auth/register", json={"username": "naonao", "password": "password123"}
    )
    assert r.status_code == 409


async def test_change_password_revokes_all_sessions(client: AsyncClient, user) -> None:
    login = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    bad = await client.post(
        "/api/auth/password",
        json={"current_password": "nope", "new_password": "brandnew123"},
        headers=headers,
    )
    assert bad.status_code == 400

    ok = await client.post(
        "/api/auth/password",
        json={"current_password": TEST_PASSWORD, "new_password": "brandnew123"},
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["revoked"] == 1

    assert (
        await client.post("/api/auth/refresh", json={"refresh_token": login.json()["refresh_token"]})
    ).status_code == 401

    relogin = await client.post(
        "/api/auth/login", json={"username": "naonao", "password": "brandnew123"}
    )
    assert relogin.status_code == 200
