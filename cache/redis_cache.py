"""
Redis 异步缓存封装 —— 为 WebTextExtractor 提供跨进程/跨用户共享缓存

功能特性：
1. 封装 redis.asyncio.client.Redis，提供 get/set/delete 接口
2. 连接失败时自动降级到内存 dict（零中断降级）
3. 支持 JSON 序列化/反序列化
4. 不同 TTL：成功页面 7 天，失败页面 10 分钟
5. 线程安全的内存缓存（使用 asyncio.Lock）

使用示例：
    cache = await RedisCache.create()
    await cache.set("key", {"data": 123}, ttl=3600)
    value = await cache.get("key")
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# 默认 Redis 连接 URL
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# 缓存 TTL 常量
CACHE_TTL_SUCCESS = 7 * 24 * 3600       # 成功页面缓存 7 天
CACHE_TTL_FAILURE = 10 * 60              # 失败页面缓存 10 分钟


class MemoryCache:
    """
    内存缓存 —— Redis 不可用时的降级方案。
    使用 dict + asyncio.Lock 保证线程安全。
    """

    def __init__(self):
        self._cache: dict[str, tuple[Any, float]] = {}  # key -> (value, expire_at)
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        """获取缓存值，已过期返回 None。"""
        async with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            value, expire_at = entry
            if expire_at is not None and time.monotonic() > expire_at:
                del self._cache[key]
                return None
            return value

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """设置缓存值。"""
        expire_at = (time.monotonic() + ttl) if ttl is not None else None
        async with self._lock:
            self._cache[key] = (value, expire_at)

    async def delete(self, key: str) -> None:
        """删除缓存项。"""
        async with self._lock:
            self._cache.pop(key, None)

    async def clear(self) -> None:
        """清空缓存。"""
        async with self._lock:
            self._cache.clear()

    @property
    async def size(self) -> int:
        """当前缓存项数量。"""
        async with self._lock:
            # 清理过期项
            now = time.monotonic()
            expired = [k for k, (_, t) in self._cache.items() if t is not None and now > t]
            for k in expired:
                del self._cache[k]
            return len(self._cache)


class RedisCache:
    """
    Redis 异步缓存封装。
    支持自动降级到内存缓存。

    使用方式：
        cache = await RedisCache.create(redis_url="redis://localhost:6379/0")
        await cache.set("key", value)
        data = await cache.get("key")
    """

    def __init__(self):
        self._redis = None
        self._memory: MemoryCache | None = None
        self._use_redis = False
        self._redis_url: str = ""

    @classmethod
    async def create(
        cls,
        redis_url: str | None = None,
    ) -> "RedisCache":
        """
        创建 RedisCache 实例。
        自动尝试连接 Redis，失败则降级到内存缓存。

        Args:
            redis_url: Redis 连接 URL，默认从环境变量 REDIS_URL 获取

        Returns:
            RedisCache 实例
        """
        self = cls()
        self._redis_url = redis_url or os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
        self._memory = MemoryCache()

        try:
            import redis.asyncio as redis_asyncio

            self._redis = redis_asyncio.from_url(
                self._redis_url,
                socket_connect_timeout=3,   # 连接超时 3 秒
                socket_timeout=5,           # 读写超时 5 秒
                decode_responses=True,      # 自动解码为字符串
                retry_on_timeout=False,     # 不重试，快速失败降级
            )
            # 测试连接
            await self._redis.ping()
            self._use_redis = True
            logger.info(f"Redis 缓存已连接: {self._redis_url}")
        except Exception as e:
            logger.warning(f"Redis 连接失败，降级到内存缓存: {e}")
            self._redis = None
            self._use_redis = False

        return self

    async def get(self, key: str) -> Any | None:
        """
        获取缓存值。

        Args:
            key: 缓存键

        Returns:
            缓存的值（反序列化后），不存在或已过期返回 None
        """
        if self._use_redis and self._redis is not None:
            try:
                data = await self._redis.get(key)
                if data is not None:
                    return json.loads(data)
                return None
            except Exception as e:
                logger.warning(f"Redis get 失败，降级到内存: {e}")
                self._use_redis = False

        # 降级到内存缓存
        return await self._memory.get(key)

    async def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
    ) -> None:
        """
        设置缓存值。

        Args:
            key: 缓存键
            value: 要缓存的值（必须是 JSON 可序列化的）
            ttl: 过期时间（秒），None 表示永不过期
        """
        if self._use_redis and self._redis is not None:
            try:
                serialized = json.dumps(value, ensure_ascii=False, default=str)
                if ttl is not None:
                    await self._redis.setex(key, ttl, serialized)
                else:
                    await self._redis.set(key, serialized)
                return
            except Exception as e:
                logger.warning(f"Redis set 失败，降级到内存: {e}")
                self._use_redis = False

        # 降级到内存缓存
        await self._memory.set(key, value, ttl=ttl)

    async def delete(self, key: str) -> None:
        """删除缓存项。"""
        if self._use_redis and self._redis is not None:
            try:
                await self._redis.delete(key)
                return
            except Exception as e:
                logger.warning(f"Redis delete 失败，降级到内存: {e}")
                self._use_redis = False

        await self._memory.delete(key)

    async def clear(self) -> None:
        """清空所有缓存（仅内存缓存，Redis 中只清当前 DB）。"""
        if self._use_redis and self._redis is not None:
            try:
                await self._redis.flushdb()
                return
            except Exception as e:
                logger.warning(f"Redis flushdb 失败，降级到内存: {e}")
                self._use_redis = False

        await self._memory.clear()

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._redis is not None:
            try:
                await self._redis.aclose()
            except Exception:
                pass
            self._redis = None
        self._use_redis = False
        logger.info("Redis 缓存连接已关闭")

    @property
    def is_redis_connected(self) -> bool:
        """Redis 是否已连接。"""
        return self._use_redis

    @property
    async def memory_size(self) -> int:
        """内存缓存项数量。"""
        if self._memory is not None:
            return await self._memory.size
        return 0

    # ============================================================
    # 缓存键工具方法
    # ============================================================

    @staticmethod
    def make_key(url: str) -> str:
        """
        生成缓存键。

        Args:
            url: 目标 URL

        Returns:
            缓存键字符串
        """
        return f"web_cache:{url}"

    @staticmethod
    def get_ttl(is_success: bool) -> int:
        """
        根据请求结果获取 TTL。

        Args:
            is_success: 请求是否成功

        Returns:
            TTL（秒）
        """
        return CACHE_TTL_SUCCESS if is_success else CACHE_TTL_FAILURE
