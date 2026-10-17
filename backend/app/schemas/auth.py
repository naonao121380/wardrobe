"""鉴权相关的请求 / 响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    device_label: str | None = Field(default=None, max_length=128)


class RegistrationPolicyOut(BaseModel):
    """**公开**的注册策略 —— 登录页据此决定要不要显示注册入口。

    单独开一个不带鉴权的端点：登录页在拿到令牌之前就知道注册开不开、
    要不要邀请码，而不必先登录（``/meta`` 是要登录的）。
    这里只暴露「模式」，不暴露邀请码本身或任何账号信息。
    """

    mode: str
    invite_required: bool


class RegisterRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=256)
    display_name: str | None = Field(default=None, max_length=64)
    email: str | None = Field(default=None, max_length=255)
    #: 邀请码。仅当注册模式为 ``invite`` 时必填（大小写与短横不敏感）。
    invite_code: str | None = Field(default=None, max_length=64)
    device_label: str | None = Field(default=None, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=16, max_length=512)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    display_name: str | None = None
    email: str | None = None
    is_admin: bool = False
    is_active: bool = True


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_at: datetime
    session_id: int
    user: UserOut


class SessionInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_label: str | None = None
    user_agent: str | None = None
    ip_address: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None
    expires_at: datetime


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=8, max_length=256)


class ProfileUpdate(BaseModel):
    """改自己的资料。目前只有显示名（用户名是登录凭据，不给改）。"""

    display_name: str | None = Field(default=None, max_length=64)


class MetaOut(BaseModel):
    """前端「关于」面板要用的运行信息汇总。"""

    app_name: str
    version: str
    #: open / invite / closed —— 登录页据此决定要不要显示注册入口
    registration_mode: str
    matting_provider: str
    tagger_provider: str
    tagger_available: bool
    #: 当前用户有没有配好自己的大模型（没配时 AI 打标不可用）
    llm_configured: bool


class LlmSettingsOut(BaseModel):
    """当前用户的大模型配置。**API Key 只回脱敏串**。"""

    base_url: str | None = None
    model: str | None = None
    #: 形如 ``sk-abc…wxyz``；没配就是空串
    api_key_masked: str = ""
    #: 这份配置是不是管理员代设的
    from_admin: bool = False
    #: 地址与模型名是否都填了（少一个都调不通）
    configured: bool = False


class LlmSettingsIn(BaseModel):
    """用户自己填的大模型配置。

    ``api_key`` 语义：**不传 = 保持不变**，传空串 = 清空，传值 = 覆盖。
    这样用户只改模型名时不用把 Key 重新粘一遍。
    """

    base_url: str | None = Field(default=None, max_length=512)
    model: str | None = Field(default=None, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)


class QuotaItem(BaseModel):
    """一类资源的用量与上限。``limit`` 为 ``None`` 表示不限。"""

    used: int
    limit: int | None = None


class QuotaOut(BaseModel):
    """当前用户的配额用量，设置页给自己看。"""

    garments: QuotaItem
    outfits: QuotaItem
    tags: QuotaItem
