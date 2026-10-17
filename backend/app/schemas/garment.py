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


class AiSuggestionTagOut(BaseModel):
    """AI 候选标签，带上类别信息便于前端分组展示。"""

    value_id: int
    value: str
    category_id: int
    category_name: str


class AiSuggestionOut(BaseModel):
    """Worker 产出的候选结果。复核界面照着它渲染，用户可以全盘采纳或改完再确认。"""

    name: str | None = None
    tag_value_ids: list[int] = Field(default_factory=list)
    tags: list[AiSuggestionTagOut] = Field(default_factory=list)
    #: 出结果的 provider：rembg+clip / vlm / manual；none 表示没跑 AI
    provider: str = "none"
    confidence: float | None = None
    error: str | None = None


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
    #: 批次 3 流水线状态（pending / processing / needs_review / ready / failed）
    status: str = "ready"
    ai_suggestion: AiSuggestionOut | None = None


class GarmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    notes: str | None = None
    tag_value_ids: list[int] = Field(default_factory=list)


class GarmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    notes: str | None = None
    tag_value_ids: list[int] | None = None


class GarmentReview(BaseModel):
    """复核确认：字段省略表示「沿用 AI 建议」，传了值表示「用户改过」。"""

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


class MatteBoxIn(BaseModel):
    """框选局部重抠的请求体。

    坐标是**原图像素**；前端先按显示比例换算好再发过来，避免缩放错位。
    """

    x: float = 0
    y: float = 0
    width: float = 0
    height: float = 0
    #: remove = 去掉框内的背景残留；add = 补回框内被误删的部分
    mode: str = "remove"


class GarmentBatchProcessIn(BaseModel):
    """批量入队的请求体：给一串衣物 id。

    一件一件调 ``POST /{id}/process`` 也不是不行，但那意味着 N 次往返 —— 选个
    二三十件就要点一下卡半天。这里一次收下，服务端统一排队。
    """

    garment_ids: list[int] = Field(default_factory=list, max_length=200)


class BatchSkipOut(BaseModel):
    """批量入队里被跳过的那一件，以及跳过的理由（前端要原样告诉用户）。"""

    id: int
    reason: str


class GarmentBatchProcessOut(BaseModel):
    enqueued: list[int] = Field(default_factory=list)
    skipped: list[BatchSkipOut] = Field(default_factory=list)


class GarmentBatchIdsIn(BaseModel):
    """批量删除的请求体。

    ``cascade_outfits``：把这些衣物**连同引用了它们的搭配**一起删掉。
    默认 ``False`` —— 搭配只是一层引用，删衣物后那层会退化成「素材已删除」，
    搭配本身还在（更保守）。前端把选择权交给用户。
    """

    garment_ids: list[int] = Field(default_factory=list, max_length=200)
    cascade_outfits: bool = False


class GarmentBatchTagsIn(BaseModel):
    """批量改标签的请求体。

    ``mode``：
    - ``add``（默认）追加，已有的不动 —— 批量打标用这个
    - ``remove`` 移除指定标签
    - ``replace`` 覆盖成这一份
    """

    garment_ids: list[int] = Field(default_factory=list, max_length=200)
    tag_value_ids: list[int] = Field(default_factory=list)
    mode: str = "add"


class AffectedOutfitOut(BaseModel):
    """删衣物时会受影响的搭配（用了几层）。"""

    id: int
    name: str
    layers: int


class GarmentDeleteOut(BaseModel):
    """删单件衣物的结果。

    ``orphaned_layers`` = 保留搭配、但把引用这件的图层变成「素材已删除」的层数；
    ``deleted_outfits`` = 连搭配一起删掉的套数（``cascade_outfits=true`` 时才有）。
    """

    deleted_images: int = 0
    affected_outfits: list[AffectedOutfitOut] = Field(default_factory=list)
    orphaned_layers: int = 0
    deleted_outfits: int = 0


class GarmentBatchDeleteOut(BaseModel):
    deleted: list[int] = Field(default_factory=list)
    deleted_images: int = 0
    skipped: list[BatchSkipOut] = Field(default_factory=list)
    affected_outfits: list[AffectedOutfitOut] = Field(default_factory=list)
    orphaned_layers: int = 0
    deleted_outfits: int = 0


class GarmentBatchTagsOut(BaseModel):
    updated: list[int] = Field(default_factory=list)
    skipped: list[BatchSkipOut] = Field(default_factory=list)


class BatchFileSkipOut(BaseModel):
    """批量上传里没通过的那一个文件 —— 文件还没有 id，只能报文件名。"""

    file: str
    reason: str


class GarmentBatchUploadOut(BaseModel):
    created: list[GarmentOut] = Field(default_factory=list)
    skipped: list[BatchFileSkipOut] = Field(default_factory=list)


class QueueStatusOut(BaseModel):
    """流水线里各状态的数量。

    前端拿它做「这批活儿干完没」的判断：``pending + processing == 0`` 就是跑完了。
    只查计数不查明细，轮询起来几乎不要钱。
    """

    pending: int = 0
    processing: int = 0
    needs_review: int = 0
    ready: int = 0
    failed: int = 0
