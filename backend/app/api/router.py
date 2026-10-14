"""API 路由汇总。"""

from fastapi import APIRouter

from app.api.routes import auth, garments, health, media, tags

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(tags.router)
api_router.include_router(garments.router)
api_router.include_router(media.router)
