"""造一批演示数据，供浏览器验收时看缩略图墙用。"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image, ImageDraw

BASE = "http://127.0.0.1:8000/api"

# (名称, 大类, 小类, 颜色, 材质, 底色)
DEMO = [
    ("米白羊毛开衫", "针织", "开衫", "米白", "羊毛", (238, 232, 220)),
    ("黑色西装外套", "外套", "西装", "黑", "聚酯纤维", (48, 46, 48)),
    ("驼色风衣", "外套", "风衣", "驼", "棉", (196, 164, 122)),
    ("藏青直筒牛仔裤", "裤装", "牛仔", "藏青", "牛仔布", (52, 66, 96)),
    ("酒红真丝衬衫", "上衣", "衬衫", "酒红", "真丝", (132, 44, 58)),
    ("浅灰卫衣", "上衣", "卫衣", "灰", "棉", (176, 176, 178)),
    ("白色帆布鞋", "鞋履", "休闲鞋", "白", "帆布", (242, 242, 238)),
    ("棕色皮带", "配饰", "腰带", "棕", "牛皮", (108, 74, 46)),
]


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


def garment_png(bg: tuple[int, int, int]) -> bytes:
    """画一个像件衣服的简单图形 —— 只为让缩略图墙有辨识度。"""
    w, h = 900, 1200
    im = Image.new("RGB", (w, h), (250, 249, 246))
    d = ImageDraw.Draw(im)
    dark = tuple(max(0, c - 26) for c in bg)
    # 躯干
    d.rounded_rectangle([230, 300, 670, 1000], radius=40, fill=bg, outline=dark, width=4)
    # 两袖
    d.rounded_rectangle([120, 330, 250, 760], radius=34, fill=bg, outline=dark, width=4)
    d.rounded_rectangle([650, 330, 780, 760], radius=34, fill=bg, outline=dark, width=4)
    # 领口
    d.ellipse([390, 280, 510, 380], fill=(250, 249, 246), outline=dark, width=4)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    code, body = call("POST", "/auth/login", json_body={"username": "admin", "password": "admin12345"})
    if code != 200:
        print(f"登录失败：{code} {body}")
        return 1
    token = body["access_token"]

    # 清掉旧数据
    _, existing = call("GET", "/garments?page_size=200", token=token)
    for it in existing["items"]:
        call("DELETE", f"/garments/{it['id']}", token=token)

    _, cats = call("GET", "/tags/categories?scope=garment", token=token)
    cat_by_name = {c["name"]: c for c in cats}

    def value_id(cat_name: str, value: str) -> int:
        cat = cat_by_name[cat_name]
        for v in cat["values"]:
            if v["value"] == value:
                return v["id"]
        code, v = call("POST", f"/tags/categories/{cat['id']}/values", token=token, json_body={"value": value})
        if code != 201:
            raise RuntimeError(f"建取值 {value} 失败：{code} {v}")
        cat["values"].append(v)  # 更新本地缓存，否则同一取值第二次查会重复创建
        return v["id"]

    made = 0
    for name, major, minor, color, material, bg in DEMO:
        ids = [
            value_id("大类", major),
            value_id("小类", minor),
            value_id("颜色", color),
            value_id("材质", material),
        ]
        code, g = call("POST", "/garments", token=token, json_body={"name": name, "tag_value_ids": ids})
        if code != 201:
            print(f"  建衣物 {name} 失败：{code} {g}")
            continue
        code, _ = call(
            "POST",
            f"/garments/{g['id']}/images",
            token=token,
            form=("files", [(f"{g['id']}.png", garment_png(bg), "image/png")]),
        )
        if code != 201:
            print(f"  传图 {name} 失败：{code}")
            continue
        made += 1
        print(f"  [OK] {name}")

    print(f"\n造了 {made} 件衣物")
    return 0


if __name__ == "__main__":
    sys.exit(main())
