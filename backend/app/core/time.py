"""时间工具：抹平 SQLite / PostgreSQL 的时区差异。"""

from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_naive_utc() -> datetime:
    """UTC 的「裸」时间戳，专门给**没有时区**的列用（如 ``garments.processed_at``）。

    ★ 别拿 ``now_utc()`` 往 ``TIMESTAMP WITHOUT TIME ZONE`` 里写：PostgreSQL 的
    asyncpg 会直接抛 ``DataError: can't subtract offset-naive and offset-aware
    datetimes``。更坑的是 SQLite **不报错**（它本来就不存时区），所以本地测试全绿、
    线上才炸 —— 2026-10-17 的事故就是它：超时后写 ``processed_at`` 失败，
    异常从 ``_fail`` 里冒出来，这件衣物永远留在 processing，40 分钟后回收重试，
    如此无限循环，前端看到的就是「一直处理中」。
    """
    return datetime.now(UTC).replace(tzinfo=None)


def as_aware(dt: datetime) -> datetime:
    """SQLite 读回来的 datetime 是 naive 的，统一补成 UTC。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
