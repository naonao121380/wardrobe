"""搭配的请求 / 响应模型（批次 4）。

图层的坐标全部是**归一化**的（0~1 相对画布），服务端不做像素换算 —— 详见
``app/models/outfit.py``。取值范围放宽到画布外一点点（x / y 允许 -1~2），
因为「半个袖子摆到画布外」是很常见的摆法，硬卡在 0~1 反而碍事。
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

#: 一套搭配最多多少层：再多画布上已经看不清了，也别让请求体无限膨胀
MAX_ITEMS = 60

X_RANGE = (-1.0, 2.0)
W_RATIO_RANGE = (0.02, 4.0)
ROTATION_RANGE = (-1800.0, 1800.0)
OPACITY_RANGE = (0.05, 1.0)


class OutfitItemIn(BaseModel):
    """画布上的一层。前端保存时把整个图层数组一起提交，服务端整份替换。"""

    garment_id: int | None = None
    garment_image_id: int | None = None
    name_snapshot: str | None = Field(default=None, max_length=128)

    x: float = Field(default=0.5, ge=X_RANGE[0], le=X_RANGE[1])
    y: float = Field(default=0.5, ge=X_RANGE[0], le=X_RANGE[1])
    w_ratio: float = Field(default=0.5, ge=W_RATIO_RANGE[0], le=W_RATIO_RANGE[1])
    rotation: float = Field(default=0.0, ge=ROTATION_RANGE[0], le=ROTATION_RANGE[1])
    z: int = 0
    flip_h: bool = False
    opacity: float = Field(default=1.0, ge=OPACITY_RANGE[0], le=OPACITY_RANGE[1])


class OutfitItemOut(BaseModel):
    """回显的一层，额外带上能直接塞进 ``<img src>`` 的签名 URL。"""

    id: int
    garment_id: int | None = None
    garment_image_id: int | None = None
    name_snapshot: str | None = None

    x: float
    y: float
    w_ratio: float
    rotation: float
    z: int
    flip_h: bool
    opacity: float

    #: 素材图（抠图优先）的签名 URL；素材被删了就是 None
    image_url: str | None = None
    #: 素材图的原始像素尺寸，前端靠它算宽高比、导出时算绘制高度
    width: int = 0
    height: int = 0
    #: True = 这层引用的衣物 / 图片已经不在了（界面提示「素材已删除」）
    missing: bool = False
    #: 衣物当前的名字（衣物还在时给前端显示；删了就退回 name_snapshot）
    garment_name: str | None = None


class OutfitImageOut(BaseModel):
    id: int
    #: result = 编辑器合成的搭配图（只留最新一张）；worn = 上身图（可选、可多张）。
    #: 老数据里可能还是早期别名 ``upload``，前端按上身图处理。
    kind: str
    width: int
    height: int
    sort_order: int
    thumb_small_url: str
    thumb_medium_url: str
    original_url: str


class OutfitTagOut(BaseModel):
    value_id: int
    value: str
    category_id: int
    category_name: str


class OutfitOut(BaseModel):
    id: int
    name: str
    notes: str | None = None
    background: str = "#ffffff"
    created_at: datetime
    updated_at: datetime
    cover_url: str | None = None
    images: list[OutfitImageOut] = Field(default_factory=list)
    items: list[OutfitItemOut] = Field(default_factory=list)
    tag_value_ids: list[int] = Field(default_factory=list)
    tags: list[OutfitTagOut] = Field(default_factory=list)


def _not_blank(value: str) -> str:
    """光空格的名字算没填：``min_length`` 挡不住 ``"  "``。"""
    if not value.strip():
        raise ValueError("名称不能为空")
    return value


class OutfitCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    notes: str | None = None
    _check_name = field_validator("name")(_not_blank)
    background: str | None = Field(default=None, max_length=16)
    tag_value_ids: list[int] = Field(default_factory=list)
    items: list[OutfitItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)


class OutfitUpdate(BaseModel):
    """``items`` 传了就**整份替换**图层；不传表示本次不动画布。"""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = None
    background: str | None = Field(default=None, max_length=16)
    tag_value_ids: list[int] | None = None
    items: list[OutfitItemIn] | None = Field(default=None, max_length=MAX_ITEMS)
    _check_name = field_validator("name")(_not_blank)


class OutfitListOut(BaseModel):
    items: list[OutfitOut]
    total: int
    page: int
    page_size: int


class OutfitRefOut(BaseModel):
    """「这件衣物被哪些搭配用过」的一条记录。

    批次 5 删衣物时拿它提示用户；这里先把接口备好，避免那时候再动模型。
    """

    id: int
    name: str
    layers: int = 0


class OutfitSuggestionOut(BaseModel):
    """AI 给的搭配名 + 标签。**只是建议**，要不要用由用户在界面上点。

    ``error`` 是「这次没跑出来」的原因（没配模型 / 调用失败 / 没成图）；
    ``hint`` 是「跑出来了但值得一提」的提醒（比如搭配侧一个标签都没有）。
    """

    name: str | None = None
    tag_value_ids: list[int] = Field(default_factory=list)
    tags: list[OutfitTagOut] = Field(default_factory=list)
    confidence: float | None = None
    error: str | None = None
    hint: str | None = None


class OutfitAiStatusOut(BaseModel):
    """AI 能不能用。前端据此决定「AI 推荐」按钮是亮着还是置灰。"""

    provider: str
    available: bool
