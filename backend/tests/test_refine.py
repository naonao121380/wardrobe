"""抠图二次修整的纯函数部分（不依赖 rembg）。"""

from PIL import Image

from app.worker.refine import RefineError, replace_cutout_pixels, _clamp_box
import pytest


def test_clamp_box_normal():
    img = Image.new("RGB", (1000, 800))
    assert _clamp_box(img, {"x": 10, "y": 20, "width": 300, "height": 200}) == (10, 20, 310, 220)


def test_clamp_box_clamps_to_bounds():
    img = Image.new("RGB", (1000, 800))
    # 超出边界的部分被裁掉
    assert _clamp_box(img, {"x": -50, "y": -50, "width": 2000, "height": 2000}) == (0, 0, 1000, 800)


def test_clamp_box_rejects_tiny():
    img = Image.new("RGB", (1000, 800))
    with pytest.raises(RefineError):
        _clamp_box(img, {"x": 0, "y": 0, "width": 4, "height": 4})


def test_clamp_box_rejects_nan():
    img = Image.new("RGB", (1000, 800))
    with pytest.raises(RefineError):
        _clamp_box(img, {"x": "abc", "y": 0, "width": 100, "height": 100})


def test_replace_cutout_pixels_resizes():
    base = Image.new("RGBA", (200, 300), (255, 0, 0, 255))
    incoming = Image.new("RGBA", (100, 150), (0, 255, 0, 128))
    out = replace_cutout_pixels(base, incoming)
    assert out.size == (200, 300)
    assert out.getpixel((0, 0)) == (0, 255, 0, 128)
