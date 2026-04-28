"""
启动脚本 —— 一键启动生产服务器

用法：
    python start_server.py              # 默认 0.0.0.0:8000
    python start_server.py --port 8080   # 自定义端口
    python start_server.py --host 127.0.0.1  # 仅本地访问
    python start_server.py --no-redis    # 不启动 Redis
"""

import argparse
import logging
import os
import subprocess
import sys
import time
from pathlib import Path

# 确保项目根目录在 Python 路径中
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


def _find_redis_service() -> Path | None:
    """
    从项目根目录向下查找 RedisService.exe。
    
    查找路径：BASE_DIR / "Redis" / "RedisService.exe"
    
    Returns:
        Path | None: RedisService.exe 的路径，如果未找到则返回 None
    """
    redis_service_path = BASE_DIR / "Redis" / "RedisService.exe"
    if redis_service_path.exists():
        logger.info(f"🔍 找到 Redis 服务: {redis_service_path}")
        return redis_service_path
    logger.warning(f"⚠️ 未找到 Redis 服务: {redis_service_path}")
    return None


def _try_start_redis() -> bool:
    """
    尝试启动本地 Redis 服务。
    
    检测策略：
    1. 先检查环境变量 REDIS_URL（用户自行管理的外部 Redis）
    2. 从项目根目录查找 Redis/RedisService.exe
    3. 如果找到则启动它
    4. 如果 Redis 已经在运行（端口占用），视为成功
    5. 如果启动失败，记录警告但不阻止服务器启动
    
    Returns:
        bool: Redis 是否成功启动
    """
    # 检查环境变量中是否已指定 Redis URL（用户自行管理）
    if os.environ.get("REDIS_URL"):
        logger.info(f"📡 使用外部 Redis: {os.environ['REDIS_URL']}")
        return True

    # 检查 Redis 是否已经在运行（尝试连接 6379 端口）
    try:
        import socket
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", 6379))
        sock.close()
        if result == 0:
            logger.info("✅ Redis 已在运行中")
            return True
    except Exception:
        pass

    # 从项目根目录查找 RedisService.exe
    redis_exe = _find_redis_service()
    if redis_exe is None:
        logger.warning("   应用将使用内存缓存降级运行")
        logger.warning("   如需 Redis 缓存，请确保 Redis/RedisService.exe 存在于项目根目录")
        return False

    # 尝试启动 RedisService.exe
    try:
        logger.info(f"🚀 正在启动 Redis 服务: {redis_exe}...")
        # 使用 subprocess.Popen 后台启动
        process = subprocess.Popen(
            [str(redis_exe)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        # 等待 Redis 启动（最多等待 5 秒）
        for i in range(10):
            time.sleep(0.5)
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(0.5)
                result = sock.connect_ex(("127.0.0.1", 6379))
                sock.close()
                if result == 0:
                    logger.info("✅ Redis 服务已启动")
                    return True
            except Exception:
                pass

        # 启动超时
        logger.warning("⚠️ Redis 启动超时（5秒），将继续使用内存缓存降级运行")
        return False

    except Exception as e:
        logger.warning(f"⚠️ Redis 启动失败: {e}")
        logger.warning("   应用将使用内存缓存降级运行")
        return False


def main():
    parser = argparse.ArgumentParser(description="启动数据比对工具生产服务器")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式：启用热重载")
    parser.add_argument("--no-redis", action="store_true", help="不启动 Redis，使用内存缓存")
    args = parser.parse_args()

    # 检查前端构建产物
    frontend_dist = BASE_DIR / "frontend" / "dist"
    if not frontend_dist.exists():
        logger.warning(
            f"前端构建产物不存在: {frontend_dist}\n"
            f"请先运行: cd frontend && npm run build\n"
            f"或者使用开发模式: cd frontend && npm run dev"
        )

    # 检查必要目录
    data_dir = BASE_DIR / "backend" / "data"
    shared_pdfs = data_dir / "shared_pdfs"
    if not shared_pdfs.exists():
        logger.warning(f"PDF 数据目录不存在: {shared_pdfs}")
        logger.warning("请将 PDF 文件放入 backend/data/shared_pdfs/ 目录")

    # 启动 Redis（除非指定 --no-redis）
    if not args.no_redis:
        _try_start_redis()
    else:
        logger.info("🔌 已跳过 Redis 启动（--no-redis），使用内存缓存")

    logger.info(f"🚀 启动服务器: http://{args.host}:{args.port}")
    logger.info(f"📂 前端静态文件: {frontend_dist if frontend_dist.exists() else '未构建'}")
    logger.info(f"📄 PDF 数据目录: {shared_pdfs if shared_pdfs.exists() else '未创建'}")

    import uvicorn
    uvicorn.run(
        "backend.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
