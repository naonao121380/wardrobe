"""造一批演示数据，供浏览器验收时看缩略图墙用。

⚠️ **这是演示数据，不是「初始化默认值」。** 新用户的默认骨架只预置「大类」的 8 个
取值和一套颜色词表（见 ``app/services/tags.py``）；「小类」默认是空的。本脚本为了
让级联筛选在浏览器里看得见，会额外建若干「小类」取值并挂到对应「大类」下 ——
这只发生在这台开发机上，不会影响任何真实用户的初始化结果。

幂等：只删本脚本造过的同名衣物，不动用户自己传的东西；小类取值撞名就复用。
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

#: (名称, 大类, 小类, 颜色, 形状, 主色)
#: 大类 / 小类都取自默认分类树（app/services/tags.py），脚本只复用不新建。
DEMO: list[tuple[str, str, str, str, str, tuple[int, int, int]]] = [
    ("米白针织开衫", "上衣", "针织衫", "米白", "top", (238, 232, 220)),
    ("酒红真丝衬衫", "上衣", "衬衫", "红", "top", (132, 44, 58)),
    ("浅灰连帽卫衣", "上衣", "卫衣", "灰", "top", (176, 176, 178)),
    ("藏青直筒牛仔裤", "裤子", "牛仔裤", "藏青", "bottom", (52, 66, 96)),
    ("卡其工装裤", "裤子", "工装裤", "卡其", "bottom", (168, 148, 108)),
    ("黑色针织连衣裙", "连衣裙", "针织裙", "黑", "dress", (44, 42, 46)),
    ("黑色西装", "上衣", "西装", "黑", "top", (48, 46, 48)),
    ("驼色长款风衣", "上衣", "风衣", "驼", "top", (196, 164, 122)),
    ("白色帆布鞋", "鞋子", "帆布鞋", "白", "shoe", (242, 242, 238)),
    ("棕色皮质托特包", "包包", "托特包", "棕", "bag", (108, 74, 46)),
    ("棕色腰带", "配饰", "腰带", "棕", "belt", (96, 66, 42)),
    ("米白百褶半身裙", "半身裙", "百褶裙", "米白", "bottom", (238, 232, 220)),
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

    def ensure_value(category: dict, label: str, parent_value_id: int | None = None) -> int:
        """取类别下的取值 id；没有就建（可指定父值）。"""
        for v in category["values"]:
            if v["value"] == label:
                return v["id"]
        payload: dict = {"value": label}
        if parent_value_id is not None:
            payload["parent_value_id"] = parent_value_id
        code, v = call(
            "POST", f"/tags/categories/{category['id']}/values", token=token, json_body=payload
        )
        if code != 201:
            raise RuntimeError(f"建取值 {label} 失败：{code} {v}")
        category["values"].append(v)
        return v["id"]

    def major_id(label: str) -> int:
        return ensure_value(major, label)

    # 「小类」是级联子类别：类别默认就建好了，这里只往里灌演示取值
    minor = by_name.get("小类")
    if minor is None:
        code, minor = call(
            "POST",
            "/tags/categories",
            token=token,
            json_body={"scope": "garment", "name": "小类", "parent_category_id": major["id"]},
        )
        if code != 201:
            print(f"建「小类」类别失败：{code} {minor}")
            return 1
    elif minor["parent_category_id"] != major["id"]:
        code, minor = call(
            "PATCH",
            f"/tags/categories/{minor['id']}",
            token=token,
            json_body={"parent_category_id": major["id"]},
        )
        if code != 200:
            print(f"把「小类」挂到「大类」下失败：{code} {minor}")
            return 1

    color = by_name.get("颜色")  # 老库可能还没这一轴；没有就跳过颜色标签
    if color is None:
        print("⚠️ 该用户没有「颜色」类别（老数据），本次不打颜色标签；")
        print("   想让演示数据完整，先跑 python -m app.cli reseed-tags --username admin --yes")

    made = 0
    for name, major_label, minor_label, color_label, kind, rgb in DEMO:
        tag_ids = [major_id(major_label)]
        # 小类取值必须挂在对应的「大类」取值下 —— 这就是级联
        tag_ids.append(ensure_value(minor, minor_label, parent_value_id=major_id(major_label)))
        if color is not None:
            try:
                tag_ids.append(ensure_value(color, color_label))
            except RuntimeError as exc:
                print(f"  [WARN] {name}：{exc}")

        code, g = call("POST", "/garments", token=token, json_body={"name": name, "tag_value_ids": tag_ids})
        if code != 201:
            print(f"  建衣物 {name} 失败：{code} {g}")
            continue
        code, _ = call(
            "POST",
            f"/garments/{g['id']}/images",
            token=token,
            form=("files", [(f"{g['id']}.png", garment_png(kind, rgb), "image/png")]),
        )
        if code != 201:
            print(f"  传图 {name} 失败：{code}")
            continue
        made += 1
        print(f"  [OK] {name}（{major_label} / {minor_label} / {color_label}）")

    print(f"\n造了 {made} 件衣物")
    print(f"小类取值 {len(minor['values'])} 个，已按大类分组挂好")
    return 0


if __name__ == "__main__":
    sys.exit(main())
