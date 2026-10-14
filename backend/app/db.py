"""数据库引擎与会话。"""

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的基类。"""


def attach_sqlite_fk_pragma(async_engine: AsyncEngine) -> None:
    """SQLite 默认**不强制外键**，必须逐连接打开 PRAGMA。

    否则 ON DELETE CASCADE 形同虚设 —— 在 SQLite 上测试通过、到 PostgreSQL 上
    行为却不同（或反过来留下孤儿行）。
    """

    @event.listens_for(async_engine.sync_engine, "connect")
    def _enable_fk(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


_settings = get_settings()

engine = create_async_engine(
    _settings.database_url,
    echo=_settings.debug,
    pool_pre_ping=True,
    future=True,
)

if _settings.database_url.startswith("sqlite"):
    attach_sqlite_fk_pragma(engine)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：每请求一个会话。"""
    async with SessionLocal() as session:
        yield session
