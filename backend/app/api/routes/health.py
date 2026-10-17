"""健康检查与运行信息。

``/healthz`` ``/readyz`` **无需鉴权**，供 compose healthcheck / 反代探活使用；
``/meta`` 要登录，给前端「关于」面板用（版本号、AI 能力是否就绪）。
"""

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.schemas.auth import MetaOut
from app.services import system as system_service
from app.services.llm import tagger_available, tagger_for_user

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


@router.get("/meta", response_model=MetaOut)
async def meta(user: CurrentUser, db: DbSession) -> MetaOut:
    """前端「关于」要的信息。

    打标能力是**按当前用户**报的：模型配置存在各自的用户行上（地址 / Key / 模型名
    由用户自己填，管理员可代填），别人配了不代表我也能用。注册模式从库里读，
    管理员在界面上改完立刻生效（不是环境变量那种要重启的）。
    """
    s = get_settings()
    tagger = tagger_for_user(user)
    settings = await system_service.get_app_settings(db)
    return MetaOut(
        app_name=s.app_name,
        version=s.version,
        registration_mode=settings.registration_mode,
        matting_provider=s.ai_matting_provider or "none",
        tagger_provider=tagger.name,
        tagger_available=tagger_available(tagger),
        llm_configured=user.llm_configured,
    )
