"""Worker 主循环：抢占式消费 pending 队列。

**为什么用「抢占」而不是先 SELECT 再 UPDATE**：Worker 可能跑多个副本，两个进程同时
SELECT 到同一件衣物就会算两遍（抠两次图、打两次标）。这里用一条带 WHERE 条件的
UPDATE 当锁 —— 谁的 rowcount 是 1 谁才算抢到，SQLite / PostgreSQL 都成立。

队列载体就是 ``garments.status`` 本身，不引 Redis / MQ：单用户衣橱的量级用不上，
多一套中间件就多一个要运维的东西。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.core.time import now_naive_utc, now_utc
from app.models.garment import Garment, GarmentStatus
from app.worker.pipeline import process_garment, stale_processing_ids

log = logging.getLogger(__name__)

# 本进程是否踩到过「抠图超时」。超时的推理线程取消不掉，只能靠整进程重启清掉，
# 所以 run_once 只负责打标记，由 run_forever 决定是否退出。
_timed_out = False

# 退出码：约定一个非 0 值，方便 `docker inspect` / 日志里一眼认出是「超时自杀」。
EXIT_CODE_TIMEOUT = 75


async def claim_garment(session: AsyncSession, garment_id: int) -> bool:
    """把 pending 抢成 processing。抢不到（已被别人捞走 / 状态不对）返回 False。"""
    result = await session.execute(
        update(Garment)
        .where(Garment.id == garment_id, Garment.status == GarmentStatus.PENDING)
        .values(status=GarmentStatus.PROCESSING, updated_at=now_utc())
    )
    await session.commit()
    return result.rowcount == 1


async def next_pending_ids(session: AsyncSession, limit: int) -> list[int]:
    ids = (
        await session.execute(
            select(Garment.id)
            .where(Garment.status == GarmentStatus.PENDING)
            .order_by(Garment.id)
            .limit(limit)
        )
    ).scalars().all()
    return list(ids)


async def requeue_stuck(session: AsyncSession, minutes: int) -> int:
    """把卡住的 processing 退回 pending，返回回收件数。"""
    ids = await stale_processing_ids(session, minutes)
    for garment_id in ids:
        await session.execute(
            update(Garment)
            .where(Garment.id == garment_id, Garment.status == GarmentStatus.PROCESSING)
            .values(status=GarmentStatus.PENDING, updated_at=now_utc())
        )
    if ids:
        await session.commit()
        log.warning("回收 %s 件卡住的衣物：%s", len(ids), ids)
    return len(ids)


async def _fail(session: AsyncSession, garment_id: int, reason: str) -> None:
    """把一件衣物判死，原因留在 ``ai_suggestion.error`` 里给前端展示。

    同样是**显式 Core UPDATE**：这件衣物刚被 claim 成 processing，靠 ORM 属性赋值
    在「值恰好没变」时会被跳过（见 pipeline.process_garment 里的说明）。
    """
    log.error("衣物 %s 处理失败：%s", garment_id, reason)
    # ★ 两个时间列的时区语义不一样：updated_at 是 timestamptz（要 aware），
    #   processed_at 是 timestamp（要 naive）。混用会让 asyncpg 抛 DataError，
    #   于是「判死」这一步本身写不进库 —— 这件衣物就永远卡在 processing。
    stamped = now_utc()
    processed = now_naive_utc()
    await session.execute(
        update(Garment)
        .where(Garment.id == garment_id)
        .values(
            status=GarmentStatus.FAILED,
            ai_suggestion=json.dumps(
                {
                    "name": None,
                    "tag_value_ids": [],
                    "provider": None,
                    "confidence": None,
                    "error": reason,
                },
                ensure_ascii=False,
            ),
            processed_at=processed,
            updated_at=stamped,
        )
    )
    await session.commit()


async def _idle(seconds: float) -> None:
    """休眠，但对系统时钟跳变免疫。

    容器在宿主机 suspend / 热迁移之后 ``CLOCK_MONOTONIC`` 可能大幅跳变。一次长的
    ``await asyncio.sleep(n)`` 会把唤醒时刻算到很远的未来 —— 进程看着活着、日志也不报错，
    但再也不干活了（CT202 上踩到过，表现就是衣物永久卡在 processing）。
    这里切成小段睡，wall clock 到点就醒；万一 wall clock 被往回调，max_ticks 兜底。
    """
    if seconds <= 0:
        return
    deadline = time.time() + seconds
    step = min(0.25, seconds)
    max_ticks = int(seconds / step) + 8
    for _ in range(max_ticks):
        await asyncio.sleep(step)
        if time.time() >= deadline:
            return


async def run_once(
    session: AsyncSession,
    *,
    limit: int | None = None,
    garment_id: int | None = None,
    matting=None,
    tagger=None,
) -> int:
    """跑一批就返回，返回实际处理的件数。

    ``--once`` 与测试用它；常驻模式循环调用它。
    """
    settings = get_settings()
    batch = limit if limit is not None else settings.worker_batch_size

    if garment_id is not None:
        ids = [garment_id]
    else:
        await requeue_stuck(session, settings.worker_stuck_minutes)
        ids = await next_pending_ids(session, batch)

    handled = 0
    for gid in ids:
        if not await claim_garment(session, gid):
            continue
        try:
            # rembg / torch 偶尔会卡在推理里不返回。硬超时至少让 Worker 继续往下走，
            # 而不是把整个队列堵死（真正卡住的线程只能等进程重启）。
            await asyncio.wait_for(
                process_garment(session, gid, matting, tagger),
                timeout=settings.worker_job_timeout_seconds,
            )
        except asyncio.TimeoutError:
            # 超时那一路的协程是被 cancel 掉的，很可能正卡在某个事务中间 ——
            # 先回滚再 _fail，否则 _fail 自己的 UPDATE 也会撞上「事务已 abort」。
            await session.rollback()
            try:
                await _fail(session, gid, f"处理超时（>{settings.worker_job_timeout_seconds:.0f}s）")
            finally:
                # 哪怕 _fail 自己写库失败，也要打标记：这个线程已经废了，
                # 留在进程里只会继续吃内存，让后面的任务一个个超时。
                global _timed_out
                _timed_out = True
            handled += 1
            # 不再碰这一批的下一件：推理线程取消不掉，接着跑只会再叠一个超时。
            break
        except Exception as exc:  # noqa: BLE001 - 单件失败不能拖垮整个循环
            # ⚠️ 必须回滚：process_garment 能抛出来的多半是**数据库错误**（Python 层的
            #    抠图/打标异常都在 pipeline 里就地兜住了）。PostgreSQL 一旦有一条语句
            #    失败，整个事务就 abort，同一连接上后续任何查询都只会报
            #    「current transaction is aborted」。不回滚的话，_fail 写不下去、
            #    异常继续往上冒，这件衣物会一直卡在 processing 等 30 分钟回收。
            await session.rollback()
            await _fail(session, gid, f"{type(exc).__name__}: {exc}")
        handled += 1
    return handled


async def run_forever(session: AsyncSession, *, poll_seconds: float | None = None) -> None:
    """常驻轮询。收到 SIGTERM / SIGINT 时把手上这批跑完再退。"""
    settings = get_settings()
    poll = poll_seconds if poll_seconds is not None else settings.worker_poll_seconds

    stop = asyncio.Event()

    def _stop(*_args: object) -> None:
        log.info("收到停止信号，跑完当前批次后退出")
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            asyncio.get_running_loop().add_signal_handler(sig, _stop)
        except NotImplementedError:  # Windows 上没有这些 handler，退化为 KeyboardInterrupt
            pass

    log.info("Worker 启动：matting=%s tagger=%s", settings.ai_matting_provider, settings.ai_tagger_provider)
    while not stop.is_set():
        try:
            handled = await run_once(session)
        except Exception:  # noqa: BLE001 - 数据库抖动之类的，睡一觉再来
            log.exception("本轮处理异常，%s 秒后重试", poll)
            # ★ 必须回滚，否则 Worker 会**永久**变成僵尸：PostgreSQL 里一条语句失败
            #   之后事务就 abort 了，同一个 session 上后续任何查询都只会报
            #   「current transaction is aborted, commands ignored until end of
            #   transaction block」。不回滚的话，日志每 poll 秒刷一次同样的异常、
            #   进程看着活着，实际永远干不了活。
            #   最容易踩的场景：compose 同时拉起 api 和 worker，worker 抢在
            #   `alembic upgrade head` 跑完之前就查询 → 「relation does not exist」
            #   → 之后即使迁移完成，这个 session 也再没恢复过。
            await session.rollback()
            await _idle(poll)
        except BaseException as exc:  # SystemExit / CancelledError —— 记一笔再放它走
            log.critical("Worker 收到 %s，退出循环", type(exc).__name__)
            raise
        else:
            if handled == 0:
                await _idle(poll)
        finally:
            # 单例 session 会一直攒着加载过的对象：既吃内存，又留下过期的属性值
            session.expire_all()
            # ★ 踩过超时就不干第二件了：卡住的推理线程取消不掉，继续跑只会
            #   内存叠加 + 队列一件件超时。主动退出（非 0），让 compose 的
            #   `restart: unless-stopped` 拉起一个干净进程；超时那件已判 failed。
            if _timed_out and get_settings().worker_hard_exit_on_timeout:
                log.critical(
                    "抠图/打标超时且线程无法取消，主动退出进程以释放内存（退出码 %s，容器会自动重启）",
                    EXIT_CODE_TIMEOUT,
                )
                os._exit(EXIT_CODE_TIMEOUT)
    log.info("Worker 已停止")
