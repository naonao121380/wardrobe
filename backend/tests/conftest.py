"""测试夹具。

测试跑在内存 SQLite 上（aiosqlite + StaticPool），生产跑 PostgreSQL。
所有跨库 datetime 比较都经 ``app.core.time.as_aware`` 归一化，见 models/base.py 的说明。
"""

import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

# ⚠️ 必须在导入 app.* 之前设置：app.db 在 import 时就会读取配置。
_MEDIA_TMP = Path(tempfile.mkdtemp(prefix="wardrobe-test-media-"))
os.environ.setdefault("WARDROBE_MEDIA_ROOT", str(_MEDIA_TMP))

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import Base, attach_sqlite_fk_pragma, get_session  # noqa: E402
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.tags import seed_default_categories  # noqa: E402

TEST_PASSWORD = "secret12345"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    attach_sqlite_fk_pragma(eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


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


@pytest.fixture
def allow_registration(monkeypatch):
    monkeypatch.setenv("WARDROBE_ALLOW_REGISTRATION", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def media_root() -> Path:
    return _MEDIA_TMP
