"""运维 CLI。

用法::

    python -m app.cli create-user --username naonao --password 'xxx' --admin
    python -m app.cli list-users
    python -m app.cli reset-password --username naonao --password 'yyy'
"""

import argparse
import asyncio
import getpass
import sys

from sqlalchemy import select

from app.core.security import hash_password
from app.db import SessionLocal
from app.models.user import User


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

    args = parser.parse_args()

    if args.cmd == "list-users":
        return asyncio.run(_list_users())

    password = args.password or getpass.getpass("密码: ")
    if args.cmd == "create-user":
        return asyncio.run(_create_user(args.username, password, args.admin, args.display_name))
    if args.cmd == "reset-password":
        return asyncio.run(_reset_password(args.username, password))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
