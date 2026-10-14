"""测试夹具。

测试跑在内存 SQLite 上（aiosqlite + StaticPool），生产跑 PostgreSQL。
所有跨库 datetime 比较都经 ``app.core.time.as_aware`` 归一化，见 models/base.py 的说明。
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.core.security import hash_password
from app.db import Base, get_session
from app.main import app
from app.models.user import User

TEST_PASSWORD = "secret12345"


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
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
        await s.commit()
        await s.refresh(u)
        return u


@pytest_asyncio.fixture
async def auth_headers(client: AsyncClient, user: User) -> dict[str, str]:
    r = await client.post("/api/auth/login", json={"username": "naonao", "password": TEST_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
def allow_registration(monkeypatch):
    monkeypatch.setenv("WARDROBE_ALLOW_REGISTRATION", "true")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
