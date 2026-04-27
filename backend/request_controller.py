"""
请求控制器 —— 为爬虫/数据采集项目提供完整的请求限流方案

功能特性：
1. 并发数控制（Semaphore）
2. 请求重试机制（指数退避 + 随机抖动）
3. 请求间隔策略（固定延时 + 随机抖动）
4. 域名级别的令牌桶限流器
5. 请求失败的降级处理
6. 完整的日志记录

使用示例：
    controller = RequestController(
        max_concurrent=5,          # 最大并发数
        default_rate=10,           # 默认每秒请求数
        retry_max=3,               # 最大重试次数
    )

    # 方式一：作为上下文管理器使用
    async with controller.request("https://example.com/api") as resp:
        data = await resp.json()

    # 方式二：直接获取响应
    resp = await controller.get("https://example.com/api")
    data = resp.json()

    # 方式三：批量请求
    results = await controller.batch_get([
        "https://example.com/page/1",
        "https://example.com/page/2",
    ])
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# ============================================================
# 1. 配置类
# ============================================================

@dataclass
class RateLimitConfig:
    """域名级别的速率限制配置。"""
    requests_per_second: float = 10.0       # 每秒允许的请求数
    burst_size: int = 5                      # 突发请求的令牌桶容量
    min_interval: float = 0.05               # 同一域名请求的最小间隔（秒）


@dataclass
class RetryConfig:
    """重试配置。"""
    max_retries: int = 3                     # 最大重试次数
    base_delay: float = 1.0                  # 初始退避延迟（秒）
    max_delay: float = 30.0                  # 最大退避延迟（秒）
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
        max_concurrent: int = 10,
        default_rate: float = 10.0,
        retry_max: int = 3,
        timeout: float = 30.0,
        domain_configs: dict[str, RateLimitConfig] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        """
        Args:
            max_concurrent: 全局最大并发数
            default_rate: 默认每秒请求数（每个域名）
            retry_max: 最大重试次数
            timeout: 请求超时（秒）
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

        # 并发控制
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

        # 域名限流
        self._rate_limiter = DomainRateLimiter(
            default_config=config.default_rate_limit,
            domain_configs=config.domain_configs,
        )

        # 重试处理器
        self._retry_handler = RetryHandler(config.retry)

        # HTTP 客户端（可复用）
        self._client = client

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

    async def _make_request(
        self,
        method: str,
        url: str,
        **kwargs,
    ) -> httpx.Response:
        """
        执行单个 HTTP 请求（包含限流、重试、降级）。
        
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
            # 1. 域名级别限流
            wait_time = await self._rate_limiter.acquire(domain)
            if wait_time > 0:
                await self._update_stats(rate_limited=1, total_wait_time=wait_time)
                logger.debug(f"[{domain}] 限流等待 {wait_time:.2f}s (尝试 {attempt + 1})")

            # 2. 并发控制
            async with self._semaphore:
                try:
                    # 3. 执行请求
                    if self._client is not None:
                        resp = await self._client.request(method, url, **kwargs)
                    else:
                        async with httpx.AsyncClient(
                            timeout=self.config.default_timeout,
                            follow_redirects=True,
                        ) as client:
                            resp = await client.request(method, url, **kwargs)

                    # 4. 检查是否需要重试
                    if self._retry_handler.should_retry(resp, None):
                        await self._update_stats(retried=1)
                        delay = self._retry_handler.get_delay(attempt)
                        logger.warning(
                            f"[{domain}] HTTP {resp.status_code} "
                            f"重试 {attempt + 1}/{self.config.retry.max_retries} "
                            f"等待 {delay:.1f}s: {url[:80]}"
                        )
                        await asyncio.sleep(delay)
                        last_response = resp
                        continue

                    # 5. 成功
                    await self._update_stats(successful=1)
                    logger.debug(f"[{domain}] 成功 ({resp.status_code}): {url[:80]}")
                    return resp

                except Exception as e:
                    last_exception = e
                    if self._retry_handler.should_retry(None, e):
                        await self._update_stats(retried=1)
                        delay = self._retry_handler.get_delay(attempt)
                        logger.warning(
                            f"[{domain}] {type(e).__name__}: {e} "
                            f"重试 {attempt + 1}/{self.config.retry.max_retries} "
                            f"等待 {delay:.1f}s: {url[:80]}"
                        )
                        await asyncio.sleep(delay)
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

        tasks = [_limited_get(url) for url in urls]
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

        tasks = [_limited_request(req.copy()) for req in requests]
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
            max_concurrent=3,
            default_rate=5.0,
            retry_max=2,
            timeout=30.0,
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
# 8. 使用示例
# ============================================================

async def demo():
    """演示如何使用 RequestController。"""
    import httpx

    # 配置日志
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    # 创建控制器
    controller = RequestController(
        max_concurrent=3,                    # 最多 3 个并发
        default_rate=5.0,                    # 每个域名每秒最多 5 个请求
        retry_max=2,                         # 最多重试 2 次
        timeout=15.0,                        # 超时 15 秒
        domain_configs={
            "fencing.sport.org.cn": RateLimitConfig(
                requests_per_second=2,       # 这个域名每秒最多 2 个请求
                burst_size=3,                # 突发容量 3
                min_interval=0.5,            # 最小间隔 0.5 秒
            ),
            "www.sport.gov.cn": RateLimitConfig(
                requests_per_second=3,
                burst_size=5,
            ),
        },
    )

    # 示例 1：单个请求
    try:
        resp = await controller.get("https://httpbin.org/get")
        print(f"单个请求成功: HTTP {resp.status_code}")
    except Exception as e:
        print(f"单个请求失败: {e}")

    # 示例 2：批量请求
    urls = [
        "https://httpbin.org/delay/1",
        "https://httpbin.org/delay/2",
        "https://httpbin.org/delay/1",
    ]
    results = await controller.batch_get(
        urls,
        on_progress=lambda done, total: print(f"进度: {done}/{total}"),
    )
    print(f"批量请求完成: {sum(1 for r in results if r is not None)}/{len(results)} 成功")

    # 示例 3：使用上下文管理器
    async with RequestController(max_concurrent=5) as ctrl:
        resp = await ctrl.get("https://httpbin.org/get")
        print(f"上下文管理器请求: HTTP {resp.status_code}")

    # 示例 4：打印统计信息
    controller.print_stats()


if __name__ == "__main__":
    asyncio.run(demo())
