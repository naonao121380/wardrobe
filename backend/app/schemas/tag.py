"""标签体系的请求 / 响应模型。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScopeLiteral = Literal["garment", "outfit"]


class TagValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    value: str
    sort_order: int = 0


class TagCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: str
    name: str
    sort_order: int = 0
    values: list[TagValueOut] = Field(default_factory=list)


class TagCategoryCreate(BaseModel):
    scope: ScopeLiteral
    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0


class TagCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None


class TagValueCreate(BaseModel):
    value: str = Field(min_length=1, max_length=64)
    sort_order: int = 0


class TagValueUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None
