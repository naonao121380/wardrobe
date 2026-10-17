"""标签体系的请求 / 响应模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ScopeLiteral = Literal["garment", "outfit"]


class TagValueOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    value: str
    sort_order: int = 0
    #: 父取值 id。只有子类别（如「小类」）的取值才有；NULL = 未归类。
    parent_value_id: int | None = None
    #: 当前有多少件衣物在用它。0 = 没有命中，删除时前端不必再确认。
    usage_count: int = 0


class TagCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scope: str
    name: str
    sort_order: int = 0
    #: 父类别 id。NULL = 顶层类别（大类 / 颜色 / 材质）。
    parent_category_id: int | None = None
    values: list[TagValueOut] = Field(default_factory=list)
    #: 本类别下所有取值被使用的总次数；0 表示整个类别都没被任何衣物引用。
    usage_count: int = 0


class TagCategoryCreate(BaseModel):
    scope: ScopeLiteral
    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0
    parent_category_id: int | None = None


class TagCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None
    #: 显式传 null 可以把子类别提升为顶层类别
    parent_category_id: int | None = None


class TagValueCreate(BaseModel):
    value: str = Field(min_length=1, max_length=64)
    sort_order: int = 0
    parent_value_id: int | None = None


class TagValueUpdate(BaseModel):
    value: str | None = Field(default=None, min_length=1, max_length=64)
    sort_order: int | None = None
    parent_value_id: int | None = None


# ---------- 标签配置的导入 / 导出 ----------
#
# 配置文件里**只用名字表达层级，不带任何 id**：id 只在本库成立，换个账号、换台机器、
# 甚至按导出→删除→导入重来一遍，id 全都会变。名字是唯一能跨实例落地的锚点。
#
# 导入语义（见路由）：**按名字去重、只补不删**。导入一份配置不该把本地已经攒的
# 标签改掉或删掉 —— 那是两回事。要覆盖，先自己删。


class TagValueConfig(BaseModel):
    """配置里的一个取值。"""

    value: str = Field(min_length=1, max_length=64)
    sort_order: int = 0
    #: 父**取值**的名字（必须是父类别下已有的取值文本），None = 未归类。
    parent: str | None = None


class TagCategoryConfig(BaseModel):
    """配置里的一个类别。"""

    name: str = Field(min_length=1, max_length=64)
    sort_order: int = 0
    #: 父**类别**的名字，None = 顶层类别。只支持两层。
    parent: str | None = None
    values: list[TagValueConfig] = Field(default_factory=list)


class TagConfigOut(BaseModel):
    """导出文件。`kind` / `version` 让导入端能在解析前就认出「这是不是我的文件」。"""

    kind: Literal["wardrobe.tag-config"] = "wardrobe.tag-config"
    version: int = 1
    scope: ScopeLiteral
    exported_at: datetime
    categories: list[TagCategoryConfig] = Field(default_factory=list)


class TagConfigIn(BaseModel):
    """导入文件。

    刻意宽松：`kind` / `version` / `scope` 都允许缺省，方便手写一份最小的
    `{"categories":[{"name":"版型","values":[{"value":"宽松"}]}]}` 也能导进来。
    """

    kind: str | None = None
    version: int | None = None
    scope: ScopeLiteral | None = None
    categories: list[TagCategoryConfig] = Field(default_factory=list)


class TagConfigImportResult(BaseModel):
    """导入了什么、跳过了什么、哪些没按原样落下来。"""

    scope: ScopeLiteral
    categories_created: int = 0
    categories_skipped: int = 0
    values_created: int = 0
    values_skipped: int = 0
    #: 非致命问题：父级认不出来、超过两层被降级、文件里写了但本地已存在…
    warnings: list[str] = Field(default_factory=list)
