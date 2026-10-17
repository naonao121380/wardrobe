"""管理员接口的请求 / 响应模型。

两条脱敏规则：

- **API Key 一律只回脱敏串**（``mask_key``），无论谁在问 —— 管理员也不需要看到
  别人的完整密钥，需要换就直接覆盖。
- **用户详情不回密码摘要**，只回「有没有配模型」「最近一次登录」这类结论。
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.auth import LlmSettingsOut, QuotaItem, SessionInfo


class UserStatsOut(BaseModel):
    """用户的数据用量。"""

    garments: int = 0
    outfits: int = 0
    tags: int = 0
    #: 未吊销的登录设备数
    sessions: int = 0


class QuotasOut(BaseModel):
    """三档配额（``limit`` 为 ``None`` 表示不限）。"""

    garments: QuotaItem
    outfits: QuotaItem
    tags: QuotaItem


class AdminUserOut(BaseModel):
    """列表里的一行。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    is_admin: bool = False
    is_active: bool = True
    created_at: datetime
    #: 最近一次活跃（取所有设备里最新的 last_seen_at）；从没登录过就是 null
    last_seen_at: datetime | None = None
    stats: UserStatsOut
    quotas: QuotasOut
    llm: LlmSettingsOut


class AdminUserDetailOut(AdminUserOut):
    """详情弹窗额外给出设备列表（要显示是哪几台在登着）。"""

    sessions: list[SessionInfo] = []


class AdminUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    is_admin: bool = False
    #: 不传 = 不限
    garment_quota: int | None = Field(default=None, ge=0)
    outfit_quota: int | None = Field(default=None, ge=0)
    tag_quota: int | None = Field(default=None, ge=0)
    #: 建号时顺手把模型也配上（用户自己没配时管理员代设的场景）
    llm_base_url: str | None = Field(default=None, max_length=512)
    llm_api_key: str | None = Field(default=None, max_length=512)
    llm_model: str | None = Field(default=None, max_length=128)


class AdminUserUpdate(BaseModel):
    """改资料 / 配额 / 状态。**只传想改的字段**，没传的一律不动。"""

    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    is_admin: bool | None = None
    is_active: bool | None = None
    garment_quota: int | None = Field(default=None, ge=0)
    outfit_quota: int | None = Field(default=None, ge=0)
    tag_quota: int | None = Field(default=None, ge=0)
    #: 显式把配额改回「不限」时用：``{"garment_quota": null}`` 与「不传」在
    #: pydantic 里都是 None，靠这两个开关区分（默认都当成「不动」）
    clear_garment_quota: bool = False
    clear_outfit_quota: bool = False
    clear_tag_quota: bool = False


class AdminPasswordReset(BaseModel):
    new_password: str = Field(min_length=8, max_length=256)


class AdminLlmUpdate(BaseModel):
    """管理员给某个用户设的大模型配置。整份覆盖，``llm_from_admin`` 置位。"""

    base_url: str | None = Field(default=None, max_length=512)
    api_key: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)


class RegistrationSettingsOut(BaseModel):
    """注册模式。``invite_codes_usable`` 是给界面提示用的计数。"""

    mode: str
    #: 现有可用（未吊销 / 未过期 / 还有次数）的邀请码数量
    invite_codes_usable: int = 0


class RegistrationSettingsIn(BaseModel):
    mode: str = Field(pattern=r"^(open|invite|closed)$")


class InviteCodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    note: str | None = None
    max_uses: int | None = None
    used_count: int = 0
    #: 还能用几次；``None`` = 不限
    remaining: int | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    #: 现在还能不能用（未吊销 / 未过期 / 还有次数）
    usable: bool = False


class InviteCodeCreateIn(BaseModel):
    """生成邀请码。``count`` 支持一次生成多枚（如给一个班的学生）。"""

    count: int = Field(default=1, ge=1, le=50)
    note: str | None = Field(default=None, max_length=128)
    #: 不传 = 不限次数；1 = 一人一码
    max_uses: int | None = Field(default=None, ge=1, le=10000)
    #: 不传 = 永不过期
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
