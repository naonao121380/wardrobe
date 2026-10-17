"""框选重抠的两段路：Worker 内部接口 + API 侧的分发。

这一组用例守的是一个**只在 Docker 部署里才暴露**的 bug：API 镜像不含 rembg
（DECISIONS D5），而路由原来直接 ``import app.worker.refine``。裸机测全绿，
真部署点一下就报 ``rembg 不可用：No module named 'rembg'``。
"""

import io

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.core.media import store_image
from app.services import refine as refine_service

# ---------- Worker 内部接口 ----------


def _png(size: tuple[int, int] = (40, 30), color=(200, 120, 80, 255)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA", size, color).save(buf, format="PNG")
    return buf.getvalue()


@pytest_asyncio.fixture
async def worker_client():
    """Worker 那个 FastAPI app 不挂在 /api 下，这里单独起一个客户端。"""
    from app.worker.refine_api import app as worker_app

    async with AsyncClient(
        transport=ASGITransport(app=worker_app), base_url="http://worker"
    ) as client:
        yield client


@pytest.fixture
def fake_remat(monkeypatch):
    """把 rembg 推理换掉：这一组只验「接口与 IO」，真模型在冒烟里验。"""
    seen: dict[str, object] = {}

    def _fake(original: Image.Image, cutout: Image.Image, box: dict, mode: str):
        seen["box"] = box
        seen["mode"] = mode
        seen["size"] = cutout.size
        return Image.new("RGBA", cutout.size, (0, 0, 0, 128))

    # 接口里是「调用时」才 import 的，所以打模块属性就生效
    monkeypatch.setattr("app.worker.refine.remat_box", _fake)
    return seen


async def test_worker_healthz(worker_client):
    r = await worker_client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "matting_provider" in r.json()


async def test_worker_endpoint_returns_png(worker_client, fake_remat):
    original = store_image(1, _png((40, 30)), "image/png")
    cutout = store_image(1, _png((40, 30)), "image/png")

    r = await worker_client.post(
        "/refine/matte-box",
        json={
            "original_key": original["storage_key"],
            "cutout_key": cutout["storage_key"],
            "box": {"x": 0, "y": 0, "width": 40, "height": 30},
            "mode": "add",
        },
    )

    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"
    assert img.size == (40, 30)
    # 框与模式要**原样**转给推理，别被中间层吃掉
    assert fake_remat["mode"] == "add"
    assert fake_remat["box"] == {"x": 0, "y": 0, "width": 40, "height": 30}


async def test_worker_endpoint_rejects_path_traversal(worker_client, fake_remat):
    r = await worker_client.post(
        "/refine/matte-box",
        json={
            "original_key": "../../etc/passwd",
            "cutout_key": "x.png",
            "box": {"x": 0, "y": 0, "width": 40, "height": 30},
            "mode": "remove",
        },
    )
    assert r.status_code == 400
    assert "媒体路径" in r.json()["detail"]


async def test_worker_endpoint_missing_file(worker_client, fake_remat):
    r = await worker_client.post(
        "/refine/matte-box",
        json={
            "original_key": "garments/1/nope.png",
            "cutout_key": "garments/1/nope2.png",
            "box": {"x": 0, "y": 0, "width": 40, "height": 30},
            "mode": "remove",
        },
    )
    assert r.status_code == 409
    assert "缺失" in r.json()["detail"]


async def test_worker_endpoint_maps_refine_error_to_400(worker_client, monkeypatch):
    from app.worker.refine import RefineError

    def _boom(*_a, **_kw):
        raise RefineError("框选区域太小")

    monkeypatch.setattr("app.worker.refine.remat_box", _boom)
    original = store_image(1, _png(), "image/png")

    r = await worker_client.post(
        "/refine/matte-box",
        json={
            "original_key": original["storage_key"],
            "cutout_key": original["storage_key"],
            "box": {"x": 0, "y": 0, "width": 4, "height": 4},
            "mode": "remove",
        },
    )
    assert r.status_code == 400
    assert r.json()["detail"] == "框选区域太小"


# ---------- API 侧分发 ----------


class _StubSettings:
    def __init__(self, url: str) -> None:
        self.worker_refine_url = url
        self.worker_refine_timeout_seconds = 5.0


async def test_dispatch_uses_worker_when_url_configured(monkeypatch):
    monkeypatch.setattr(refine_service, "get_settings", lambda: _StubSettings("http://worker:8001/"))
    seen: dict[str, object] = {}

    async def _remote(base, original_key, cutout_key, box, mode):
        seen["base"] = base
        return refine_service.RefineOutcome(png=b"png")

    monkeypatch.setattr(refine_service, "_remat_remote", _remote)
    monkeypatch.setattr(
        refine_service, "_remat_local", _should_not_be_called, raising=False
    )

    out = await refine_service.remat_box(
        original=Image.new("RGB", (8, 8)),
        cutout=Image.new("RGBA", (8, 8)),
        original_key="a.png",
        cutout_key="b.png",
        box={},
        mode="remove",
    )

    assert out.ok
    # 结尾的斜杠要去掉，否则拼出来是 //refine/matte-box
    assert seen["base"] == "http://worker:8001"


async def test_dispatch_falls_back_to_local(monkeypatch):
    monkeypatch.setattr(refine_service, "get_settings", lambda: _StubSettings(""))
    seen: dict[str, object] = {}

    async def _local(original, cutout, box, mode):
        seen["local"] = True
        return refine_service.RefineOutcome(png=b"png")

    monkeypatch.setattr(refine_service, "_remat_local", _local)
    monkeypatch.setattr(refine_service, "_remat_remote", _should_not_be_called, raising=False)

    out = await refine_service.remat_box(
        original=Image.new("RGB", (8, 8)),
        cutout=Image.new("RGBA", (8, 8)),
        original_key="a.png",
        cutout_key="b.png",
        box={},
        mode="remove",
    )

    assert out.ok and seen["local"] is True


def _should_not_be_called(*_a, **_kw):  # pragma: no cover - 走到这里就是用例失败
    raise AssertionError("不该走这条路")
