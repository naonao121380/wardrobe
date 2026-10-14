"""图片存储、缩略图生成与签名 URL。

为什么需要签名 URL：图片通过 ``<img src>`` 加载，浏览器不会附带 Authorization 头；
而媒体目录又不能直接公开暴露（多用户下会串号）。所以由 API 为每个文件签发一条
带 HMAC 的临时链接，签名同时绑定「文件路径 + 过期时间」。
"""

import base64
import hashlib
import hmac
import io
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from app.config import get_settings
from app.core.time import now_utc

# 单张图片解码上限，防解压炸弹
Image.MAX_IMAGE_PIXELS = 80_000_000

ALLOWED_CONTENT_TYPES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


class MediaError(Exception):
    """媒体处理失败，路由层转成 4xx。"""


def media_root() -> Path:
    return Path(get_settings().media_root).resolve()


def resolve_key(rel_key: str) -> Path:
    """把相对 key 解析为绝对路径，并挡住路径穿越。"""
    root = media_root()
    target = (root / rel_key).resolve()
    if target != root and root not in target.parents:
        raise MediaError("非法的媒体路径")
    return target


# ---------- 签名 ----------


def _sign(rel_key: str, expires_at: int) -> str:
    secret = get_settings().jwt_secret.encode("utf-8")
    message = f"{rel_key}|{expires_at}".encode()
    digest = hmac.new(secret, message, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest[:16]).decode("ascii").rstrip("=")


def sign_url(rel_key: str) -> str:
    """生成可直接放进 <img src> 的签名链接。"""
    expires_at = int(now_utc().timestamp()) + get_settings().media_url_ttl_seconds
    return f"/api/media/{rel_key}?e={expires_at}&s={_sign(rel_key, expires_at)}"


def verify_signature(rel_key: str, expires_at: int, signature: str) -> bool:
    if expires_at < int(now_utc().timestamp()):
        return False
    return hmac.compare_digest(_sign(rel_key, expires_at), signature)


# ---------- 写入 ----------


def _make_thumb(img: Image.Image, max_px: int) -> bytes:
    copy = img.copy()
    copy.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    if copy.mode not in ("RGB", "RGBA"):
        copy = copy.convert("RGBA" if "A" in img.getbands() else "RGB")
    buf = io.BytesIO()
    copy.save(buf, format="WEBP", quality=82, method=4)
    return buf.getvalue()


def store_image(owner_id: int, data: bytes, content_type: str | None) -> dict[str, object]:
    """落盘原图 + 两档缩略图，返回可直接写进 GarmentImage 的字段。

    原图**不做重编码**（保留原始格式与画质），仅在生成缩略图时应用 EXIF 方向。
    """
    s = get_settings()
    ct = (content_type or "").split(";")[0].strip().lower()
    ext = ALLOWED_CONTENT_TYPES.get(ct)
    if ext is None:
        raise MediaError("仅支持 JPEG / PNG / WebP 格式")
    if len(data) > s.max_upload_bytes:
        raise MediaError(f"图片超过 {s.max_upload_bytes // (1024 * 1024)} MB 上限")

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img.load()
    except Exception as exc:  # Pillow 的异常类型很杂，统一收口
        raise MediaError("无法解析该图片文件") from exc

    uid = uuid.uuid4().hex
    rel_dir = f"garments/{owner_id}"
    resolve_key(f"{rel_dir}/thumbs").mkdir(parents=True, exist_ok=True)

    original_key = f"{rel_dir}/{uid}{ext}"
    small_key = f"{rel_dir}/thumbs/{uid}_s.webp"
    medium_key = f"{rel_dir}/thumbs/{uid}_m.webp"

    resolve_key(original_key).write_bytes(data)
    resolve_key(small_key).write_bytes(_make_thumb(img, s.thumbnail_small_px))
    resolve_key(medium_key).write_bytes(_make_thumb(img, s.thumbnail_medium_px))

    return {
        "storage_key": original_key,
        "thumb_small_key": small_key,
        "thumb_medium_key": medium_key,
        "width": img.width,
        "height": img.height,
        "bytes": len(data),
        "content_type": ct,
    }


def delete_keys(keys: list[str | None]) -> None:
    """尽力删除；文件已不存在或路径非法都不报错。"""
    for key in keys:
        if not key:
            continue
        try:
            resolve_key(key).unlink(missing_ok=True)
        except (MediaError, OSError):
            continue


def open_for_export(rel_key: str) -> tuple[bytes, str]:
    """读取媒体文件，返回 (字节, content_type)。"""
    path = resolve_key(rel_key)
    if not path.is_file():
        raise MediaError("文件不存在")
    suffix = path.suffix.lower()
    ct = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
    return path.read_bytes(), ct
