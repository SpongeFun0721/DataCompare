"""
启动脚本 —— 一键启动生产服务器

用法：
    python start_server.py              # 默认 0.0.0.0:8000
    python start_server.py --port 8080   # 自定义端口
    python start_server.py --host 127.0.0.1  # 仅本地访问
"""

import argparse
import logging
import sys
from pathlib import Path

# 确保项目根目录在 Python 路径中
BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="启动数据比对工具生产服务器")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式：启用热重载")
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
