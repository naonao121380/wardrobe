"""媒体文件读取。

链接必须带有效签名（由 ``app.core.media.sign_url`` 签发）。图片走 ``<img src>``，
浏览器不会附带 Authorization 头，所以用「签名即授权」的预签名 URL 模式。
"""

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.core.media import MediaError, open_for_export, verify_signature

router = APIRouter(prefix="/media", tags=["media"])


@router.get("/{rel_key:path}")
async def get_media(
    rel_key: str,
    e: int = Query(description="过期时间戳（秒）"),
    s: str = Query(description="HMAC 签名"),
) -> Response:
    if not verify_signature(rel_key, e, s):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "链接无效或已过期")

    try:
        data, content_type = open_for_export(rel_key)
    except MediaError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    return Response(
        content=data,
        media_type=content_type,
        headers={
            # 私有缓存：链接本身有有效期，浏览器缓存一天足够
            "Cache-Control": "private, max-age=86400",
        },
    )
