"""ORM 模型集中导出，保证 Alembic 能发现全部表。"""

from app.models.base import TimestampMixin
from app.models.garment import Garment, GarmentImage, GarmentTag
from app.models.outfit import Outfit, OutfitImage, OutfitItem, OutfitTag
from app.models.system import REGISTRATION_MODES, AppSetting, InviteCode
from app.models.tag import TagCategory, TagScope, TagValue
from app.models.user import User, UserSession

__all__ = [
    "AppSetting",
    "Garment",
    "GarmentImage",
    "GarmentTag",
    "InviteCode",
    "Outfit",
    "OutfitImage",
    "OutfitItem",
    "OutfitTag",
    "REGISTRATION_MODES",
    "TagCategory",
    "TagScope",
    "TagValue",
    "TimestampMixin",
    "User",
    "UserSession",
]
