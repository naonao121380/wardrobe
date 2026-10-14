"""造一批演示数据，供浏览器验收时看缩略图墙用。

只打「大类」标签 —— 与新用户的默认体系保持一致。颜色 / 材质 / 小类留给用户自己
在「管理标签」里加，脚本不越俎代庖。

幂等：只删本脚本造过的同名衣物，不动用户自己传的东西。
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8000/api"

#: (名称, 大类, 形状, 主色)
DEMO: list[tuple[str, str, str, tuple[int, int, int]]] = [
    ("米白羊毛开衫", "上装", "top", (238, 232, 220)),
    ("酒红真丝衬衫", "上装", "top", (132, 44, 58)),
    ("浅灰连帽卫衣", "上装", "top", (176, 176, 178)),
    ("藏青直筒牛仔裤", "下装", "bottom", (52, 66, 96)),
    ("卡其工装裤", "下装", "bottom", (168, 148, 108)),
    ("黑色针织连衣裙", "连衣裙", "dress", (44, 42, 46)),
    ("黑色西装外套", "外套", "top", (48, 46, 48)),
    ("驼色长款风衣", "外套", "top", (196, 164, 122)),
    ("白色帆布鞋", "鞋履", "shoe", (242, 242, 238)),
    ("棕色皮质托特包", "包袋", "bag", (108, 74, 46)),
    ("棕色皮带", "配饰", "belt", (96, 66, 42)),
    ("灰色纯棉家居套装", "内衣家居", "top", (198, 200, 202)),
]

PAPER = (250, 249, 246)


def _shade(color: tuple[int, int, int], delta: int = -28) -> tuple[int, int, int]:
    return tuple(max(0, c + delta) for c in color)  # type: ignore[return-value]


def garment_png(kind: str, color: tuple[int, int, int]) -> bytes:
    """按品类画一个简笔图形 —— 只为让缩略图墙有辨识度，不追求写实。"""
    w, h = 900, 1200
    im = Image.new("RGB", (w, h), PAPER)
    d = ImageDraw.Draw(im)
    edge = _shade(color)

    def box(x0, y0, x1, y1, r=36, fill=None):
        d.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill or color, outline=edge, width=5)

    if kind == "top":
        box(230, 300, 670, 1010, 44)          # 躯干
        box(120, 330, 255, 780, 34)           # 左袖
        box(645, 330, 780, 780, 34)           # 右袖
        d.ellipse([392, 276, 508, 378], fill=PAPER, outline=edge, width=5)  # 领口
    elif kind == "bottom":
        box(250, 300, 650, 560, 34)           # 腰臀
        box(250, 520, 430, 1060, 26)          # 左腿
        box(470, 520, 650, 1060, 26)          # 右腿
        d.line([450, 520, 450, 700], fill=PAPER, width=6)
        d.rectangle([300, 300, 600, 340], fill=_shade(color, -50), outline=edge, width=4)  # 腰头
    elif kind == "dress":
        d.polygon(
            [(340, 300), (560, 300), (700, 1050), (200, 1050)],
            fill=color, outline=edge,
        )
        d.rounded_rectangle([336, 290, 564, 360], radius=30, fill=color, outline=edge, width=5)
        d.ellipse([400, 262, 500, 352], fill=PAPER, outline=edge, width=5)
    elif kind == "shoe":
        d.polygon(  # 鞋面
            [(215, 775), (250, 610), (430, 545), (610, 600), (700, 700), (735, 775)],
            fill=color,
            outline=edge,
        )
        d.rounded_rectangle([175, 762, 760, 855], radius=46, fill=_shade(color, -95), outline=edge, width=5)
        for y, x1, x2 in ((630, 385, 585), (668, 370, 610), (706, 355, 635)):  # 鞋带
            d.line([x1, y, x2, y - 26], fill=edge, width=9)
    elif kind == "bag":
        box(240, 470, 660, 980, 30)
        d.arc([350, 300, 550, 520], start=180, end=360, fill=edge, width=16)  # 提手
        d.line([240, 620, 660, 620], fill=_shade(color, -45), width=6)
    elif kind == "belt":
        d.rounded_rectangle([120, 520, 780, 640], radius=18, fill=color, outline=edge, width=5)
        d.rounded_rectangle([520, 470, 700, 690], radius=14, outline=_shade(color, -70), width=14)
        d.ellipse([560, 540, 640, 620], fill=PAPER, outline=edge, width=4)
    else:  # pragma: no cover - 防御
        box(240, 320, 660, 980, 40)

    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def call(method, path, *, token=None, json_body=None, form=None):
    url = f"{BASE}{urllib.parse.quote(path, safe='/?&=%')}"
    data, headers = None, {}
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        boundary = "----wardrobeSeed"
        name, files = form
        buf = io.BytesIO()
        for filename, content, ctype in files:
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
            )
            buf.write(f"Content-Type: {ctype}\r\n\r\n".encode())
            buf.write(content)
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        data = buf.getvalue()
        headers["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if "json" in r.headers.get("Content-Type", "") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def main() -> int:
    code, body = call("POST", "/auth/login", json_body={"username": "admin", "password": "admin12345"})
    if code != 200:
        print(f"登录失败：{code} {body}")
        return 1
    token = body["access_token"]

    # 只清掉**本脚本造过**的同名衣物，不动用户自己传的东西
    demo_names = {d[0] for d in DEMO}
    _, existing = call("GET", "/garments?page_size=200", token=token)
    for it in existing["items"]:
        if it["name"] in demo_names:
            call("DELETE", f"/garments/{it['id']}", token=token)

    _, cats = call("GET", "/tags/categories?scope=garment", token=token)
    by_name = {c["name"]: c for c in cats}
    if "大类" not in by_name:
        print("该用户没有「大类」标签类别 —— 先跑一次 cli reseed-tags 或删库重来")
        return 1

    major = by_name["大类"]
    value_ids = {v["value"]: v["id"] for v in major["values"]}

    def major_id(label: str) -> int:
        if label in value_ids:
            return value_ids[label]
        code, v = call(
            "POST", f"/tags/categories/{major['id']}/values", token=token, json_body={"value": label}
        )
        if code != 201:
            raise RuntimeError(f"建大类取值 {label} 失败：{code} {v}")
        value_ids[label] = v["id"]
        return v["id"]

    made = 0
    for name, label, kind, color in DEMO:
        code, g = call(
            "POST", "/garments", token=token, json_body={"name": name, "tag_value_ids": [major_id(label)]}
        )
        if code != 201:
            print(f"  建衣物 {name} 失败：{code} {g}")
            continue
        code, _ = call(
            "POST",
            f"/garments/{g['id']}/images",
            token=token,
            form=("files", [(f"{g['id']}.png", garment_png(kind, color), "image/png")]),
        )
        if code != 201:
            print(f"  传图 {name} 失败：{code}")
            continue
        made += 1
        print(f"  [OK] {name}（{label}）")

    print(f"\n造了 {made} 件衣物")
    return 0


if __name__ == "__main__":
    sys.exit(main())
