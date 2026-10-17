"""API 路由汇总。"""

from fastapi import APIRouter

from app.api.routes import admin, auth, garments, health, media, outfits, tags

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router)
api_router.include_router(admin.router)
api_router.include_router(tags.router)
api_router.include_router(garments.router)
api_router.include_router(outfits.router)
api_router.include_router(media.router)
