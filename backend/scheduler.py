"""
请求优先级队列调度器 —— 区分实时用户请求和后台归档任务

功能特性：
1. 使用 asyncio.PriorityQueue 实现优先级调度
2. 实时用户请求（高优先级）等待时间 < 2 秒
3. 后台归档任务（低优先级）允许更长时间排队
4. 支持任务取消和超时
5. 完整的统计信息

使用示例：
    scheduler = RequestScheduler()
    
    # 实时请求
    result = await scheduler.schedule(
        priority=Priority.REALTIME,
        user_id="user1",
        coro=my_async_task(),
    )
    
    # 后台任务
    result = await scheduler.schedule(
        priority=Priority.BACKGROUND,
        user_id="user2",
        coro=my_async_task(),
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Callable, Coroutine

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    """任务优先级（数值越小优先级越高）。"""
    REALTIME = 0       # 实时用户请求，等待时间 < 2 秒
    BACKGROUND = 1     # 后台归档任务，允许更长时间排队


@dataclass(order=True)
class ScheduledTask:
    """
    调度队列中的任务项。
    
    PriorityQueue 使用 (priority, timestamp, task) 元组排序，
    但为了可读性，使用 dataclass 封装。
    """
    priority: int           # 优先级（0=实时，1=后台）
    timestamp: float        # 入队时间戳
    user_id: str            # 用户 ID
    coro: Coroutine = field(compare=False)       # 要执行的协程
    task_id: str = field(compare=False, default="")  # 任务 ID


class RequestScheduler:
    """
    请求优先级调度器。
    
    使用 asyncio.PriorityQueue 实现，支持：
    - 实时/后台优先级区分
    - 任务排队等待时间监控
    - 统计信息收集
    """

    def __init__(self, maxsize: int = 0):
        """
        Args:
            maxsize: 队列最大长度，0 表示无限制
        """
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=maxsize)
        self._worker_task: asyncio.Task | None = None
        self._running = False

        # 统计信息
        self._stats = {
            "total_scheduled": 0,
            "total_completed": 0,
            "total_failed": 0,
            "total_cancelled": 0,
            "realtime_count": 0,
            "background_count": 0,
            "realtime_max_wait": 0.0,
            "background_max_wait": 0.0,
        }
        self._stats_lock = asyncio.Lock()

    async def start(self):
        """启动调度器工作线程。"""
        if self._running:
            return
        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop())
        logger.info("请求优先级调度器已启动")

    async def stop(self):
        """停止调度器工作线程。"""
        self._running = False
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        logger.info("请求优先级调度器已停止")

    async def schedule(
        self,
        priority: Priority,
        user_id: str,
        coro: Coroutine,
        task_id: str = "",
    ) -> Any:
        """
        提交一个任务到调度队列并等待执行结果。
        
        Args:
            priority: 任务优先级
            user_id: 用户 ID
            coro: 要执行的协程
            task_id: 任务 ID（可选）
        
        Returns:
            协程的执行结果
        
        Raises:
            协程执行过程中抛出的异常
        """
        # 创建 Future 来获取执行结果
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        async def wrapped_coro():
            """包装协程，将结果设置到 Future 中。"""
            try:
                result = await coro
                if not future.done():
                    future.set_result(result)
                return result
            except Exception as e:
                if not future.done():
                    future.set_exception(e)
                raise

        task = ScheduledTask(
            priority=priority.value,
            timestamp=time.monotonic(),
            user_id=user_id,
            coro=wrapped_coro(),
            task_id=task_id,
        )

        # 入队
        await self._queue.put(task)

        # 更新统计
        async with self._stats_lock:
            self._stats["total_scheduled"] += 1
            if priority == Priority.REALTIME:
                self._stats["realtime_count"] += 1
            else:
                self._stats["background_count"] += 1

        # 等待执行结果
        return await future

    async def _worker_loop(self):
        """工作线程：不断从队列中取出任务并执行。"""
        while self._running:
            try:
                # 从队列中获取任务（会阻塞直到有任务可用）
                task: ScheduledTask = await self._queue.get()

                # 计算等待时间
                wait_time = time.monotonic() - task.timestamp

                # 更新等待时间统计
                async with self._stats_lock:
                    if task.priority == Priority.REALTIME:
                        if wait_time > self._stats["realtime_max_wait"]:
                            self._stats["realtime_max_wait"] = wait_time
                    else:
                        if wait_time > self._stats["background_max_wait"]:
                            self._stats["background_max_wait"] = wait_time

                # 执行任务
                try:
                    await task.coro
                    async with self._stats_lock:
                        self._stats["total_completed"] += 1
                except asyncio.CancelledError:
                    async with self._stats_lock:
                        self._stats["total_cancelled"] += 1
                    logger.debug(
                        f"任务被取消: user={task.user_id}, "
                        f"priority={'realtime' if task.priority == 0 else 'background'}, "
                        f"wait={wait_time:.2f}s"
                    )
                except Exception as e:
                    async with self._stats_lock:
                        self._stats["total_failed"] += 1
                    logger.warning(
                        f"任务执行失败: user={task.user_id}, "
                        f"priority={'realtime' if task.priority == 0 else 'background'}, "
                        f"wait={wait_time:.2f}s, error={e}"
                    )
                finally:
                    self._queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"调度器工作线程异常: {e}")
                await asyncio.sleep(0.1)

    @property
    def stats(self) -> dict[str, Any]:
        """获取统计信息快照。"""
        return dict(self._stats)

    @property
    def queue_size(self) -> int:
        """当前队列长度。"""
        return self._queue.qsize()

    def print_stats(self):
        """打印统计信息。"""
        stats = self.stats
        logger.info("=" * 50)
        logger.info("请求优先级调度器统计信息")
        logger.info("=" * 50)
        logger.info(f"  总调度数:       {stats['total_scheduled']}")
        logger.info(f"  已完成:         {stats['total_completed']}")
        logger.info(f"  失败:           {stats['total_failed']}")
        logger.info(f"  取消:           {stats['total_cancelled']}")
        logger.info(f"  实时请求数:     {stats['realtime_count']}")
        logger.info(f"  后台任务数:     {stats['background_count']}")
        logger.info(f"  实时最大等待:   {stats['realtime_max_wait']:.3f}s")
        logger.info(f"  后台最大等待:   {stats['background_max_wait']:.3f}s")
        logger.info(f"  当前队列长度:   {self.queue_size}")
        logger.info("=" * 50)
