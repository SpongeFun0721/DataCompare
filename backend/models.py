"""
数据模型定义模块

使用 Pydantic v2 定义所有 API 请求/响应的数据结构。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Indicator(BaseModel):
    """Excel 中的一条指标记录"""

    id: int = Field(description="指标序号（自动生成）")
    name: str = Field(description="指标名称（三级分类或拼接分类）")
    target_value: float = Field(description="Excel 中的目标数值")
    unit: str | None = Field(default=None, description="单位，如'万元'、'元'、'%'")
    aliases: list[str] = Field(default_factory=list, description="指标别名列表")
    review_status: str = Field(default="未核对", description="核对状态：未核对|已确认|存疑|未找到")
    note: str = Field(default="", description="备注")
    year: str = Field(default="", description="对应年份，如'2024年'")
    category1: str = Field(default="", description="一级分类")
    category2: str = Field(default="", description="二级分类")
    row_index: int | None = Field(default=None, description="原始Excel中的行号(1-based)")
    col_name: str | None = Field(default=None, description="原始Excel中目标数值所在的列名")
    bg_color: str | None = Field(default=None, description="单元格背景颜色(十六进制，无填充为'无填充')")
    source_file: str | None = Field(default=None, description="原数据来源(AI检索数据来源)")


class NumberMatch(BaseModel):
    """PDF 中找到的一个数值及其上下文"""

    raw_str: str = Field(description="原始数字字符串，如 '1,234.56'")
    value: float = Field(description="解析后的浮点数值")
    position: int = Field(description="在全文中的字符偏移位置")
    page_number: int = Field(description="所在页码（从 1 开始）")
    context: str = Field(description="前后 N 字符的上下文文本")
    unit: str | None = Field(default=None, description="检测到的单位")


class MatchResult(BaseModel):
    """一个指标在一个 PDF 中的单个匹配结果"""

    pdf_name: str = Field(description="PDF 文件名")
    page_number: int = Field(default=0, description="所在页码")
    matched_value: float | None = Field(default=None, description="匹配到的数值")
    matched_value_raw: str | None = Field(default=None, description="原始数值字符串")
    context: str | None = Field(default=None, description="上下文原文")
    context_highlighted: str | None = Field(default=None, description="高亮后的上下文 HTML")
    confidence: float = Field(default=0.0, description="置信度 0-100")
    is_match: bool = Field(default=False, description="与 Excel 值是否一致")
    difference: float | None = Field(default=None, description="与 Excel 值的差值")
    unit: str | None = Field(default=None, description="PDF 中检测到的单位")
    fuzzy_score: float = Field(default=0.0, description="模糊匹配分数")
    match_index: int = Field(default=0, description="当前匹配序号（从 0 开始）")
    total_matches: int = Field(default=0, description="该 PDF 中的总匹配数")


class IndicatorResult(BaseModel):
    """一个指标在所有 PDF 中的汇总结果"""

    indicator: Indicator
    matches: dict[str, list[MatchResult]] = Field(
        default_factory=dict,
        description="按 PDF 文件名分组的匹配结果"
    )
    best_matches: dict[str, MatchResult | None] = Field(
        default_factory=dict,
        description="每个 PDF 中置信度最高的匹配"
    )


class ProgressInfo(BaseModel):
    """核对进度信息"""

    total: int = Field(description="指标总数")
    confirmed: int = Field(default=0, description="已确认数")
    disputed: int = Field(default=0, description="存疑数")
    not_found: int = Field(default=0, description="未找到数")
    unchecked: int = Field(default=0, description="未核对数")


class AnalysisResponse(BaseModel):
    """分析结果响应"""

    indicators: list[Indicator]
    pdf_names: list[str]
    results: list[IndicatorResult]
    progress: ProgressInfo


class AnalyzeRequest(BaseModel):
    """触发分析请求参数"""
    
    selected_colors: list[str] | None = Field(default=None, description="选中的背景色列表。如果为空则不限颜色。")


class StatusUpdate(BaseModel):
    """核对状态更新请求"""

    status: str = Field(description="新状态：已确认|存疑")
    note: str = Field(default="", description="备注")


class ManualBinding(BaseModel):
    """手动绑定请求参数"""
    
    indicator_id: int = Field(description="当前指标ID")
    pdf_name: str = Field(description="PDF 文件名")
    page: int = Field(description="所在页码")
    selected_text: str = Field(description="用户划选的文本")
