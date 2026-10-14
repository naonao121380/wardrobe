"""时间工具：抹平 SQLite / PostgreSQL 的时区差异。"""

from datetime import UTC, datetime


def now_utc() -> datetime:
    return datetime.now(UTC)


def as_aware(dt: datetime) -> datetime:
    """SQLite 读回来的 datetime 是 naive 的，统一补成 UTC。"""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)
