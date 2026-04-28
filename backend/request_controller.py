"""
请求控制器 —— 为爬虫/数据采集项目提供完整的请求限流方案

功能特性：
1. 用户级任务隔离（UserTaskManager）：每个用户拥有独立的 Semaphore
2. 域名级别的令牌桶限流器（全局共享，保护目标服务器）
3. 请求重试机制（指数退避 + 随机抖动）
4. 请求间隔策略（固定延时 + 随机抖动）
5. 请求失败的降级处理
6. 批次超时熔断：超时后返回部分结果，不抛异常
7. 完整的日志记录

使用示例：
    # 用户级隔离
    manager = UserTaskManager()
    
    # 用户A的批次
    results_a = await manager.run_batch(
        user_id="user_a",
        urls=[...],
        max_concurrent=3,
        batch_timeout=30.0,
    )
    
    # 用户B的批次（完全独立，不受用户A影响）
    results_b = await manager.run_batch(
        user_id="user_b",
        urls=[...],
        max_concurrent=5,
        batch_timeout=60.0,
    )
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# 默认请求头
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
}


# ============================================================
# 1. 配置类
# ============================================================

@dataclass
class RateLimitConfig:
    """域名级别的速率限制配置。"""
    requests_per_second: float = 0.5        # 每秒允许的请求数（极大降低频率防止被封/阻塞）
    burst_size: int = 1                      # 突发请求的令牌桶容量（禁止突发，平稳请求）
    min_interval: float = 5.0                # 同一域名请求的最小间隔（秒）（大幅增大间隔避免线程阻塞和访问拒绝）


@dataclass
class RetryConfig:
    """重试配置。"""
    max_retries: int = 2                     # 最大重试次数（减少重试，避免激化阻塞）
    base_delay: float = 6.0                  # 初始退避延迟（秒）（大幅增大避免过快重试）
    max_delay: float = 120.0                 # 最大退避延迟（秒）（延长最大等待时间）
    jitter_factor: float = 0.5               # 随机抖动因子（0~1）
    retryable_statuses: tuple = (429, 500, 502, 503, 504)  # 可重试的 HTTP 状态码


@dataclass
class RequestControllerConfig:
    """请求控制器全局配置。"""
    max_concurrent: int = 10                 # 全局最大并发数
    default_timeout: float = 30.0            # 请求超时（秒）
    default_rate_limit: RateLimitConfig = field(
        default_factory=lambda: RateLimitConfig()
    )
    retry: RetryConfig = field(
        default_factory=lambda: RetryConfig()
    )
    domain_configs: dict[str, RateLimitConfig] = field(default_factory=dict)
    """域名特定的速率配置，例如：
    {
        "fencing.sport.org.cn": RateLimitConfig(requests_per_second=2, burst_size=3),
        "www.sport.gov.cn": RateLimitConfig(requests_per_second=5, burst_size=10),
    }
    """


# ============================================================
# 2. 令牌桶限流器（域名级别）
# ============================================================

class TokenBucket:
    """
    令牌桶限流器。
    
    以恒定速率向桶中添加令牌，每次请求消耗一个令牌。
    桶满时令牌不再增加，桶空时请求需等待。
    """

    def __init__(self, rate: float, burst: int):
        """
        Args:
            rate: 令牌添加速率（个/秒）
            burst: 桶容量（最大突发请求数）
        """
        self.rate = rate
        self.burst = burst
        self._tokens = float(burst)          # 当前令牌数
        self._last_refill = time.monotonic() # 上次补充令牌的时间
        self._lock = asyncio.Lock()

    async def acquire(self) -> float:
        """
        获取一个令牌。如果桶为空，等待直到有可用令牌。
        
        Returns:
            等待时间（秒）
        """
        async with self._lock:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return 0.0

            # 计算需要等待的时间
            wait_time = (1.0 - self._tokens) / self.rate
            self._tokens = 0.0

        # 释放锁后再等待，避免阻塞其他 acquire 调用
        await asyncio.sleep(wait_time)
        return wait_time

    def _refill(self):
        """补充令牌。"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self.burst, self._tokens + elapsed * self.rate)
        self._last_refill = now

    @property
    def available_tokens(self) -> float:
        """当前可用令牌数（近似值）。"""
        self._refill()
        return self._tokens


# ============================================================
# 3. 域名限流管理器
# ============================================================

class DomainRateLimiter:
    """
    域名级别的限流管理器。
    
    为每个域名维护一个令牌桶，支持不同域名的不同速率配置。
    """

    def __init__(self, default_config: RateLimitConfig, domain_configs: dict[str, RateLimitConfig] = None):
        self._default_config = default_config
        self._domain_configs = domain_configs or {}
        self._buckets: dict[str, TokenBucket] = {}
        self._last_request_time: dict[str, float] = {}  # 域名 -> 上次请求时间
        self._lock = asyncio.Lock()

    def _get_config(self, domain: str) -> RateLimitConfig:
        """获取域名的速率配置。"""
        return self._domain_configs.get(domain, self._default_config)

    def _get_bucket(self, domain: str) -> TokenBucket:
        """获取或创建域名的令牌桶。"""
        if domain not in self._buckets:
            config = self._get_config(domain)
            self._buckets[domain] = TokenBucket(
                rate=config.requests_per_second,
                burst=config.burst_size,
            )
        return self._buckets[domain]

    async def acquire(self, domain: str) -> float:
        """
        获取域名的请求许可。
        
        Returns:
            总等待时间（秒）
        """
        total_wait = 0.0

        # 1. 令牌桶限流
        bucket = self._get_bucket(domain)
        wait1 = await bucket.acquire()
        total_wait += wait1

        # 2. 最小请求间隔
        async with self._lock:
            last_time = self._last_request_time.get(domain, 0.0)
            now = time.monotonic()
            config = self._get_config(domain)
            elapsed = now - last_time
            if elapsed < config.min_interval:
                wait2 = config.min_interval - elapsed
                total_wait += wait2
                await asyncio.sleep(wait2)
            self._last_request_time[domain] = time.monotonic()

        return total_wait


# ============================================================
# 4. 请求重试器
# ============================================================

class RetryHandler:
    """
    请求重试处理器，带指数退避和随机抖动。
    
    退避策略：
        delay = min(base_delay * 2^attempt, max_delay)
        actual_delay = delay * (1 + random.uniform(-jitter, jitter))
    """

    def __init__(self, config: RetryConfig):
        self.config = config

    def should_retry(self, response: httpx.Response | None, exception: Exception | None) -> bool:
        """判断是否应该重试。"""
        if response is not None:
            return response.status_code in self.config.retryable_statuses
        if exception is not None:
            # 网络层面的错误（超时、连接错误等）都应该重试
            return isinstance(exception, (
                httpx.TimeoutException,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
                httpx.ReadError,
                httpx.WriteError,
            ))
        return False

    def get_delay(self, attempt: int) -> float:
        """
        计算第 attempt 次重试的等待时间。
        
        Args:
            attempt: 当前重试次数（从 0 开始）
        
        Returns:
            等待时间（秒）
        """
        delay = min(
            self.config.base_delay * (2 ** attempt),
            self.config.max_delay,
        )
        # 添加随机抖动
        jitter = delay * self.config.jitter_factor
        actual_delay = delay + random.uniform(-jitter, jitter)
        return max(0.1, actual_delay)


# ============================================================
# 5. 请求控制器（主类）
# ============================================================

class RequestController:
    """
    请求控制器 —— 整合并发控制、限流、重试、降级处理。
    
    使用示例：
        controller = RequestController(max_concurrent=5)
        
        # 单个请求
        async with controller.request("https://example.com") as resp:
            data = await resp.text()
        
        # 批量请求
        results = await controller.batch_get(urls)
    """

    def __init__(
        self,
        max_concurrent: int = 3,             # 全局最大并发数（降低避免线程阻塞）
        default_rate: float = 2.0,           # 默认每秒请求数（降低请求频率）
        retry_max: int = 3,                  # 最大重试次数
        timeout: float = 60.0,               # 请求超时（秒）（延长超时防止超时重试风暴）
        max_concurrent_per_domain: int = 2,  # 每个域名最大并发数（新增，防止单一域名阻塞全局）
        domain_configs: dict[str, RateLimitConfig] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        """
        Args:
            max_concurrent: 全局最大并发数
            default_rate: 默认每秒请求数（每个域名）
            retry_max: 最大重试次数
            timeout: 请求超时（秒）
            max_concurrent_per_domain: 每个域名最大并发数（新增，防止单一域名阻塞全局）
            domain_configs: 域名特定的速率配置
            client: 可复用的 httpx.AsyncClient 实例
        """
        config = RequestControllerConfig(
            max_concurrent=max_concurrent,
            default_timeout=timeout,
            default_rate_limit=RateLimitConfig(requests_per_second=default_rate),
            retry=RetryConfig(max_retries=retry_max),
            domain_configs=domain_configs or {},
        )
        self.config = config
        self._max_concurrent_per_domain = max_concurrent_per_domain

        # 全局并发控制
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

        # 域名级并发控制（每个域名独立的信号量，防止单一域名阻塞全局）
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self._domain_semaphores_lock = asyncio.Lock()

        # 域名限流
        self._rate_limiter = DomainRateLimiter(
            default_config=config.default_rate_limit,
            domain_configs=config.domain_configs,
        )

        # 重试处理器
        self._retry_handler = RetryHandler(config.retry)

        # HTTP 客户端（可复用）
        self._client = client

        # 线程池，将实际 HTTP 请求放到独立线程中执行，避免阻塞事件循环
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_concurrent * 2,
            thread_name_prefix="http_worker",
        )

        # 统计信息
        self._stats = {
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "retried": 0,
            "rate_limited": 0,
            "total_wait_time": 0.0,
        }
        self._stats_lock = asyncio.Lock()

    async def _get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """获取或创建域名的信号量（懒加载）。"""
        async with self._domain_semaphores_lock:
            if domain not in self._domain_semaphores:
                self._domain_semaphores[domain] = asyncio.Semaphore(
                    self._max_concurrent_per_domain
                )
            return self._domain_semaphores[domain]

    def _get_domain(self, url: str) -> str:
        """从 URL 中提取域名。"""
        return urlparse(url).hostname or "unknown"

    async def _update_stats(self, **kwargs):
        """更新统计信息。"""
        async with self._stats_lock:
            for key, value in kwargs.items():
                if key in self._stats:
                    self._stats[key] += value

    @property
    def stats(self) -> dict[str, Any]:
        """获取统计信息的快照。"""
        return dict(self._stats)

    def print_stats(self):
        """打印统计信息。"""
        stats = self.stats
        logger.info("=" * 50)
        logger.info("请求控制器统计信息")
        logger.info("=" * 50)
        logger.info(f"  总请求数:     {stats['total_requests']}")
        logger.info(f"  成功:         {stats['successful']}")
        logger.info(f"  失败:         {stats['failed']}")
        logger.info(f"  重试次数:     {stats['retried']}")
        logger.info(f"  被限流次数:   {stats['rate_limited']}")
        logger.info(f"  总等待时间:   {stats['total_wait_time']:.2f}s")
        if stats['total_requests'] > 0:
            success_rate = stats['successful'] / stats['total_requests'] * 100
            logger.info(f"  成功率:       {success_rate:.1f}%")
        logger.info("=" * 50)

    def _do_sync_request(
        self,
        method: str,
        url: str,
        timeout: float,
        **kwargs,
    ) -> httpx.Response:
        """
        在线程池中执行的同步 HTTP 请求。
        将耗时 I/O 操作完全移出事件循环，避免阻塞 FastAPI 主线程。
        """
        # 每次请求使用独立客户端，避免连接池竞争
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            return client.request(method, url, **kwargs)

    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        执行单个 HTTP 请求（包含限流、重试、降级）。
        实际 HTTP I/O 在线程池中执行，完全不阻塞事件循环。
        
        Args:
            method: HTTP 方法（get, post 等）
            url: 请求 URL
            **kwargs: 传递给 httpx 的额外参数
        
        Returns:
            httpx.Response
        
        Raises:
            RequestFailedError: 所有重试都失败后抛出
        """
        domain = self._get_domain(url)
        last_exception = None
        last_response = None

        await self._update_stats(total_requests=1)

        for attempt in range(self.config.retry.max_retries + 1):
            # 如果不是第一次尝试，先执行重试退避等待（不占用信号量）
            if attempt > 0:
                delay = self._retry_handler.get_delay(attempt - 1)
                await asyncio.sleep(delay)

            # ================================================================
            # 1. 域名级并发控制 + 限流等待
            #    - 限流等待在获取全局信号量之前执行
            #    - 防止一个域名的慢请求阻塞全局信号量
            # ================================================================
            domain_sem = await self._get_domain_semaphore(domain)
            async with domain_sem:
                wait_time = await self._rate_limiter.acquire(domain)
                if wait_time > 0:
                    await self._update_stats(rate_limited=1, total_wait_time=wait_time)
                    logger.debug(f"[{domain}] 限流等待 {wait_time:.2f}s (尝试 {attempt + 1})")

                # ================================================================
                # 2. 全局并发控制：执行请求期间占用全局槽位
                # ================================================================
                async with self._semaphore:
                    try:
                        # 3. 通过线程池执行 HTTP 请求，完全避免阻塞事件循环
                        loop = asyncio.get_running_loop()
                        resp = await loop.run_in_executor(
                            self._thread_pool,
                            self._do_sync_request,
                            method,
                            url,
                            self.config.default_timeout,
                        )

                        # 4. 检查是否需要重试
                        if self._retry_handler.should_retry(resp, None):
                            await self._update_stats(retried=1)
                            last_response = resp
                            logger.warning(
                                f"[{domain}] HTTP {resp.status_code} "
                                f"将重试 {attempt + 1}/{self.config.retry.max_retries}: "
                                f"{url[:80]}"
                            )
                            # 退出信号量上下文后执行退避等待
                            continue

                        # 5. 成功
                        await self._update_stats(successful=1)
                        logger.debug(f"[{domain}] 成功 ({resp.status_code}): {url[:80]}")
                        return resp

                    except Exception as e:
                        last_exception = e
                        if self._retry_handler.should_retry(None, e):
                            await self._update_stats(retried=1)
                            logger.warning(
                                f"[{domain}] {type(e).__name__}: {e} "
                                f"将重试 {attempt + 1}/{self.config.retry.max_retries}: "
                                f"{url[:80]}"
                            )
                            # 退出信号量上下文后执行退避等待
                            continue
                        else:
                            # 不可重试的错误，直接抛出
                            await self._update_stats(failed=1)
                            logger.error(f"[{domain}] 不可重试的错误: {e}: {url[:80]}")
                            raise

        # 所有重试都失败
        await self._update_stats(failed=1)
        error_msg = (
            f"请求失败（已重试 {self.config.retry.max_retries} 次）: {url[:80]}"
        )
        if last_response is not None:
            logger.error(f"{error_msg} 最后响应: HTTP {last_response.status_code}")
            return last_response  # 降级：返回最后一次的响应
        if last_exception is not None:
            logger.error(f"{error_msg} 最后异常: {last_exception}")
            raise RequestFailedError(error_msg) from last_exception

        raise RequestFailedError(error_msg)

    # ============================================================
    # 公开 API
    # ============================================================

    async def get(self, url: str, **kwargs) -> httpx.Response:
        """发送 GET 请求。"""
        return await self._make_request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs) -> httpx.Response:
        """发送 POST 请求。"""
        return await self._make_request("POST", url, **kwargs)

    async def request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """发送自定义 HTTP 请求。"""
        return await self._make_request(method, url, **kwargs)

    async def batch_get(
        self,
        urls: list[str],
        concurrency: int | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> list[httpx.Response | None]:
        """
        批量发送 GET 请求。
        
        Args:
            urls: URL 列表
            concurrency: 自定义并发数（默认使用全局配置）
            on_progress: 进度回调函数，参数为 (completed, total)
        
        Returns:
            响应列表，失败的请求返回 None
        """
        semaphore = asyncio.Semaphore(concurrency or self.config.max_concurrent)

        async def _limited_get(url: str) -> httpx.Response | None:
            async with semaphore:
                try:
                    return await self.get(url)
                except Exception as e:
                    logger.error(f"批量请求失败: {url[:80]}: {e}")
                    return None

        # 创建 Task 对象（asyncio.as_completed 和 asyncio.gather 需要 Task，不能传协程）
        coros = [_limited_get(url) for url in urls]
        tasks = [asyncio.create_task(coro) for coro in coros]
        results = []

        if on_progress:
            total = len(tasks)
            for i, coro in enumerate(asyncio.as_completed(tasks)):
                result = await coro
                results.append(result)
                on_progress(i + 1, total)
        else:
            results = await asyncio.gather(*tasks)

        return results

    async def batch_request(
        self,
        requests: list[dict],
        concurrency: int | None = None,
    ) -> list[httpx.Response | None]:
        """
        批量发送自定义请求。
        
        Args:
            requests: 请求配置列表，每项为 {"method": "GET", "url": "...", ...}
            concurrency: 自定义并发数
        
        Returns:
            响应列表
        """
        semaphore = asyncio.Semaphore(concurrency or self.config.max_concurrent)

        async def _limited_request(req: dict) -> httpx.Response | None:
            async with semaphore:
                try:
                    method = req.pop("method", "GET")
                    url = req.pop("url")
                    return await self._make_request(method, url, **req)
                except Exception as e:
                    logger.error(f"批量请求失败: {req.get('url', '?')[:80]}: {e}")
                    return None

        # 创建 Task 对象（asyncio.gather 需要 Task，不能传协程）
        coros = [_limited_request(req.copy()) for req in requests]
        tasks = [asyncio.create_task(coro) for coro in coros]
        return await asyncio.gather(*tasks)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.print_stats()


# ============================================================
# 6. 自定义异常
# ============================================================

class RequestFailedError(Exception):
    """请求失败异常（所有重试都失败后抛出）。"""
    pass


# ============================================================
# 7. 网页文本提取器（与项目 URL 代理功能结合）
# ============================================================

class WebTextExtractor:
    """
    网页文本提取器 —— 从 URL 中提取正文纯文本。
    
    使用 RequestController 进行限流控制，避免被目标网站封禁。
    支持缓存，避免重复请求。
    """

    def __init__(
        self,
        controller: RequestController | None = None,
        max_lines: int = 500,
        max_chars: int = 50000,
    ):
        """
        Args:
            controller: RequestController 实例，不传则使用默认配置
            max_lines: HTML 页面最多提取的行数
            max_chars: 非 HTML 内容最多提取的字符数
        """
        self.controller = controller or RequestController(
            max_concurrent=2,                # 降低并发，避免线程阻塞
            default_rate=2.0,                # 降低请求频率
            retry_max=2,                     # 最多重试 2 次
            timeout=60.0,                    # 延长超时，防止超时重试风暴
        )
        self.max_lines = max_lines
        self.max_chars = max_chars
        self._cache: dict[str, dict] = {}
        self._cache_hits = 0

    async def extract(self, url: str) -> dict:
        """
        提取单个 URL 的网页文本。
        
        Args:
            url: 目标 URL
        
        Returns:
            {"url": ..., "title": ..., "text": ...}
        """
        # 检查缓存
        if url in self._cache:
            self._cache_hits += 1
            logger.info(f"WebTextExtractor 缓存命中: {url[:60]}...")
            return self._cache[url]

        try:
            resp = await self.controller.get(url)
            result = self._parse_response(url, resp)
            self._cache[url] = result
            logger.info(f"WebTextExtractor 成功: {url[:60]}... ({len(result['text'])} 字符)")
            return result
        except Exception as e:
            logger.exception(f"WebTextExtractor 失败: {url}")
            result = {"url": url, "title": "", "text": f"获取失败: {e}"}
            self._cache[url] = result
            return result

    async def extract_batch(self, urls: list[str]) -> list[dict]:
        """
        批量提取多个 URL 的网页文本。
        已缓存的直接返回，未缓存的通过 RequestController 限流获取。
        
        Args:
            urls: URL 列表
        
        Returns:
            [{"url": ..., "title": ..., "text": ...}, ...]
        """
        # 区分已缓存和未缓存的 URL
        cached_results = []
        uncached_urls = []
        for url in urls:
            if url in self._cache:
                self._cache_hits += 1
                cached_results.append(self._cache[url])
            else:
                uncached_urls.append(url)

        # 未缓存的通过 RequestController 批量获取
        if uncached_urls:
            responses = await self.controller.batch_get(uncached_urls)
            for url, resp in zip(uncached_urls, responses):
                if resp is not None:
                    result = self._parse_response(url, resp)
                else:
                    result = {"url": url, "title": "", "text": "获取失败"}
                self._cache[url] = result
                cached_results.append(result)

        # 按原始顺序返回
        url_to_result = {r["url"]: r for r in cached_results}
        return [url_to_result[url] for url in urls]

    def _parse_response(self, url: str, resp: httpx.Response) -> dict:
        """
        解析 HTTP 响应，提取标题和正文。
        
        自动处理编码问题：
        1. 优先使用响应头 Content-Type 中的 charset
        2. 其次从 HTML <meta charset> 或 <meta http-equiv> 中提取
        3. 最后回退到 httpx 自动检测的编码
        """
        from bs4 import BeautifulSoup
        import re

        content_type = resp.headers.get("content-type", "")
        title = ""
        text = ""

        # ============================================================
        # 编码检测与解码
        # ============================================================
        # 1. 从 Content-Type 头中提取 charset
        encoding = None
        charset_match = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
        if charset_match:
            encoding = charset_match.group(1)

        # 2. 获取原始字节内容
        raw_content = resp.content

        # 3. 如果是 HTML，尝试从 <meta> 标签中提取编码
        if "text/html" in content_type or "application/xhtml" in content_type:
            if not encoding:
                # 从 <meta charset="xxx"> 中提取
                meta_charset = re.search(
                    rb'<meta[^>]+charset\s*=\s*["\']?([\w-]+)["\']?',
                    raw_content[:4096],  # 只检查前 4KB
                    re.IGNORECASE
                )
                if meta_charset:
                    encoding = meta_charset.group(1).decode('ascii', errors='ignore')
                else:
                    # 从 <meta http-equiv="Content-Type" content="text/html; charset=xxx"> 中提取
                    meta_http_equiv = re.search(
                        rb'<meta[^>]+http-equiv\s*=\s*["\']?Content-Type["\']?[^>]+charset=([\w-]+)',
                        raw_content[:4096],
                        re.IGNORECASE
                    )
                    if meta_http_equiv:
                        encoding = meta_http_equiv.group(1).decode('ascii', errors='ignore')

        # 4. 使用检测到的编码解码
        if encoding:
            try:
                decoded_text = raw_content.decode(encoding, errors='replace')
            except (LookupError, UnicodeDecodeError):
                # 编码名称不合法或解码失败，回退到 httpx 自动检测
                decoded_text = resp.text
        else:
            # 没有检测到编码，使用 httpx 自动检测的编码
            decoded_text = resp.text

        # ============================================================
        # 正文提取
        # ============================================================
        if "text/html" in content_type or "application/xhtml" in content_type:
            soup = BeautifulSoup(decoded_text, "html.parser")
            if soup.title:
                title = soup.title.get_text(strip=True)
            # 移除非正文元素
            for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                             "noscript", "iframe", "svg", "form", "button"]):
                tag.decompose()
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines[:self.max_lines])
        else:
            text = decoded_text[:self.max_chars]

        return {"url": url, "title": title, "text": text}

    @property
    def stats(self) -> dict:
        """获取统计信息。"""
        base = self.controller.stats
        base["cache_hits"] = self._cache_hits
        base["cache_size"] = len(self._cache)
        return base

    def print_stats(self):
        """打印统计信息。"""
        self.controller.print_stats()
        stats = self.stats
        logger.info(f"  缓存命中:     {stats['cache_hits']}")
        logger.info(f"  缓存大小:     {stats['cache_size']}")


# ============================================================
# 8. 用户级任务管理器（UserTaskManager）
# ============================================================

@dataclass
class BatchResult:
    """
    用户批次请求的结果。
    
    包含部分成功和失败详情，超时后返回已完成的 partial 结果。
    """
    user_id: str
    total: int
    completed: int
    failed: int
    results: list[dict | None]
    """每个 URL 的提取结果，顺序与输入 urls 一致，失败的为 None"""
    errors: list[str | None]
    """每个 URL 的错误信息，成功的为 None"""
    elapsed: float
    """实际耗时（秒）"""
    cancelled: int = 0
    """被取消的任务数（超时取消）"""
    timed_out: bool = False
    partial: bool = False
    """是否为部分结果（超时返回）"""



class UserTaskManager:
    """
    用户级任务管理器 —— 实现多用户并发安全的批量请求隔离。
    
    核心设计：
    1. 每个用户批次拥有独立的 asyncio.Semaphore(3-5)，控制该用户内部并发数
    2. 不同用户的任务互不影响：用户A的10个任务内部排队，不会拖慢用户B的请求
    3. 域名级别的令牌桶（DomainRateLimiter）保持全局共享，保护目标服务器不被封 IP
    4. 请求失败时禁止级联重试风暴，单个用户的重试不挤占其他用户的资源
    5. 每个用户批次设置整体超时，超时后返回已抓取的部分结果并取消剩余任务
    
    使用示例：
        manager = UserTaskManager(domain_configs={...})
        
        # 用户A的批次
        results = await manager.run_batch(
            user_id="user_a",
            urls=[...],
            max_concurrent=3,
            batch_timeout=30.0,
        )
    """

    def __init__(
        self,
        default_rate: float = 2.0,
        retry_max: int = 2,
        request_timeout: float = 20.0,
        max_concurrent_per_domain: int = 2,
        domain_configs: dict[str, RateLimitConfig] | None = None,
        rate_limiter: DomainRateLimiter | None = None,
    ):
        """
        Args:
            default_rate: 默认每秒请求数（每个域名）
            retry_max: 最大重试次数
            request_timeout: 单个 HTTP 请求超时（秒）
            max_concurrent_per_domain: 每个域名最大并发数
            domain_configs: 域名特定的速率配置
            rate_limiter: 外部传入的 DomainRateLimiter 实例（全局共享）。
                         传入后 default_rate 和 domain_configs 参数将被忽略。
        """
        # 全局共享的域名限流器（保护目标服务器）
        # 优先使用外部传入的实例，确保全局共享
        if rate_limiter is not None:
            self._rate_limiter = rate_limiter
        else:
            self._rate_limiter = DomainRateLimiter(
                default_config=RateLimitConfig(requests_per_second=default_rate),
                domain_configs=domain_configs or {},
            )

        # 全局共享的重试处理器
        self._retry_handler = RetryHandler(RetryConfig(max_retries=retry_max))

        # 全局共享的域名级信号量
        self._domain_semaphores: dict[str, asyncio.Semaphore] = {}
        self._domain_semaphores_lock = asyncio.Lock()
        self._max_concurrent_per_domain = max_concurrent_per_domain

        # 线程池（全局共享）
        self._thread_pool = ThreadPoolExecutor(
            max_workers=20,
            thread_name_prefix="http_worker",
        )

        # 请求超时
        self._request_timeout = request_timeout

        # 统计信息
        self._stats = {
            "total_batches": 0,
            "total_requests": 0,
            "successful": 0,
            "failed": 0,
            "retried": 0,
            "timed_out_batches": 0,
        }
        self._stats_lock = asyncio.Lock()

    async def _get_domain_semaphore(self, domain: str) -> asyncio.Semaphore:
        """获取或创建域名的信号量（懒加载，全局共享）。"""
        async with self._domain_semaphores_lock:
            if domain not in self._domain_semaphores:
                self._domain_semaphores[domain] = asyncio.Semaphore(
                    self._max_concurrent_per_domain
                )
            return self._domain_semaphores[domain]

    def _get_domain(self, url: str) -> str:
        """从 URL 中提取域名。"""
        return urlparse(url).hostname or "unknown"

    def _do_sync_request(
        self,
        method: str,
        url: str,
        timeout: float,
    ) -> httpx.Response:
        """
        在线程池中执行的同步 HTTP 请求。
        """
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers=DEFAULT_HEADERS,
        ) as client:
            return client.request(method, url)

    async def _make_request(
        self,
        url: str,
        user_semaphore: asyncio.Semaphore,
        is_cancelled: asyncio.Event,
    ) -> httpx.Response | None:
        """
        执行单个 HTTP 请求（用户级隔离 + 全局域名限流）。
        
        Args:
            url: 请求 URL
            user_semaphore: 用户级信号量
            is_cancelled: 取消事件，被取消时返回 None
        
        Returns:
            httpx.Response 或 None（被取消时）
        """
        domain = self._get_domain(url)
        last_exception = None
        last_response = None

        async with self._stats_lock:
            self._stats["total_requests"] += 1

        for attempt in range(self._retry_handler.config.max_retries + 1):
            # 检查是否被取消
            if is_cancelled.is_set():
                return None

            # 重试退避（不占用用户信号量）
            if attempt > 0:
                delay = self._retry_handler.get_delay(attempt - 1)
                # 分段等待，期间可响应取消
                waited = 0.0
                while waited < delay:
                    if is_cancelled.is_set():
                        return None
                    wait_step = min(0.5, delay - waited)
                    await asyncio.sleep(wait_step)
                    waited += wait_step

            # ================================================================
            # 1. 域名级并发控制 + 限流等待（全局共享）
            # ================================================================
            domain_sem = await self._get_domain_semaphore(domain)
            async with domain_sem:
                # 域名令牌桶限流（全局共享）
                await self._rate_limiter.acquire(domain)

                # ================================================================
                # 2. 用户级并发控制（用户独立）
                # ================================================================
                async with user_semaphore:
                    # 再次检查取消
                    if is_cancelled.is_set():
                        return None

                    try:
                        loop = asyncio.get_running_loop()
                        resp = await loop.run_in_executor(
                            self._thread_pool,
                            self._do_sync_request,
                            "GET",
                            url,
                            self._request_timeout,
                        )

                        # 检查是否需要重试
                        if self._retry_handler.should_retry(resp, None):
                            async with self._stats_lock:
                                self._stats["retried"] += 1
                            last_response = resp
                            logger.warning(
                                f"[用户级] [{domain}] HTTP {resp.status_code} "
                                f"将重试 {attempt + 1}/{self._retry_handler.config.max_retries}: "
                                f"{url[:80]}"
                            )
                            continue

                        # 成功
                        async with self._stats_lock:
                            self._stats["successful"] += 1
                        return resp

                    except Exception as e:
                        last_exception = e
                        if self._retry_handler.should_retry(None, e):
                            async with self._stats_lock:
                                self._stats["retried"] += 1
                            logger.warning(
                                f"[用户级] [{domain}] {type(e).__name__}: {e} "
                                f"将重试 {attempt + 1}/{self._retry_handler.config.max_retries}: "
                                f"{url[:80]}"
                            )
                            continue
                        else:
                            # 不可重试的错误
                            async with self._stats_lock:
                                self._stats["failed"] += 1
                            logger.error(
                                f"[用户级] [{domain}] 不可重试的错误: {e}: {url[:80]}"
                            )
                            return None

        # 所有重试都失败
        async with self._stats_lock:
            self._stats["failed"] += 1
        error_msg = f"请求失败（已重试 {self._retry_handler.config.max_retries} 次）: {url[:80]}"
        if last_response is not None:
            logger.error(f"{error_msg} 最后响应: HTTP {last_response.status_code}")
            return last_response  # 降级返回
        if last_exception is not None:
            logger.error(f"{error_msg} 最后异常: {last_exception}")
        return None

    async def run_batch(
        self,
        user_id: str,
        urls: list[str],
        max_concurrent: int = 3,
        batch_timeout: float = 30.0,
    ) -> BatchResult:
        """
        执行用户批次的批量请求。
        
        特性：
        - 每个用户批次拥有独立的 Semaphore
        - 支持整体超时熔断，超时后返回部分结果
        - 被取消的任务不触发重试
        - 错误只在当前用户范围内传播
        
        Args:
            user_id: 用户 ID
            urls: URL 列表
            max_concurrent: 该用户批次的最大并发数（3-5 推荐）
            batch_timeout: 批次整体超时（秒），默认 30 秒
        
        Returns:
            BatchResult: 包含部分成功和失败详情
        """
        start_time = time.monotonic()
        total = len(urls)

        async with self._stats_lock:
            self._stats["total_batches"] += 1

        logger.info(
            f"[用户={user_id}] 开始批量请求: {total} 个 URL, "
            f"并发={max_concurrent}, 超时={batch_timeout}s"
        )

        # 用户级信号量（独立于其他用户）
        user_semaphore = asyncio.Semaphore(max_concurrent)

        # 取消事件
        is_cancelled = asyncio.Event()

        # 创建所有任务
        async def _task_wrapper(url: str, idx: int) -> tuple[int, httpx.Response | None, str | None]:
            """包装任务，返回 (index, response, error_message)。"""
            try:
                resp = await self._make_request(url, user_semaphore, is_cancelled)
                if resp is not None:
                    return idx, resp, None
                else:
                    return idx, None, f"请求失败: {url[:80]}"
            except asyncio.CancelledError:
                # 被取消的任务返回 "cancelled" 标记，不记录 error 日志
                return idx, None, "cancelled"
            except Exception as e:
                return idx, None, f"未知错误: {e}"

        # 创建 Task 对象（asyncio.wait 需要 Task，不能传协程）
        coros = [_task_wrapper(url, i) for i, url in enumerate(urls)]
        tasks = [asyncio.create_task(coro) for coro in coros]

        # 使用 asyncio.wait 实现超时熔断
        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=batch_timeout,
                return_when=asyncio.ALL_COMPLETED,
            )
        except Exception as e:
            logger.error(f"[用户={user_id}] asyncio.wait 异常: {e}")
            done = set()
            pending = set(tasks)

        # 超时处理
        timed_out = len(pending) > 0
        if timed_out:
            async with self._stats_lock:
                self._stats["timed_out_batches"] += 1

            # 设置取消事件，通知还在运行的任务停止
            is_cancelled.set()

            # 取消所有 pending 任务
            for task in pending:
                task.cancel()

            # 等待所有 pending 任务完成取消
            if pending:
                await asyncio.wait(pending, timeout=5.0)

            logger.warning(
                f"[用户={user_id}] 批次超时: {batch_timeout}s, "
                f"已完成 {len(done)}/{total}, 已取消 {len(pending)}"
            )

        # 收集结果
        results_map: dict[int, tuple[httpx.Response | None, str | None]] = {}

        # 处理已完成的任务
        for task in done:
            try:
                idx, resp, error = await task
                results_map[idx] = (resp, error)
            except Exception as e:
                # 任务本身可能已经失败
                pass

        # 处理被取消的任务
        for task in pending:
            try:
                idx, resp, error = await task
                results_map[idx] = (resp, error)
            except (asyncio.CancelledError, Exception):
                pass

        # 按原始顺序构建结果
        final_results: list[dict | None] = []
        final_errors: list[str | None] = []
        completed_count = 0
        failed_count = 0
        cancelled_count = 0

        for i in range(total):
            if i in results_map:
                resp, error = results_map[i]
                if resp is not None and error is None:
                    completed_count += 1
                    final_results.append({"url": urls[i], "response": resp})
                    final_errors.append(None)
                elif error == "cancelled":
                    # 被取消的任务单独统计
                    cancelled_count += 1
                    final_results.append(None)
                    final_errors.append("cancelled")
                else:
                    failed_count += 1
                    final_results.append(None)
                    final_errors.append(error or f"未知错误: {urls[i][:80]}")
            else:
                # 任务完全丢失（极低概率）
                failed_count += 1
                final_results.append(None)
                final_errors.append(f"任务丢失: {urls[i][:80]}")

        elapsed = time.monotonic() - start_time

        logger.info(
            f"[用户={user_id}] 批次完成: "
            f"成功={completed_count}, 失败={failed_count}, "
            f"取消={cancelled_count}, 超时={timed_out}, 耗时={elapsed:.2f}s"
        )

        return BatchResult(
            user_id=user_id,
            total=total,
            completed=completed_count,
            failed=failed_count,
            cancelled=cancelled_count,
            timed_out=timed_out,
            partial=timed_out,
            results=final_results,
            errors=final_errors,
            elapsed=elapsed,
        )

    @property
    def stats(self) -> dict[str, Any]:
        """获取统计信息快照。"""
        return dict(self._stats)

    def print_stats(self):
        """打印统计信息。"""
        stats = self.stats
        logger.info("=" * 50)
        logger.info("用户级任务管理器统计信息")
        logger.info("=" * 50)
        logger.info(f"  总批次:       {stats['total_batches']}")
        logger.info(f"  总请求数:     {stats['total_requests']}")
        logger.info(f"  成功:         {stats['successful']}")
        logger.info(f"  失败:         {stats['failed']}")
        logger.info(f"  重试次数:     {stats['retried']}")
        logger.info(f"  超时批次:     {stats['timed_out_batches']}")
        if stats['total_requests'] > 0:
            success_rate = stats['successful'] / stats['total_requests'] * 100
            logger.info(f"  成功率:       {success_rate:.1f}%")
        logger.info("=" * 50)


# ============================================================
# 9. 增强版 WebTextExtractor（集成 Redis 缓存）
# ============================================================

class CachedWebTextExtractor:
    """
    增强版网页文本提取器 —— 集成 Redis 缓存 + 用户级任务隔离。
    
    功能特性：
    1. 优先读 Redis 缓存，命中直接返回
    2. 未命中再走网络请求（通过 UserTaskManager）
    3. 缓存过期时间：成功页面 7 天，失败页面 10 分钟
    4. Redis 不可用时自动降级到内存 dict
    5. 支持用户级任务隔离
    """

    def __init__(
        self,
        task_manager: UserTaskManager | None = None,
        redis_cache: Any | None = None,
        max_lines: int = 500,
        max_chars: int = 50000,
    ):
        """
        Args:
            task_manager: UserTaskManager 实例，不传则使用默认配置
            redis_cache: RedisCache 实例，不传则不使用 Redis 缓存
            max_lines: HTML 页面最多提取的行数
            max_chars: 非 HTML 内容最多提取的字符数
        """
        self.task_manager = task_manager or UserTaskManager()
        self.redis_cache = redis_cache
        self.max_lines = max_lines
        self.max_chars = max_chars

        # 内存缓存（Redis 降级方案 + 二次缓存）
        self._memory_cache: dict[str, dict] = {}
        self._cache_hits = 0
        self._memory_hits = 0

    async def extract(
        self,
        url: str,
        user_id: str = "default",
        task_manager: UserTaskManager | None = None,
    ) -> dict:
        """
        提取单个 URL 的网页文本（带 Redis 缓存）。
        
        缓存优先级：Redis > 内存 > 网络请求
        
        Args:
            url: 目标 URL
            user_id: 用户 ID（用于统计）
            task_manager: 自定义 UserTaskManager 实例，不传则使用 self.task_manager
        
        Returns:
            {"url": ..., "title": ..., "text": ..., "cached": bool}
        """
        cache_key = f"web_cache:{url}"

        # ============================================================
        # 1. 尝试 Redis 缓存
        # ============================================================
        if self.redis_cache is not None:
            try:
                cached = await self.redis_cache.get(cache_key)
                if cached is not None:
                    self._cache_hits += 1
                    logger.info(f"[Redis缓存命中] {url[:60]}...")
                    return cached
            except Exception:
                pass

        # ============================================================
        # 2. 尝试内存缓存
        # ============================================================
        if url in self._memory_cache:
            self._memory_hits += 1
            logger.info(f"[内存缓存命中] {url[:60]}...")
            result = self._memory_cache[url]
            result["cached"] = True
            return result

        # ============================================================
        # 3. 网络请求
        # ============================================================
        # 使用传入的 task_manager，或回退到 self.task_manager
        tm = task_manager or self.task_manager
        if tm is None:
            tm = UserTaskManager()
        try:
            batch_result = await tm.run_batch(
                user_id=user_id,
                urls=[url],
                max_concurrent=1,
                batch_timeout=tm._request_timeout + 10,
            )

            if batch_result.completed > 0 and batch_result.results[0] is not None:
                resp = batch_result.results[0]["response"]
                result = self._parse_response(url, resp)
                result["cached"] = False
            else:
                error_msg = batch_result.errors[0] if batch_result.errors else "获取失败"
                result = {
                    "url": url,
                    "title": "",
                    "text": f"获取失败: {error_msg}",
                    "cached": False,
                }
        except Exception as e:
            logger.exception(f"WebTextExtractor 失败: {url}")
            result = {
                "url": url,
                "title": "",
                "text": f"获取失败: {e}",
                "cached": False,
            }

        # ============================================================
        # 4. 写入缓存
        # ============================================================
        is_success = not result.get("text", "").startswith("获取失败")
        ttl = 7 * 24 * 3600 if is_success else 10 * 60  # 成功7天，失败10分钟

        # 写入内存缓存
        self._memory_cache[url] = result

        # 写入 Redis 缓存
        if self.redis_cache is not None:
            try:
                await self.redis_cache.set(cache_key, result, ttl=ttl)
            except Exception:
                pass

        return result

    async def extract_batch(
        self,
        urls: list[str],
        user_id: str = "default",
        max_concurrent: int = 3,
        batch_timeout: float = 30.0,
        task_manager: UserTaskManager | None = None,
    ) -> list[dict]:
        """
        批量提取多个 URL 的网页文本。
        
        已缓存的直接返回，未缓存的通过 UserTaskManager 限流获取。
        
        Args:
            urls: URL 列表
            user_id: 用户 ID
            max_concurrent: 该用户批次的最大并发数
            batch_timeout: 批次整体超时（秒）
            task_manager: 自定义 UserTaskManager 实例，不传则使用 self.task_manager
        
        Returns:
            [{"url": ..., "title": ..., "text": ..., "cached": bool}, ...]
        """
        # 区分已缓存和未缓存的 URL
        cached_results: dict[str, dict] = {}
        uncached_urls: list[str] = []

        for url in urls:
            cache_key = f"web_cache:{url}"
            found = False

            # 尝试 Redis 缓存
            if self.redis_cache is not None:
                try:
                    cached = await self.redis_cache.get(cache_key)
                    if cached is not None:
                        self._cache_hits += 1
                        cached["cached"] = True
                        cached_results[url] = cached
                        found = True
                except Exception:
                    pass

            if not found:
                # 尝试内存缓存
                if url in self._memory_cache:
                    self._memory_hits += 1
                    result = self._memory_cache[url]
                    result["cached"] = True
                    cached_results[url] = result
                    found = True

            if not found:
                uncached_urls.append(url)

        # 未缓存的通过 UserTaskManager 批量获取
        # 使用传入的 task_manager，或回退到 self.task_manager
        tm = task_manager or self.task_manager
        if tm is None:
            tm = UserTaskManager()
        if uncached_urls:
            batch_result = await tm.run_batch(
                user_id=user_id,
                urls=uncached_urls,
                max_concurrent=max_concurrent,
                batch_timeout=batch_timeout,
            )

            for i, url in enumerate(uncached_urls):
                if i < len(batch_result.results) and batch_result.results[i] is not None:
                    resp = batch_result.results[i]["response"]
                    result = self._parse_response(url, resp)
                    result["cached"] = False
                else:
                    error_msg = (
                        batch_result.errors[i]
                        if i < len(batch_result.errors) and batch_result.errors[i]
                        else "获取失败"
                    )
                    result = {
                        "url": url,
                        "title": "",
                        "text": f"获取失败: {error_msg}",
                        "cached": False,
                    }

                # 写入缓存
                is_success = not result.get("text", "").startswith("获取失败")
                ttl = 7 * 24 * 3600 if is_success else 10 * 60
                self._memory_cache[url] = result
                if self.redis_cache is not None:
                    try:
                        await self.redis_cache.set(
                            f"web_cache:{url}", result, ttl=ttl
                        )
                    except Exception:
                        pass

                cached_results[url] = result

        # 按原始顺序返回
        return [cached_results[url] for url in urls]

    def _parse_response(self, url: str, resp: httpx.Response) -> dict:
        """
        解析 HTTP 响应，提取标题和正文。
        与原始 WebTextExtractor 相同的解析逻辑。
        """
        from bs4 import BeautifulSoup
        import re

        content_type = resp.headers.get("content-type", "")
        title = ""
        text = ""

        # 编码检测与解码
        encoding = None
        charset_match = re.search(r'charset=([\w-]+)', content_type, re.IGNORECASE)
        if charset_match:
            encoding = charset_match.group(1)

        raw_content = resp.content

        if "text/html" in content_type or "application/xhtml" in content_type:
            if not encoding:
                meta_charset = re.search(
                    rb'<meta[^>]+charset\s*=\s*["\']?([\w-]+)["\']?',
                    raw_content[:4096],
                    re.IGNORECASE
                )
                if meta_charset:
                    encoding = meta_charset.group(1).decode('ascii', errors='ignore')
                else:
                    meta_http_equiv = re.search(
                        rb'<meta[^>]+http-equiv\s*=\s*["\']?Content-Type["\']?[^>]+charset=([\w-]+)',
                        raw_content[:4096],
                        re.IGNORECASE
                    )
                    if meta_http_equiv:
                        encoding = meta_http_equiv.group(1).decode('ascii', errors='ignore')

        if encoding:
            try:
                decoded_text = raw_content.decode(encoding, errors='replace')
            except (LookupError, UnicodeDecodeError):
                decoded_text = resp.text
        else:
            decoded_text = resp.text

        # 正文提取
        if "text/html" in content_type or "application/xhtml" in content_type:
            soup = BeautifulSoup(decoded_text, "html.parser")
            if soup.title:
                title = soup.title.get_text(strip=True)
            for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                             "noscript", "iframe", "svg", "form", "button"]):
                tag.decompose()
            body = soup.find("body")
            if body:
                text = body.get_text(separator="\n", strip=True)
            else:
                text = soup.get_text(separator="\n", strip=True)
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            text = "\n".join(lines[:self.max_lines])
        else:
            text = decoded_text[:self.max_chars]

        return {"url": url, "title": title, "text": text}

    @property
    def stats(self) -> dict:
        """获取统计信息。"""
        return {
            "redis_cache_hits": self._cache_hits,
            "memory_cache_hits": self._memory_hits,
            "memory_cache_size": len(self._memory_cache),
            "task_manager": self.task_manager.stats,
        }

    def print_stats(self):
        """打印统计信息。"""
        self.task_manager.print_stats()
        stats = self.stats
        logger.info(f"  Redis 缓存命中: {stats['redis_cache_hits']}")
        logger.info(f"  内存缓存命中:   {stats['memory_cache_hits']}")
        logger.info(f"  内存缓存大小:   {stats['memory_cache_size']}")


# ============================================================
# 10. 使用示例
# ============================================================

async def demo():
    """演示如何使用 UserTaskManager 和 CachedWebTextExtractor。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # 示例 1：使用 UserTaskManager 进行用户级隔离
    manager = UserTaskManager(
        default_rate=2.0,
        retry_max=2,
        request_timeout=20.0,
        domain_configs={
            "httpbin.org": RateLimitConfig(
                requests_per_second=5,
                burst_size=3,
                min_interval=0.5,
            ),
        },
    )

    # 用户A的批次
    print("\n=== 用户A 批次 ===")
    result_a = await manager.run_batch(
        user_id="user_a",
        urls=[
            "https://httpbin.org/delay/1",
            "https://httpbin.org/delay/2",
            "https://httpbin.org/delay/1",
        ],
        max_concurrent=3,
        batch_timeout=30.0,
    )
    print(f"用户A: {result_a.completed}/{result_a.total} 成功, 超时={result_a.timed_out}")

    # 用户B的批次（完全独立）
    print("\n=== 用户B 批次 ===")
    result_b = await manager.run_batch(
        user_id="user_b",
        urls=[
            "https://httpbin.org/delay/3",
            "https://httpbin.org/delay/1",
        ],
        max_concurrent=2,
        batch_timeout=10.0,  # 短超时
    )
    print(f"用户B: {result_b.completed}/{result_b.total} 成功, 超时={result_b.timed_out}")

    manager.print_stats()

    # 示例 2：使用 CachedWebTextExtractor
    print("\n=== CachedWebTextExtractor ===")
    extractor = CachedWebTextExtractor(task_manager=manager)
    result = await extractor.extract("https://httpbin.org/get", user_id="user_c")
    print(f"提取结果: title={result.get('title', '')}, text_len={len(result.get('text', ''))}")


if __name__ == "__main__":
    asyncio.run(demo())
