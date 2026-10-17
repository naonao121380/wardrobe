"""批次 2 端到端冒烟：真起一个后端进程，走 HTTP 打全流程。

与 pytest 的区别：这里用的是真实 SQLite 文件 + 真实媒体目录 + 真实 multipart
上传，验证「迁移 → 启动 → 上传落盘 → 签名 URL 取图 → 筛选 → 删除」整条链路。
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from PIL import Image

BASE = "http://127.0.0.1:8000/api"

ok_count = 0
fail: list[str] = []


def check(label: str, cond: bool, extra: str = "") -> None:
    global ok_count
    if cond:
        ok_count += 1
        print(f"  [OK] {label}")
    else:
        fail.append(label)
        print(f"  [FAIL] {label} {extra}")


def call(
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: dict | None = None,
    form: tuple[str, list[tuple[str, bytes, str]]] | None = None,
) -> tuple[int, object]:
    url = f"{BASE}{urllib.parse.quote(path, safe='/?&=%')}"
    data: bytes | None = None
    headers: dict[str, str] = {}

    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        boundary = "----wardrobeE2E"
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
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            return resp.status, (json.loads(raw) if "json" in ctype else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, raw


def png(size: tuple[int, int] = (900, 1200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (200, 180, 160)).save(buf, format="PNG")
    return buf.getvalue()


def ensure_value(token: str, cat_id: int, value: str, parent_value_id: int | None = None) -> int:
    """新增取值；已存在（409）则从类别列表里找回它的 id —— 让脚本可重复跑。"""
    payload: dict = {"value": value}
    if parent_value_id is not None:
        payload["parent_value_id"] = parent_value_id
    code, body = call("POST", f"/tags/categories/{cat_id}/values", token=token, json_body=payload)
    if code == 201:
        check(f"取值「{value}」新建成功", True)
        return body["id"]  # type: ignore[index]
    # 409 说明是预置值或上次跑残留 —— 都算正常，去列表里捞回来
    check(f"取值「{value}」已存在（复用）", code == 409, f"got {code} {body}")
    _, cats = call("GET", "/tags/categories?scope=garment", token=token)
    for c in cats:  # type: ignore[union-attr]
        if c["id"] == cat_id:
            for v in c["values"]:
                if v["value"] == value:
                    return v["id"]
    raise RuntimeError(f"取值 {value} 既建不了也找不到")


def ensure_category(
    token: str, name: str, scope: str = "garment", parent_category_id: int | None = None
) -> int:
    """取类别 id；不存在就建。默认骨架已建齐，通常直接命中。"""
    _, cats = call("GET", f"/tags/categories?scope={scope}", token=token)
    for c in cats:  # type: ignore[union-attr]
        if c["name"] == name:
            return c["id"]
    payload: dict = {"scope": scope, "name": name}
    if parent_category_id is not None:
        payload["parent_category_id"] = parent_category_id
    code, body = call("POST", "/tags/categories", token=token, json_body=payload)
    if code != 201:
        raise RuntimeError(f"建类别 {name} 失败：{code} {body}")
    return body["id"]  # type: ignore[index]


def ensure_user(username: str, password: str) -> str:
    """注册（或已存在则直接登录），返回 access token。"""
    call("POST", "/auth/register", json_body={"username": username, "password": password})
    code, body = call("POST", "/auth/login", json_body={"username": username, "password": password})
    if code != 200:
        raise RuntimeError(f"{username} 登录失败：{code} {body}")
    return body["access_token"]  # type: ignore[index]


def main() -> int:
    print("\n== 1. 登录 ==")
    code, body = call("POST", "/auth/login", json_body={"username": "admin", "password": "admin12345"})
    check("管理员登录 200", code == 200, f"got {code} {body}")
    if code != 200:
        return 1
    token = body["access_token"]  # type: ignore[index]

    # 只清掉本脚本造过的同名衣物（避免误删 seed_demo 的演示数据），
    # 保证「按名称命中」这类断言是确定的
    mine = {"米白羊毛外套"}
    _, existing = call("GET", "/garments?page_size=200", token=token)
    for it in existing["items"]:  # type: ignore[index]
        if it["name"] in mine:
            call("DELETE", f"/garments/{it['id']}", token=token)

    print("\n== 2. 默认标签骨架 ==")
    code, cats = call("GET", "/tags/categories?scope=garment", token=token)
    check("取衣物标签 200", code == 200)
    names = [c["name"] for c in cats]  # type: ignore[union-attr]
    check(
        "衣物骨架 = 大类/小类/颜色/材质",
        names == ["大类", "小类", "颜色", "材质"],
        f"got {names}（老库请先跑 cli reseed-tags --username admin --yes）",
    )
    by_name = {c["name"]: c for c in cats}  # type: ignore[union-attr]
    major = by_name["大类"]
    check("大类预置 8 个取值", len(major["values"]) == 8, f"got {[v['value'] for v in major['values']]}")
    check("颜色预置一套词表（20）", len(by_name["颜色"]["values"]) == 20)
    check(
        "小类预置 8×10（每个大类各 10 个）、材质不预置",
        len(by_name["小类"]["values"]) == 80 and by_name["材质"]["values"] == [],
        f"小类 {len(by_name['小类']['values'])} 条",
    )
    check("小类级联到「大类」", by_name["小类"]["parent_category_id"] == major["id"])
    check("颜色是顶层类别（不级联）", by_name["颜色"]["parent_category_id"] is None)

    code, outfit_cats = call("GET", "/tags/categories?scope=outfit", token=token)
    check("搭配骨架 = 季节/风格/色系", [c["name"] for c in outfit_cats] == ["季节", "风格", "色系"])  # type: ignore[union-attr]
    season = next(c for c in outfit_cats if c["name"] == "季节")  # type: ignore[union-attr]
    check("季节预置 4 个取值", len(season["values"]) == 4)
    check(
        "风格 / 色系 不预置取值",
        all(next(c for c in outfit_cats if c["name"] == n)["values"] == [] for n in ("风格", "色系")),  # type: ignore[union-attr]
    )

    print("\n== 2.5 级联（大类 → 小类）==")
    minor = by_name["小类"]
    outer = next(v for v in major["values"] if v["value"] == "上衣")
    minor_id = ensure_value(token, minor["id"], "级联测试小类", parent_value_id=outer["id"])
    _, cats2 = call("GET", "/tags/categories?scope=garment", token=token)
    coat = next(
        v
        for c in cats2  # type: ignore[union-attr]
        if c["name"] == "小类"
        for v in c["values"]
        if v["id"] == minor_id
    )
    check("小类取值挂到大类取值下", coat["parent_value_id"] == outer["id"], f"got {coat}")

    black = next(v for v in by_name["颜色"]["values"] if v["value"] == "黑")
    code, body = call(
        "POST",
        f"/tags/categories/{minor['id']}/values",
        token=token,
        json_body={"value": "跨类别挂父值", "parent_value_id": black["id"]},
    )
    check("小类取值挂到「颜色」取值下被拒 400", code == 400, f"got {code} {body}")

    code, body = call(
        "POST",
        f"/tags/categories/{by_name['颜色']['id']}/values",
        token=token,
        json_body={"value": "顶层挂父值", "parent_value_id": outer["id"]},
    )
    check("顶层类别取值挂父值被拒 400", code == 400, f"got {code} {body}")

    code, body = call(
        "POST",
        "/tags/categories",
        token=token,
        json_body={"scope": "garment", "name": "三层测试", "parent_category_id": minor["id"]},
    )
    check("只允许两层（子类别不能再当父）400", code == 400, f"got {code} {body}")

    cat_id = major["id"]
    mat_id = by_name["材质"]["id"]
    v1_id = ensure_value(token, cat_id, "外套")
    v2_id = ensure_value(token, mat_id, "羊毛")
    code, _ = call("POST", f"/tags/categories/{cat_id}/values", token=token, json_body={"value": "外套"})
    check("重复取值 409", code == 409, f"got {code}")

    print("\n== 3. 建衣物 + 上传图片 ==")
    code, g = call("POST", "/garments", token=token, json_body={"name": "米白羊毛外套", "tag_value_ids": [v1_id, v2_id]})
    check("创建衣物 201", code == 201, f"got {code} {g}")
    check("标签回显 2 条", len(g["tags"]) == 2, f"got {g['tags']}")  # type: ignore[index]
    gid = g["id"]  # type: ignore[index]

    code, g2 = call(
        "POST",
        f"/garments/{gid}/images",
        token=token,
        form=("files", [("a.png", png((900, 1200)), "image/png"), ("b.png", png((600, 800)), "image/png")]),
    )
    check("上传两张图 201", code == 201, f"got {code} {g2}")
    imgs = g2["images"]  # type: ignore[index]
    check("图片数 = 2", len(imgs) == 2, f"got {len(imgs)}")
    check("有封面 URL", bool(g2["cover_url"]))  # type: ignore[index]

    print("\n== 4. 签名 URL 能取到图 ==")
    small = imgs[0]["thumb_small_url"]
    req = urllib.request.Request(f"http://127.0.0.1:8000{small}")
    with urllib.request.urlopen(req) as resp:
        blob = resp.read()
    check("缩略图 200 且非空", len(blob) > 0, f"{len(blob)} bytes")
    im = Image.open(io.BytesIO(blob))
    check("缩略图已缩到 240 内", max(im.size) <= 240, f"got {im.size}")
    check("缩略图是 WebP", im.format == "WEBP", f"got {im.format}")

    bad = small.split("?")[0] + "?e=99999999999&s=deadbeef"
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8000{bad}"))
        check("篡改签名被拒", False, "竟然 200")
    except urllib.error.HTTPError as e:
        check("篡改签名被拒（403）", e.code == 403, f"got {e.code}")

    print("\n== 5. 非图片被拒 ==")
    code, body = call(
        "POST", f"/garments/{gid}/images", token=token, form=("files", [("x.txt", b"not an image", "image/png")])
    )
    check("伪造 png 被拒 400", code == 400, f"got {code} {body}")

    print("\n== 6. 封面切换 ==")
    code, g3 = call("POST", f"/garments/{gid}/images/{imgs[1]['id']}/primary", token=token)
    check("设封面 200", code == 200)
    check("第二张成为首图", g3["images"][0]["id"] == imgs[1]["id"], f"got {[i['id'] for i in g3['images']]}")  # type: ignore[index]

    print("\n== 7. 筛选 ==")
    # 库里可能有 seed_demo 的演示数据，所以断言「包含」而不是「恰好等于」
    code, lst = call("GET", f"/garments?tag_value_ids={v1_id}", token=token)
    ids = [i["id"] for i in lst["items"]]  # type: ignore[index]
    check("按标签命中包含本件", gid in ids, f"got {ids}")

    code, lst = call("GET", "/garments?q=米白羊毛外套", token=token)
    names = [i["name"] for i in lst["items"]]  # type: ignore[index]
    check("按名称命中本件", names == ["米白羊毛外套"], f"got {names}")

    code, lst = call("GET", "/garments?q=不存在的名字", token=token)
    check("无匹配为空", lst["total"] == 0, f"got {lst['total']}")  # type: ignore[index]

    code, lst = call("GET", f"/garments?tag_value_ids={v1_id}&tag_value_ids={v2_id}", token=token)
    ids = [i["id"] for i in lst["items"]]  # type: ignore[index]
    check("双标签取交集包含本件", gid in ids, f"got {ids}")

    print("\n== 8. 用户隔离 ==")
    lt = ensure_user("e2e_lily", "lily12345")
    code, lst = call("GET", "/garments", token=lt)
    check("他人看不到我的衣物", lst["total"] == 0, f"got {lst['total']}")  # type: ignore[index]
    code, _ = call("GET", f"/garments/{gid}", token=lt)
    check("他人直取详情 404", code == 404, f"got {code}")
    code, lc = call("GET", "/tags/categories?scope=garment", token=lt)
    lnames = [c["name"] for c in lc]  # type: ignore[union-attr]
    check("他人有自己的「大类」", "大类" in lnames, f"got {lnames}")
    check(
        "他人的大类不是我的那个",
        next(c["id"] for c in lc if c["name"] == "大类") != cat_id,  # type: ignore[union-attr]
    )
    # 拿我的小类取值去挂到他人的大类上 → 404（跨用户看不见）
    code, _ = call(
        "PATCH", f"/tags/values/{minor_id}", token=lt, json_body={"parent_value_id": outer["id"]}
    )
    check("改他人的标签取值 404", code == 404, f"got {code}")

    print("\n== 9. 删除衣物连带清图 ==")
    code, res = call("DELETE", f"/garments/{gid}", token=token)
    check("删除 200", code == 200, f"got {code} {res}")
    check("报告删除 2 张图", res["deleted_images"] == 2, f"got {res}")  # type: ignore[index]
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8000{small}"))
        check("文件已从磁盘清掉", False, "竟然还能取到")
    except urllib.error.HTTPError as e:
        check("文件已从磁盘清掉（404）", e.code == 404, f"got {e.code}")

    print("\n== 10. 删大类取值 → 小类降级为未归类 ==")
    tmp_major = ensure_value(token, cat_id, "E2E临时大类")
    tmp_minor = ensure_value(token, minor["id"], "E2E临时小类", parent_value_id=tmp_major)
    code, res = call("DELETE", f"/tags/values/{tmp_major}", token=token)
    check("删大类取值 200", code == 200, f"got {code} {res}")
    _, cats3 = call("GET", "/tags/categories?scope=garment", token=token)
    survivor = next(
        v
        for c in cats3  # type: ignore[union-attr]
        if c["name"] == "小类"
        for v in c["values"]
        if v["id"] == tmp_minor
    )
    check("小类取值还在，但父值被清空", survivor["parent_value_id"] is None, f"got {survivor}")
    call("DELETE", f"/tags/values/{tmp_minor}", token=token)  # 收尾

    print(f"\n{'=' * 46}\n通过 {ok_count} 项，失败 {len(fail)} 项")
    if fail:
        for f in fail:
            print(f"  - {f}")
        return 1
    print("批次 2 端到端全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
