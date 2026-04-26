"""
FastAPI 应用入口

提供 RESTful API 用于：
- 文件上传（Excel + PDF）
- 触发分析
- 查询指标和匹配结果
- 更新核对状态
- 导出报告
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import uuid
from pathlib import Path

import fitz
import httpx
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from urllib.parse import quote

from backend.config import UPLOAD_DIR, OUTPUT_DIR, DATA_DIR
from backend.models import (
    AnalysisResponse, StatusUpdate, IndicatorResult, ProgressInfo, AnalyzeRequest, ManualBinding,
)
from backend.excel_reader import read_indicators
from backend.comparator import Comparator
from backend.report_generator import generate_report, generate_colored_original_excel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="数据比对工具", version="1.0.0")

# CORS 配置，允许前端开发服务器访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================================================
# 1. 启动时加载所有预存 PDF 的索引
# ============================================================
SHARED_PDF_DIR = DATA_DIR / "shared_pdfs"
pdf_index: dict[str, str] = {}
yearbook_index: dict[str, str] = {}
map_data: dict[str, list[str]] = {}


def build_pdf_index():
    """遍历 data/shared_pdfs/ 目录，构建 PDF 索引。"""
    global pdf_index, yearbook_index, map_data

    pdf_dir = SHARED_PDF_DIR
    if not pdf_dir.exists():
        logger.warning(f"共享 PDF 目录不存在: {pdf_dir}")
        return

    # --- 索引所有 PDF 文件 ---
    for f in pdf_dir.iterdir():
        if not f.is_file():
            continue
        name = f.name

        # 年鉴 PDF 单独处理，不加入 pdf_index
        if "年鉴" in name:
            continue

        # 去掉 _ocr.pdf 或 _trans.pdf 后缀（支持文件名中数字与下划线之间有空格的情况）
        core_name = re.sub(r'\s*_(ocr|trans)\.pdf$', '', name)
        if core_name != name:
            # 带 _ocr/_trans 后缀的司局报告
            pdf_index[core_name] = name
            logger.debug(f"索引司局 PDF: {core_name} -> {name}")
        else:
            # 不带 _ocr/_trans 后缀的普通 PDF 也加入索引（以去掉 .pdf 后的基名为 key）
            base_name = name[:-4] if name.endswith(".pdf") else name
            pdf_index[base_name] = name
            logger.debug(f"索引普通 PDF: {base_name} -> {name}")

    # --- 使用 glob 匹配年鉴 PDF（支持 年鉴2022.pdf、年鉴2023.pdf、中国体育年鉴.pdf 等变体）---
    yearbook_files = sorted(pdf_dir.glob("*年鉴*.pdf"))
    yearbook_index.clear()
    if yearbook_files:
        for yb_path in yearbook_files:
            yb_name = yb_path.name
            # 从文件名中提取年份
            year_match = re.search(r'(\d{4})', yb_name)
            if year_match:
                year_str = year_match.group(1)
                yearbook_index[year_str] = yb_name
                logger.info(f"年鉴 PDF 索引: 年份 {year_str} -> {yb_name}")
            else:
                # 无年份的作为默认年鉴
                yearbook_index["default"] = yb_name
                logger.info(f"年鉴 PDF 索引(默认): {yb_name}")

        logger.info(f"找到 {len(yearbook_files)} 个年鉴 PDF: {list(yearbook_index.keys())}")
    else:
        logger.warning("未找到年鉴 PDF (glob: *年鉴*.pdf)")

    # --- 加载 map.json ---
    map_path = DATA_DIR / "map.json"
    if map_path.exists():
        with open(map_path, "r", encoding="utf-8") as f:
            map_data = json.load(f)
        logger.info(f"加载 map.json，包含年份: {list(map_data.keys())}")
    else:
        logger.warning(f"map.json 不存在: {map_path}")

    logger.info(f"PDF 索引加载完成: {len(pdf_index)} 个司局/普通 PDF + {len(yearbook_index)} 个年鉴 PDF")




# 应用启动时构建索引
build_pdf_index()

# ============================================================
# 2. 并发控制 & 任务状态
# ============================================================
_semaphore = asyncio.Semaphore(3)
task_status: dict[str, str] = {}  # task_id -> status

# ============================================================
# 全局状态（单用户简化版，生产环境应使用数据库）
# ============================================================
_state: dict = {
    "analysis": None,       # AnalysisResponse
    "comparator": None,     # Comparator 实例
    "excel_path": None,     # 上传的 Excel 文件路径
    "pdf_dir": None,        # PDF 文件目录
}


@app.post("/api/upload")
async def upload_files(
    excel: UploadFile = File(...),
    user_id: str = "default",
):
    """
    上传 Excel 文件（去掉了 PDF 上传参数）。

    用户上传 Excel 后，系统自动进行 PDF 匹配并进入分析流程。
    使用 user_id + task_id 创建独立工作目录。
    """
    task_id = str(uuid.uuid4())
    task_status[task_id] = "uploading"

    # 用户隔离：创建独立目录 uploads/{user_id}/{task_id}/
    work_dir = UPLOAD_DIR / user_id / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # 保存 Excel
    excel_path = work_dir / excel.filename
    with open(excel_path, "wb") as f:
        content = await excel.read()
        f.write(content)

    _state["excel_path"] = excel_path
    _state["analysis"] = None
    _state["comparator"] = None

    # 解析 Excel
    try:
        indicators = read_indicators(excel_path)
        _state["indicators"] = indicators

        unique_colors = list(set([ind.bg_color for ind in indicators if ind.bg_color]))
        if "无填充" in unique_colors:
            unique_colors.remove("无填充")
            unique_colors.insert(0, "无填充")

    except Exception as e:
        logger.exception("解析 Excel 失败")
        task_status[task_id] = "error"
        raise HTTPException(400, f"读取 Excel 失败: {e}")

    # ============================================================
    # 3. 收集所有可能需要的 PDF 文件（用于预处理）
    #    使用 Comparator._parse_source_file 解析每个指标的 source_file，
    #    收集所有引用的 PDF 核心文件名，在 shared_pdfs 中查找匹配的实际文件。
    # ============================================================
    from backend.comparator import Comparator

    matched_pdfs: set[str] = set()
    url_count = 0

    for ind in indicators:
        source_pages = Comparator._parse_source_file(ind.source_file)
        for sp in source_pages:
            if sp.source_type == "url":
                url_count += 1
                continue
            if sp.source_type == "yearbook":
                # 年鉴：将所有年鉴 PDF 都加入 matched_pdfs，确保预处理阶段全部加载
                for yb_name in yearbook_index.values():
                    matched_pdfs.add(yb_name)
            else:
                # 司局报告：使用原有的匹配逻辑
                pdf_names = [p.name for p in SHARED_PDF_DIR.glob("*.pdf")]
                actual_name = Comparator._match_pdf_name(sp.core_name, pdf_names)
                if actual_name:
                    matched_pdfs.add(actual_name)

    task_status[task_id] = "uploaded"

    logger.info(
        f"上传并解析完成: Excel={excel.filename}, "
        f"共 {len(indicators)} 个指标, "
        f"收集到 {len(matched_pdfs)} 个 PDF, "
        f"{url_count} 个 URL 来源, "
        f"颜色={unique_colors}"
    )

    _state["task_id"] = task_id
    _state["matched_pdfs"] = list(matched_pdfs)

    return {
        "message": "上传并解析成功",
        "task_id": task_id,
        "excel": excel.filename,
        "matched_pdfs": list(matched_pdfs),
        "url_count": url_count,
        "colors": unique_colors,
        "indicators_count": len(indicators),
    }



def _infer_data_type(source_file: str) -> str:
    """从 source_file 字符串推断数据类型。"""
    if not source_file:
        return "document"
    if re.match(r'^https?://', source_file.strip()):
        return "url"
    if "年鉴" in source_file:
        return "yearbook"
    return "document"


def _extract_pdf_name(source_file: str) -> str | None:
    """
    从 source_file 中提取 PDF 文件名（核心文件名）。

    source_file 示例：
    "33.反兴奋剂中心关于2022年工作总结和2023年工作计划的报告+P6;\\n中国体育年鉴+P363;\\nhttps://..."

    提取逻辑：
    1. 按分号或换行分割
    2. 排除包含 URL 或"年鉴"的部分
    3. 取第一个符合条件的部分
    4. 去掉 +P页码 后缀
    """
    parts = re.split(r'[;\\n]+', source_file)
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if re.match(r'^https?://', part):
            continue
        if "年鉴" in part:
            continue
        clean = re.sub(r'\s*\+P\d+.*$', '', part).strip()
        if clean:
            return clean
    return None


def _find_in_year_group(pdf_core_name: str, year_group: list[str]) -> str | None:
    """
    在年份组中查找匹配的核心文件名。

    支持模糊匹配：
    1. 精确匹配
    2. 包含匹配
    3. 前缀匹配
    """
    if pdf_core_name in year_group:
        return pdf_core_name

    for entry in year_group:
        if pdf_core_name in entry or entry in pdf_core_name:
            return entry

    short_name = pdf_core_name[:10]
    for entry in year_group:
        if entry.startswith(short_name) or short_name in entry:
            return entry

    return None


@app.post("/api/analyze", response_model=AnalysisResponse)
async def run_analysis(req: AnalyzeRequest):
    """触发比对分析，返回完整结果。"""
    pdf_dir = _state.get("pdf_dir")
    indicators = _state.get("indicators")

    if indicators is None:
        raise HTTPException(400, "请先上传文件")

    if pdf_dir is None:
        pdf_dir = SHARED_PDF_DIR
        if not pdf_dir.exists() or not list(pdf_dir.glob("*.pdf")):
            raise HTTPException(400, "请先上传文件或在 data/shared_pdfs/ 目录中放置 PDF 文件")

    filtered_indicators = indicators
    if req.selected_colors is not None:
        filtered_indicators = [
            ind for ind in indicators
            if ind.bg_color in req.selected_colors
        ]

    if not filtered_indicators:
        raise HTTPException(400, "未找到符合所选颜色的指标数据")

    # 应用颜色→来源类型映射，将 source_file 分配到三个互斥字段
    color_mapping = req.color_mapping or {}
    for ind in filtered_indicators:
        # 根据 bg_color 确定来源类型
        source_type = color_mapping.get(ind.bg_color, "")
        if source_type == "yearbook":
            ind.source_file_yearbook = ind.source_file
        elif source_type == "report":
            ind.source_file_report = ind.source_file
        elif source_type == "url":
            ind.source_file_url = ind.source_file
        # 如果映射为空或未知类型，则不设置任何字段（后续会被标记为未核对）

    # 获取上传时匹配到的 PDF 文件名列表，仅预处理这些需要的 PDF
    matched_pdfs = _state.get("matched_pdfs", [])

    # ============================================================
    # DEBUG: 打印第一个指标的 best_matches 内容
    # ============================================================
    if filtered_indicators:
        first = filtered_indicators[0]
        print(f"\n=== DEBUG /api/analyze ===")
        print(f"  第一个指标: id={first.id}, name='{first.name}', year='{first.year}'")
        print(f"  source_type: report={first.source_file_report}, yearbook={first.source_file_yearbook}, url={first.source_file_url}")
        print(f"  matched_pdfs (from _state): {matched_pdfs}")
        
        # 手动调用 _match_pdf_name 测试
        from backend.comparator import Comparator
        pdf_names = [p.name for p in Path(pdf_dir).glob("*.pdf")]
        print(f"  目录中所有 PDF ({len(pdf_names)}):")
        for pn in sorted(pdf_names):
            print(f"    - {pn}")
        
        # 对第一个指标的 report source 做匹配测试
        source_pages = Comparator._parse_source_file(first.source_file)
        for sp in source_pages:
            if sp.source_type == "report":
                matched = Comparator._match_pdf_name(sp.core_name, pdf_names)
                print(f"  match test: core_name='{sp.core_name}' → matched='{matched}'")
                # 打印打分过程
                core_elem = Comparator._extract_key_elements(sp.core_name)
                print(f"    core_elem: org='{core_elem['org']}', year_pair={core_elem['year_pair']}, normalized='{core_elem['normalized']}'")
                for pn in pdf_names:
                    pdf_elem = Comparator._extract_key_elements(pn)
                    print(f"    pdf='{pn}': org='{pdf_elem['org']}', year_pair={pdf_elem['year_pair']}, normalized='{pdf_elem['normalized']}'")
        print(f"=== DEBUG END ===\n")

    async with _semaphore:
        comparator = Comparator()
        _state["comparator"] = comparator

        try:
            # 仅预处理匹配到的 PDF 文件
            pdf_files = [pdf_dir / name for name in matched_pdfs if (pdf_dir / name).exists()]
            # print(f"  实际存在的 PDF 文件: {[p.name for p in pdf_files]}")
            if not pdf_files:
                # print(f"  ⚠️ 所有 matched_pdfs 都不存在！回退到 run_full_analysis")
                # 回退：预处理目录下所有 PDF
                analysis = comparator.run_full_analysis(filtered_indicators, pdf_dir, yearbook_index)
            else:
                for pdf_path in pdf_files:
                    # print(f"  预处理: {pdf_path.name}")
                    comparator.preprocess_pdf(pdf_path)
                pdf_names = [p.name for p in pdf_files]
                # print(f"  预处理完成，pdf_names: {pdf_names}")
                # print(f"  缓存中的 key: {list(comparator._cache.keys())}")
                analysis = comparator.run_analysis_on_preprocessed(filtered_indicators, pdf_names, yearbook_index)
        except Exception as e:
            logger.exception("分析失败")
            raise HTTPException(500, f"分析失败: {e}")


    _state["analysis"] = analysis
    logger.info(f"分析完成: {len(filtered_indicators)} 个指标, {len(analysis.pdf_names)} 个 PDF")

    return analysis



@app.get("/api/debug/analysis")
async def debug_analysis():
    """调试接口：返回分析数据的详细信息。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        return {"error": "请先执行分析"}
    
    debug_data = []
    for r in analysis.results[:5]:  # 只打印前5个
        ind = r.indicator
        debug_data.append({
            "id": ind.id,
            "name": ind.name,
            "year": ind.year,
            "source_file": ind.source_file,
            "source_file_report": ind.source_file_report,
            "source_file_yearbook": ind.source_file_yearbook,
            "source_file_url": ind.source_file_url,
            "matched_source_type": ind.matched_source_type,
            "matched_pdf_name": ind.matched_pdf_name,
            "matched_page": ind.matched_page,
            "best_matches_keys": list(r.best_matches.keys()),
            "best_matches_detail": {
                k: {
                    "page_number": v.page_number if v else None,
                    "matched_value_raw": v.matched_value_raw if v else None,
                    "is_match": v.is_match if v else None,
                    "confidence": v.confidence if v else None,
                } if v else None
                for k, v in r.best_matches.items()
            },
            "matches_keys": list(r.matches.keys()),
        })
    
    return {
        "total_indicators": len(analysis.results),
        "pdf_names": analysis.pdf_names,
        "first_5": debug_data,
    }


@app.get("/api/indicators")
async def get_indicators():
    """获取指标列表。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    return {
        "indicators": [r.indicator for r in analysis.results],
        "progress": analysis.progress,
    }


@app.get("/api/indicator/{indicator_id}/matches")
async def get_indicator_matches(indicator_id: int):
    """获取某个指标在所有 PDF 中的匹配详情。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    for result in analysis.results:
        if result.indicator.id == indicator_id:
            return result

    raise HTTPException(404, f"未找到 ID 为 {indicator_id} 的指标")


@app.put("/api/indicator/{indicator_id}/status")
async def update_indicator_status(indicator_id: int, body: StatusUpdate):
    """更新指标的核对状态。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    for result in analysis.results:
        if result.indicator.id == indicator_id:
            result.indicator.review_status = body.status
            result.indicator.note = body.note
            analysis.progress = _calc_progress(analysis)
            logger.info(f"指标 [{result.indicator.name}] 状态更新为: {body.status}")
            return {"message": "更新成功", "progress": analysis.progress}

    raise HTTPException(404, f"未找到 ID 为 {indicator_id} 的指标")


@app.get("/api/progress")
async def get_progress():
    """获取核对进度统计。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    analysis.progress = _calc_progress(analysis)
    return analysis.progress


@app.get("/api/export")
async def export_report():
    """导出核对结果 Excel 文件。"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    excel_path = _state.get("excel_path")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    try:
        report_filename = f"核对报告_{excel_path.name}" if excel_path else "报告数据核对结果.xlsx"
        output_path = generate_report(
            results=analysis.results,
            pdf_names=analysis.pdf_names,
            output_path=OUTPUT_DIR / report_filename
        )
    except Exception as e:
        logger.exception("导出报告失败")
        raise HTTPException(500, f"导出失败: {e}")

    encoded_filename = quote(output_path.name)
    return FileResponse(
        path=str(output_path),
        filename=output_path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Accept-Ranges": "bytes"
        }
    )


@app.get("/api/export_original")
async def export_original():
    """下载标色的原版 Excel 报告"""
    excel_path = _state.get("excel_path")
    analysis = _state.get("analysis")

    if not excel_path or not analysis:
        raise HTTPException(400, "暂无分析结果可导出标色原表")

    try:
        from backend.report_generator import generate_colored_original_excel
        report_filename = f"标色原表_{excel_path.name}"
        report_path = generate_colored_original_excel(
            analysis.results,
            excel_path,
            output_path=OUTPUT_DIR / report_filename
        )
        encoded_filename = quote(report_path.name)
        return FileResponse(
            path=str(report_path),
            filename=report_path.name,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        logger.exception("导出标色原表失败")
        raise HTTPException(500, f"导出标色原表失败: {e}")


@app.get("/api/export_text")
async def export_text():
    """下载 PDF 提取出的纯文本（用于诊断OCR和提取质量）"""
    comparator = _state.get("comparator")
    if not comparator or not comparator._cache:
        raise HTTPException(400, "请先执行开始分析，以便系统提取 PDF 纯文本")

    try:
        text_content = ""
        for pdf_name, data in comparator._cache.items():
            pages, numbers, full_text = data
            text_content += f"================ {pdf_name} ================\n\n"
            text_content += full_text
            text_content += "\n\n"

        excel_path = _state.get("excel_path")
        text_filename = f"PDF纯文本_{excel_path.stem}.txt" if excel_path else "PDF提取纯文本.txt"
        output_path = OUTPUT_DIR / text_filename
        output_path.write_text(text_content, encoding="utf-8")

        encoded_filename = quote(output_path.name)
        return FileResponse(
            path=str(output_path),
            filename=output_path.name,
            media_type="text/plain",
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}"
            }
        )
    except Exception as e:
        logger.exception("导出纯文本失败")
        raise HTTPException(500, f"导出纯文本失败: {e}")


@app.get("/api/pdf/{filename}")
async def serve_pdf(filename: str):
    """提供 PDF 文件的 HTTP 访问，供前端 PDF 阅读器加载。"""
    # 优先从 SHARED_PDF_DIR 查找
    pdf_path = SHARED_PDF_DIR / filename
    if not pdf_path.exists():
        # 回退到 UPLOAD_DIR/pdfs
        from backend.config import UPLOAD_DIR
        pdf_path = UPLOAD_DIR / "pdfs" / filename
    if not pdf_path.exists():
        # 再回退到 _state 中的 pdf_dir
        pdf_dir = _state.get("pdf_dir")
        if pdf_dir:
            pdf_path = pdf_dir / filename

    logger.info(f"serve_pdf requested filename: {filename}")

    if not pdf_path.exists():
        logger.error(f"PDF not found: {filename}")
        raise HTTPException(404, "PDF 文件不存在")

    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": "inline",
            "Cache-Control": "public, max-age=3600",
        }
    )


# ============================================================
# 7. URL 代理接口（用于 AI 来源的网页文本提取）
# ============================================================
@app.get("/api/proxy-url")
async def proxy_url(url: str = Query(..., description="要代理获取的 URL")):
    """
    代理获取指定 URL 的内容，提取网页正文纯文本。
    返回 JSON：{ "url": ..., "text": ..., "title": ... }
    前端用文本面板展示，支持高亮。
    """
    import httpx
    from bs4 import BeautifulSoup
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            })
            content_type = resp.headers.get("content-type", "")
            title = ""
            text = ""
            if "text/html" in content_type or "application/xhtml" in content_type:
                soup = BeautifulSoup(resp.text, "html.parser")
                # 提取标题
                if soup.title:
                    title = soup.title.get_text(strip=True)
                # 移除 script、style、nav、footer、header、aside 等非正文元素
                for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg", "form", "button"]):
                    tag.decompose()
                # 提取正文文本
                body = soup.find("body")
                if body:
                    text = body.get_text(separator="\n", strip=True)
                else:
                    text = soup.get_text(separator="\n", strip=True)
                # 清理多余空行
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                text = "\n".join(lines[:500])  # 最多 500 行
            else:
                # 非 HTML 内容，直接返回文本
                text = resp.text[:50000]
            
            return {
                "url": url,
                "title": title,
                "text": text,
            }
    except ImportError:
        # 如果没有 BeautifulSoup，使用简单的正则提取
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                resp = await client.get(url, headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                })
                import re
                # 简单提取 body 内容
                body_match = re.search(r'<body[^>]*>(.*?)</body>', resp.text, re.DOTALL | re.IGNORECASE)
                if body_match:
                    text = re.sub(r'<[^>]+>', '', body_match.group(1))
                else:
                    text = re.sub(r'<[^>]+>', '', resp.text)
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                text = "\n".join(lines[:500])
                return {
                    "url": url,
                    "title": "",
                    "text": text,
                }
        except Exception as e2:
            logger.exception(f"代理 URL 失败(回退): {url}")
            raise HTTPException(502, f"代理获取 URL 失败: {e2}")
    except Exception as e:
        logger.exception(f"代理 URL 失败: {url}")
        raise HTTPException(502, f"代理获取 URL 失败: {e}")


# ============================================================
# 6. PDF 页面文本接口
# ============================================================
@app.get("/api/pdf/{pdf_name}/page/{page_num}")
async def get_pdf_page_text(pdf_name: str, page_num: int):
    """
    返回 PDF 指定页码的文本内容。
    使用 fitz.open 只读那一页，读完即关闭。
    """
    # 优先从 SHARED_PDF_DIR 查找
    pdf_path = SHARED_PDF_DIR / pdf_name
    if not pdf_path.exists():
        # 回退到 DATA_DIR
        pdf_path = DATA_DIR / pdf_name
    if not pdf_path.exists():
        # 再回退到 UPLOAD_DIR/pdfs
        from backend.config import UPLOAD_DIR
        pdf_path = UPLOAD_DIR / "pdfs" / pdf_name
    if not pdf_path.exists():
        raise HTTPException(404, f"PDF 文件不存在: {pdf_name}")


    try:
        doc = fitz.open(str(pdf_path))
        if page_num < 0 or page_num >= len(doc):
            total = len(doc)
            doc.close()
            raise HTTPException(404, f"页码 {page_num} 超出范围 (共 {total} 页)")

        page = doc[page_num]
        text = page.get_text("text")
        total_pages = len(doc)
        doc.close()

        return {
            "pdf_name": pdf_name,
            "page_num": page_num,
            "total_pages": total_pages,
            "text": text,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"读取 PDF 页面失败: {pdf_name} 第{page_num}页")
        raise HTTPException(500, f"读取 PDF 页面失败: {e}")


# ============================================================
# 5. 任务状态查询接口
# ============================================================
@app.get("/api/task/{task_id}/status")
async def get_task_status(task_id: str):
    """查询异步任务的状态。"""
    status = task_status.get(task_id)
    if status is None:
        raise HTTPException(404, f"任务不存在: {task_id}")
    return {
        "task_id": task_id,
        "status": status,
    }


@app.post("/api/manual_bind")
async def manual_bind(body: ManualBinding):
    """处理用户在 PDF 上的手动绑定"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    for result in analysis.results:
        if result.indicator.id == body.indicator_id:
            result.indicator.review_status = "已核对"
            result.indicator.note = f"手动绑定: {body.pdf_name} (第 {body.page} 页)"

            from backend.models import MatchResult
            manual_match = MatchResult(
                pdf_name=body.pdf_name,
                page_number=body.page,
                matched_value_raw=body.selected_text,
                confidence=100.0,
                is_match=True,
                context=body.selected_text,
                context_highlighted=f"<mark>{body.selected_text}</mark>"
            )

            if body.pdf_name not in result.matches:
                result.matches[body.pdf_name] = []

            result.best_matches[body.pdf_name] = manual_match
            result.matches[body.pdf_name].append(manual_match)

            analysis.progress = _calc_progress(analysis)
            logger.info(f"指标 [{result.indicator.name}] 手动绑定成功: {body.pdf_name} 页码 {body.page}")
            return {"message": "绑定成功", "progress": analysis.progress}

    raise HTTPException(404, f"未找到 ID 为 {body.indicator_id} 的指标")


def _calc_progress(analysis: AnalysisResponse) -> ProgressInfo:
    """重新计算核对进度。仅统计已核对。"""
    indicators = [r.indicator for r in analysis.results]
    total = len(indicators)
    confirmed = sum(1 for i in indicators if i.review_status == "已核对")
    return ProgressInfo(
        total=total,
        confirmed=confirmed,
        unchecked=total - confirmed,
    )
