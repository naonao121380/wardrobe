"""Worker 入口::

    python -m app.worker                 # 常驻轮询
    python -m app.worker --once          # 跑一批就退出（测试 / 手工补跑）
    python -m app.worker --garment-id 7  # 只处理某一件
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from app.db import SessionLocal
from app.worker.refine_api import start_refine_server
from app.worker.runner import run_forever, run_once

log = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="衣橱 AI 流水线 Worker")
    parser.add_argument("--once", action="store_true", help="处理一批就退出，不常驻")
    parser.add_argument("--garment-id", type=int, default=None, help="只处理指定的衣物")
    parser.add_argument("--batch", type=int, default=None, help="一轮最多处理几件")
    parser.add_argument("--poll", type=float, default=None, help="队列空时的休眠秒数")
    parser.add_argument("--verbose", "-v", action="store_true", help="打开 DEBUG 日志")
    return parser.parse_args(argv)


async def _main(args: argparse.Namespace) -> int:
    async with SessionLocal() as session:
        if args.once or args.garment_id is not None:
            handled = await run_once(
                session, limit=args.batch, garment_id=args.garment_id
            )
            print(f"处理 {handled} 件", file=sys.stderr)
            return 0
        # 常驻模式顺带起「框选重抠」的内部接口：API 镜像不带 rembg，这块活只能由
        # 装了模型的进程干（见 app/worker/refine_api.py 的模块注释）。
        # `--once` 不启：一次性补跑不需要这个口子。
        start_refine_server()
        await run_forever(session, poll_seconds=args.poll)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(_main(args))
    except KeyboardInterrupt:
        log.info("已中断")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
