"""测试夹具。

测试跑在**临时文件** SQLite 上（每会话一条连接，并发用例才不失真），
生产跑 PostgreSQL。所有跨库 datetime 比较都经 ``app.core.time.as_aware``
归一化，见 models/base.py 的说明。
"""

import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path

# ⚠️ 必须在导入 app.* 之前设置：app.db 在 import 时就会读取配置。
_MEDIA_TMP = Path(tempfile.mkdtemp(prefix="wardrobe-test-media-"))
os.environ.setdefault("WARDROBE_MEDIA_ROOT", str(_MEDIA_TMP))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import event  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import Base, attach_sqlite_fk_pragma, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.tags import seed_default_categories  # noqa: E402

TEST_PASSWORD = "secret12345"


@pytest_asyncio.fixture
async def engine(tmp_path):
    """每个用例一个**临时文件**库（而不是内存库）。

    内存库必须配 ``StaticPool``，也就是「所有会话共用同一条连接」—— 于是并发用例的
    两个请求落在同一个事务里：落败方回滚时会把胜方**还没提交**的 INSERT 一起抹掉，
    胜方随后报外键错。「同一枚邀请码不能同时注册两个人」这类用例因此失真，
    只有换成分文件库（每会话一条连接）才和 PostgreSQL 的并发语义一致。
    """
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'test.db'}",
        poolclass=NullPool,
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    attach_sqlite_fk_pragma(eng)

    # 吐盘是这里最贵的一步（每个用例要写一百多个预置标签取值）。回滚日志放内存、
    # 关掉 fsync，速度回到内存库的水平；**锁协议不变**，所以并发用例的语义照旧。
    @event.listens_for(eng.sync_engine, "connect")
    def _fast(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=MEMORY")
        cursor.execute("PRAGMA synchronous=OFF")
        cursor.close()

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()

    # 顺手删掉本用例的库文件。不删的话，pytest 会在 session 收尾时一次性清掉
    # 几百个 .db —— 宿主环境的「批量删除保护」（>50 个文件）会拦下这一步并抛
    # SystemExit，表现是「进度跑到 100%，但没有 passed 总结行」。
    # 这里每次只删 1 个文件，不会触发；删不掉（Windows 句柄未释放）就留着。
    #
    # ⚠️ 不要为了这个去加 `--basetemp`：给定 basetemp 时 pytest 的 tmp_path 目录名
    # 不带数字后缀，**同名用例会复用同一个目录**（函数名前 30 字符相同即撞），
    # 上一个用例的库文件还在，create_all 之后播种就撞 UNIQUE —— 实测 407 个用例报错。
    for name in ("test.db", "test.db-wal", "test.db-shm"):
        with suppress(OSError):
            (tmp_path / name).unlink()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def user(session_factory) -> User:
    async with session_factory() as s:
        u = User(
            username="naonao",
            password_hash=hash_password(TEST_PASSWORD),
            display_name="闹闹",
        )
        s.add(u)
        await s.flush()
        # 生产环境在用户创建时播种默认标签类别，测试保持一致
        await seed_default_categories(s, u.id)
        await s.commit()
        await s.refresh(u)
        return u


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, user: User) -> dict[str, str]:
    r = await client.post("/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def second_headers(client: AsyncClient, session_factory) -> dict[str, str]:
    """第二个用户，用于验证跨用户隔离。"""
    async with session_factory() as s:
        u = User(username="lily", password_hash=hash_password(TEST_PASSWORD), display_name="Lily")
        s.add(u)
        await s.flush()
        await seed_default_categories(s, u.id)
        await s.commit()
    r = await client.post("/api/auth/login", json={"username": "lily", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def admin(session_factory) -> User:
    """一个管理员。注意测试库里默认只有这一个管理员（最后一个管理员的保护靠它验证）。"""
    async with session_factory() as s:
        u = User(
            username="boss",
            password_hash=hash_password(TEST_PASSWORD),
            display_name="管理员",
            is_admin=True,
        )
        s.add(u)
        await s.flush()
        await seed_default_categories(s, u.id)
        await s.commit()
        await s.refresh(u)
        return u


@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, admin: User) -> dict[str, str]:
    r = await client.post("/api/auth/login", json={"username": "boss", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest_asyncio.fixture
async def make_user(client: AsyncClient, session_factory):
    """按需造普通用户（可带配额 / 停用标记），返回 ``(登录头, user_id)``。

    配额用例要的是「一个配额恰好卡住的新用户」，走管理员建号接口会把用例和
    管理接口耦在一起，直接在库里造更干净。
    """

    async def _make(username: str = "tester", **fields):
        async with session_factory() as s:
            u = User(username=username, password_hash=hash_password(TEST_PASSWORD), **fields)
            s.add(u)
            await s.flush()
            await seed_default_categories(s, u.id)
            await s.commit()
            await s.refresh(u)
            uid = u.id
        r = await client.post(
            "/api/auth/login", json={"username": username, "password": TEST_PASSWORD}
        )
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}, uid

    return _make


@pytest.fixture
def allow_registration(monkeypatch):
    monkeypatch.setenv("WARDROBE_ALLOW_REGISTRATION", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def media_root() -> Path:
    return _MEDIA_TMP
