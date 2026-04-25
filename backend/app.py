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
# pyright: reportMissingImports=false

import logging
import shutil
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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
    pdfs: list[UploadFile] = File(...),
):
    """
    上传 Excel 和 PDF 文件。

    - excel: 指标数据 Excel 文件
    - pdfs: 一个或多个 PDF 年度报告文件
    """
    # 清空上传目录
    upload_dir = UPLOAD_DIR
    if upload_dir.exists():
        shutil.rmtree(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)

    pdf_dir = upload_dir / "pdfs"
    pdf_dir.mkdir(exist_ok=True)

    # 保存 Excel
    excel_path = upload_dir / excel.filename
    with open(excel_path, "wb") as f:
        content = await excel.read()
        f.write(content)

    # 保存 PDF
    pdf_names = []
    for pdf in pdfs:
        pdf_path = pdf_dir / pdf.filename
        with open(pdf_path, "wb") as f:
            content = await pdf.read()
            f.write(content)
        pdf_names.append(pdf.filename)

    _state["excel_path"] = excel_path
    _state["pdf_dir"] = pdf_dir
    _state["analysis"] = None
    _state["comparator"] = None

    # 解析 Excel 以获取颜色选项
    try:
        indicators = read_indicators(excel_path)
        _state["indicators"] = indicators
        
        # 提取去重的颜色列表
        unique_colors = list(set([ind.bg_color for ind in indicators if ind.bg_color]))
        # 保证 "无填充" 在前面
        if "无填充" in unique_colors:
            unique_colors.remove("无填充")
            unique_colors.insert(0, "无填充")
            
    except Exception as e:
        logger.exception("解析 Excel 失败")
        raise HTTPException(400, f"读取 Excel 失败: {e}")

    logger.info(f"文件上传并解析完成: Excel={excel.filename}, PDFs={pdf_names}, 颜色={unique_colors}")

    return {
        "message": "上传并解析成功",
        "excel": excel.filename,
        "pdfs": pdf_names,
        "colors": unique_colors,
    }


@app.post("/api/analyze", response_model=AnalysisResponse)
async def run_analysis(req: AnalyzeRequest):
    """触发比对分析，返回完整结果。"""
    pdf_dir = _state.get("pdf_dir")
    indicators = _state.get("indicators")

    if indicators is None:
        raise HTTPException(400, "请先上传文件")

    if pdf_dir is None:
        pdf_dir = DATA_DIR
        if not list(DATA_DIR.glob("*.pdf")):
            raise HTTPException(400, "请先上传文件或在 data/ 目录中放置 PDF 文件")

    # 根据选择的颜色过滤指标
    filtered_indicators = indicators
    if req.selected_colors is not None:
        filtered_indicators = [
            ind for ind in indicators 
            if ind.bg_color in req.selected_colors
        ]
        
    if not filtered_indicators:
        raise HTTPException(400, "未找到符合所选颜色的指标数据")

    comparator = Comparator()
    _state["comparator"] = comparator

    try:
        analysis = comparator.run_full_analysis(filtered_indicators, pdf_dir)
    except Exception as e:
        logger.exception("分析失败")
        raise HTTPException(500, f"分析失败: {e}")

    _state["analysis"] = analysis
    logger.info(f"分析完成: {len(filtered_indicators)} 个指标, {len(analysis.pdf_names)} 个 PDF")

    return analysis


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

            # 重新计算进度
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
        
        # 对中文文件名进行 URL 编码
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
    pdf_dir = _state.get("pdf_dir")
    
    # 因为开发环境下热重载会导致 _state 丢失，这里做个智能回退
    if pdf_dir is None:
        from backend.config import UPLOAD_DIR, DATA_DIR
        if (UPLOAD_DIR / "pdfs" / filename).exists():
            pdf_dir = UPLOAD_DIR / "pdfs"
        else:
            pdf_dir = DATA_DIR
            
    logger.info(f"serve_pdf requested filename: {filename}, current pdf_dir: {pdf_dir}")
        
    pdf_path = pdf_dir / filename
    logger.info(f"Looking for PDF at: {pdf_path}")
    if not pdf_path.exists():
        logger.error(f"PDF not found at {pdf_path}")
        raise HTTPException(404, "PDF 文件不存在")
        
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Disposition": "inline"
        }
    )


@app.post("/api/manual_bind")
async def manual_bind(body: ManualBinding):
    """处理用户在 PDF 上的手动绑定"""
    analysis: AnalysisResponse | None = _state.get("analysis")
    if analysis is None:
        raise HTTPException(400, "请先执行分析")

    for result in analysis.results:
        if result.indicator.id == body.indicator_id:
            # 更新状态为已确认（手动）
            result.indicator.review_status = "已确认"
            result.indicator.note = f"手动绑定: {body.pdf_name} (第 {body.page} 页)"
            
            # 将手动选区记录到 matches 中以便展示
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
                
            # 可以选择追加或替换当前的 best_matches
            result.best_matches[body.pdf_name] = manual_match
            result.matches[body.pdf_name].append(manual_match)
            
            # 重新计算进度
            analysis.progress = _calc_progress(analysis)
            logger.info(f"指标 [{result.indicator.name}] 手动绑定成功: {body.pdf_name} 页码 {body.page}")
            return {"message": "绑定成功", "progress": analysis.progress}

    raise HTTPException(404, f"未找到 ID 为 {body.indicator_id} 的指标")


def _calc_progress(analysis: AnalysisResponse) -> ProgressInfo:
    """重新计算核对进度。"""
    indicators = [r.indicator for r in analysis.results]
    total = len(indicators)
    confirmed = sum(1 for i in indicators if i.review_status == "已确认")
    disputed = sum(1 for i in indicators if i.review_status == "存疑")
    not_found = sum(1 for i in indicators if i.review_status == "未找到")
    return ProgressInfo(
        total=total,
        confirmed=confirmed,
        disputed=disputed,
        not_found=not_found,
        unchecked=total - confirmed - disputed - not_found,
    )
