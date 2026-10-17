"""运维 CLI。

用法::

    python -m app.cli create-user --username naonao --password 'xxx' --admin
    python -m app.cli list-users
    python -m app.cli show-settings
    python -m app.cli reset-password --username naonao --password 'yyy'
    python -m app.cli seed-llm --username naonao
    python -m app.cli reseed-tags --username naonao --yes
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db import SessionLocal
from app.models.user import User
from app.services.tags import (
    DEFAULT_CASCADE,
    describe_defaults,
    reset_tags,
    seed_default_categories,
)


async def _create_user(username: str, password: str, admin: bool, display_name: str | None) -> int:
    async with SessionLocal() as db:
        dup = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if dup is not None:
            print(f"用户已存在：{username}", file=sys.stderr)
            return 1
        db.add(
            User(
                username=username,
                password_hash=hash_password(password),
                display_name=display_name or username,
                is_admin=admin,
            )
        )
        await db.flush()
        new_id = (
            await db.execute(select(User.id).where(User.username == username))
        ).scalar_one()
        await seed_default_categories(db, new_id)
        await db.commit()
    print(f"已创建用户：{username}{'（管理员）' if admin else ''}")
    return 0


async def _reset_password(username: str, password: str) -> int:
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            print(f"用户不存在：{username}", file=sys.stderr)
            return 1
        user.password_hash = hash_password(password)
        await db.commit()
    print(f"已重置密码：{username}")
    return 0


async def _list_users() -> int:
    async with SessionLocal() as db:
        users = (await db.execute(select(User).order_by(User.id))).scalars().all()
    if not users:
        print("(无用户)")
        return 0
    for u in users:
        flag = " [admin]" if u.is_admin else ""
        state = "" if u.is_active else " [已停用]"
        print(f"{u.id}\t{u.username}\t{u.display_name or ''}{flag}{state}")
    return 0


async def _reseed_tags(username: str, scope: str | None = None) -> int:
    """清空该用户的标签体系，按当前默认值重建（衣物不动，只解除标签关联）。

    ``scope`` 只重建一侧：给老用户补搭配侧的「风格 / 色系」而不动衣物标签时用。
    """
    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            print(f"用户不存在：{username}", file=sys.stderr)
            return 1

        stats = await reset_tags(db, user.id, scope)
        await db.commit()

    print(f"已重置 {username} 的标签体系（scope={scope or 'all'}）：")
    print(
        f"  删除 类别 {stats['removed_categories']} / 取值 {stats['removed_values']}，"
        f"解除衣物标签关联 {stats['untagged']} 条、搭配标签关联 "
        f"{stats.get('untagged_outfits', 0)} 条"
    )
    print(f"  重建 类别 {stats['created_categories']} / 取值 {stats['created_values']}")
    current_scope = None
    for scope, name, count in describe_defaults():
        if scope != current_scope:
            print(f"  [{scope}]")
            current_scope = scope
        parent = DEFAULT_CASCADE.get(scope, {}).get(name)
        suffix = f" ← 级联自「{parent}」" if parent else ""
        detail = f"{count} 个取值" if count else "空（等用户自己加）"
        print(f"    {name}（{detail}）{suffix}")
    return 0


async def _seed_llm(username: str, base_url: str | None, api_key: str | None, model: str | None) -> int:
    """把环境变量里的大模型配置写进某个用户的配置。

    批次 6 起大模型配置是**按用户**存的、默认不注入；老部署（把 VLM 配在环境变量里）
    升级后 AI 会静默失效，用这条命令把老配置平移到指定账号上：

        python -m app.cli seed-llm --username admin

    不带参数时读 ``WARDROBE_VLM_BASE_URL`` / ``WARDROBE_VLM_API_KEY`` /
    ``WARDROBE_VLM_MODEL``；显式传参则覆盖环境变量。
    """
    from app.config import get_settings
    from app.services.llm import apply_llm_settings

    s = get_settings()
    base = (base_url or s.vlm_base_url or "").strip()
    key = (api_key if api_key is not None else s.vlm_api_key) or None
    name = (model or s.vlm_model or "").strip()
    if not base or not name:
        print(
            "没有可用的配置：请传 --base-url / --model，或先设好 "
            "WARDROBE_VLM_BASE_URL / WARDROBE_VLM_MODEL",
            file=sys.stderr,
        )
        return 1

    async with SessionLocal() as db:
        user = (
            await db.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            print(f"用户不存在：{username}", file=sys.stderr)
            return 1
        apply_llm_settings(user, base_url=base, model=name, api_key=key, from_admin=True)
        await db.commit()
    print(f"已为 {username} 写入大模型配置：{base} / {name}")
    return 0


async def _show_settings() -> int:
    """看一眼全局设置（注册模式）与邀请码概况。"""
    from app.models.system import InviteCode
    from app.services.system import get_app_settings, is_usable

    async with SessionLocal() as db:
        settings = await get_app_settings(db)
        codes = (await db.execute(select(InviteCode))).scalars().all()
    usable = sum(1 for c in codes if is_usable(c))
    print(f"注册模式：{settings.registration_mode}")
    print(f"邀请码：共 {len(codes)} 枚，可用 {usable} 枚")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="wardrobe", description="衣橱管理系统运维 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_create = sub.add_parser("create-user", help="创建用户")
    p_create.add_argument("--username", required=True)
    p_create.add_argument("--password", default=None)
    p_create.add_argument("--display-name", default=None)
    p_create.add_argument("--admin", action="store_true")

    p_reset = sub.add_parser("reset-password", help="重置密码")
    p_reset.add_argument("--username", required=True)
    p_reset.add_argument("--password", default=None)

    sub.add_parser("list-users", help="列出用户")
    sub.add_parser("show-settings", help="查看注册模式与邀请码概况")

    p_llm = sub.add_parser("seed-llm", help="把大模型配置写入指定用户（老部署平移用）")
    p_llm.add_argument("--username", required=True)
    p_llm.add_argument("--base-url", default=None)
    p_llm.add_argument("--api-key", default=None)
    p_llm.add_argument("--model", default=None)

    p_reseed = sub.add_parser("reseed-tags", help="清空并重建某用户的标签体系")
    p_reseed.add_argument("--username", required=True)
    p_reseed.add_argument("--yes", action="store_true", help="跳过确认")
    p_reseed.add_argument(
        "--scope",
        choices=["garment", "outfit"],
        help="只重建一侧（另一侧连同已打的标签完全不动）",
    )

    args = parser.parse_args()

    if args.cmd == "list-users":
        return asyncio.run(_list_users())

    if args.cmd == "show-settings":
        return asyncio.run(_show_settings())

    if args.cmd == "seed-llm":
        return asyncio.run(_seed_llm(args.username, args.base_url, args.api_key, args.model))

    if args.cmd == "reseed-tags":
        if not args.yes:
            what = "全部" if not args.scope else f"{args.scope} 侧的"
            print(f"这会删掉该用户的{what}标签类别/取值，并解除相应的标签关联。")
            if input("确认？输入 yes 继续: ").strip().lower() != "yes":
                print("已取消")
                return 1
        return asyncio.run(_reseed_tags(args.username, args.scope))

    password = args.password or getpass.getpass("密码: ")
    if args.cmd == "create-user":
        return asyncio.run(_create_user(args.username, password, args.admin, args.display_name))
    if args.cmd == "reset-password":
        return asyncio.run(_reset_password(args.username, password))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
