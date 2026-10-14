"""健康检查。无需鉴权，供 compose healthcheck / 反代探活使用。"""

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.core.deps import DbSession

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, object]:
    s = get_settings()
    return {"ok": True, "app": s.app_name, "version": s.version}


@router.get("/readyz")
async def readyz(db: DbSession) -> dict[str, object]:
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    return {"ok": db_ok, "database": db_ok}
