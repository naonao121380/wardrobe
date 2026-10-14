"""衣物的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class GarmentImageOut(BaseModel):
    id: int
    kind: str
    width: int
    height: int
    sort_order: int
    thumb_small_url: str
    thumb_medium_url: str
    original_url: str


class GarmentTagOut(BaseModel):
    """带类别信息的标签，便于前端分组显示。"""

    value_id: int
    value: str
    category_id: int
    category_name: str


class GarmentOut(BaseModel):
    id: int
    name: str
    notes: str | None = None
    created_at: datetime
    updated_at: datetime
    cover_url: str | None = None
    images: list[GarmentImageOut] = Field(default_factory=list)
    tag_value_ids: list[int] = Field(default_factory=list)
    tags: list[GarmentTagOut] = Field(default_factory=list)


class GarmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    notes: str | None = None
    tag_value_ids: list[int] = Field(default_factory=list)


class GarmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = None
    tag_value_ids: list[int] | None = None


class GarmentListOut(BaseModel):
    items: list[GarmentOut]
    total: int
    page: int
    page_size: int


class ImageOrderUpdate(BaseModel):
    sort_order: int = 0
