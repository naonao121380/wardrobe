"""按用户取大模型（打标 provider）。

三条规则，来自需求本身：

1. **不默认注入**。服务器不预置任何模型地址 / Key —— 用户自己去设置页填
   模型地址、API Key、模型名称。``config.vlm_*`` 只作为运维侧的显式开关，
   默认是空的。
2. **不同用户互不通用**。调用一律按 owner 取那个人自己的配置，A 用户的 Key
   绝不会被用来处理 B 用户的衣物。
3. **用户没填时管理员可以代填**。落到 ``User.llm_*`` 三个字段上，
   ``llm_from_admin`` 记来源，界面上能看出这份配置是谁填的。

唯一不带密钥的例外是本地 CLIP 模型（``ai_tagger_provider=clip``）：它没有地址与
Key 可填，属于「机器上装没装」而不是「谁的配置」，所以仍由运维全局开关控制。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.config import get_settings
from app.models.user import User
from app.schemas.auth import LlmSettingsOut

__all__ = [
    "LlmConfig",
    "apply_llm_settings",
    "clear_llm_settings",
    "llm_config_of",
    "llm_settings_out",
    "mask_key",
    "tagger_available",
    "tagger_for_user",
]

#: ``providers`` 只能惰性导入：``app.worker.__init__`` 会 import pipeline，
#: pipeline 又要用本模块的 tagger_for_user —— 在模块顶层 import providers
#: 会构成循环（partially initialized module），见开发说明的「循环导入」一节。
#: 这些 provider 只是几个类，函数内 import 的开销可以忽略。


@dataclass(frozen=True)
class LlmConfig:
    """一个用户可用的大模型配置。"""

    base_url: str
    api_key: str | None
    model: str
    from_admin: bool


def llm_config_of(user: User) -> LlmConfig | None:
    """用户自己（或管理员代设）的大模型配置；没配齐就是 ``None``。

    地址与模型名都必填 —— 只有地址没有模型名、或反过来，都调不通，
    当作「没配」比让它去发一个注定失败的请求好。
    """
    base_url = (user.llm_base_url or "").strip()
    model = (user.llm_model or "").strip()
    if not base_url or not model:
        return None
    return LlmConfig(
        base_url=base_url,
        api_key=(user.llm_api_key or "").strip() or None,
        model=model,
        from_admin=bool(user.llm_from_admin),
    )


def mask_key(key: str | None) -> str:
    """脱敏展示：``sk-abc…wxyz``。太短就整体打码，别把密钥露出来。"""
    raw = (key or "").strip()
    if not raw:
        return ""
    if len(raw) <= 12:
        return "•" * len(raw)
    return f"{raw[:6]}…{raw[-4:]}"


def tagger_for_user(user: User):
    """按用户的配置造打标 provider。

    - 用户配了模型 → :class:`VlmTagger`（每次现造：它只是一组 httpx 参数，
      不像 CLIP 要加载权重，没有缓存的价值，反而要小心别把 A 的 Key 缓存给 B）
    - 没配 → 本地 CLIP（仅当运维显式开启）→ 否则 no-op
    """
    from app.worker.providers import NoOpTagger, VlmTagger, shared_clip_tagger

    cfg = llm_config_of(user)
    if cfg is not None:
        return VlmTagger(
            cfg.base_url,
            cfg.api_key,
            cfg.model,
            timeout=get_settings().vlm_timeout_seconds,
        )

    if get_settings().ai_tagger_provider == "clip":
        # 本地模型：没有地址与密钥，属机器能力而非个人配置
        return shared_clip_tagger()

    return NoOpTagger()


def tagger_available(tagger: object) -> bool:
    """这个 provider 现在真能出结果吗（no-op 与「地址空」都算不能）。"""
    if getattr(tagger, "name", "none") == "none":  # NoOpTagger.name
        return False
    return bool(getattr(tagger, "available", True))


def llm_settings_out(user: User) -> LlmSettingsOut:
    """把用户行翻成对外的脱敏视图（设置页与管理页共用，口径只此一处）。"""
    return LlmSettingsOut(
        base_url=user.llm_base_url or None,
        model=user.llm_model or None,
        api_key_masked=mask_key(user.llm_api_key),
        from_admin=bool(user.llm_from_admin),
        configured=user.llm_configured,
    )


def apply_llm_settings(
    user: User,
    *,
    base_url: str | None,
    model: str | None,
    api_key: str | None,
    from_admin: bool,
) -> None:
    """把一份配置写进用户行。

    ``api_key`` 的三态语义：``None`` = 保持不变、空串 = 清空、有值 = 覆盖。
    用户只改模型名时不必把密钥重新粘一遍。
    """
    user.llm_base_url = (base_url or "").strip() or None
    user.llm_model = (model or "").strip() or None
    if api_key is not None:
        user.llm_api_key = api_key.strip() or None
    user.llm_from_admin = from_admin
    # 清空地址等于整份作废，别留着一个孤零零的 Key
    if user.llm_base_url is None and user.llm_model is None:
        user.llm_api_key = None
        user.llm_from_admin = False


def clear_llm_settings(user: User) -> None:
    user.llm_base_url = None
    user.llm_model = None
    user.llm_api_key = None
    user.llm_from_admin = False
