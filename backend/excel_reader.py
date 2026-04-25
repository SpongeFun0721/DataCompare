"""
Excel 读取模块

支持两种 Excel 格式：
1. 简单格式：指标名称 + 目标数值（+ 可选单位/别名）
2. 宽表格式：分类层级列 + 多个年份列（用户实际数据结构）
   列示例：一级分类, 二级分类, 三级分类, 2021年, 2022年, ..., AI检索数据来源, ...
"""

from __future__ import annotations

import re
import logging
from pathlib import Path

import pandas as pd

from backend.models import Indicator

logger = logging.getLogger(__name__)

# ============================================================
# 列名映射（简单格式）
# ============================================================
COLUMN_MAPPINGS = {
    "name": ["指标名称", "指标", "名称", "项目", "name", "indicator"],
    "value": ["目标数值", "数值", "值", "金额", "value", "target", "amount"],
    "unit": ["单位", "unit"],
    "aliases": ["别名", "别称", "aliases"],
}

# 年份列检测正则：匹配 "2021年"、"2022"、"FY2023" 等
YEAR_COLUMN_PATTERN = re.compile(r"^(?:FY)?(\d{4})年?$")

# 分类列名
CATEGORY_COLUMNS = {
    "cat1": ["一级分类", "一级", "大类", "category1"],
    "cat2": ["二级分类", "二级", "中类", "category2"],
    "cat3": ["三级分类", "三级", "小类", "指标", "指标名称", "category3", "name"],
}


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """在 DataFrame 的列中查找匹配的列名。"""
    for col in df.columns:
        col_clean = str(col).strip().replace("\n", "").lower()
        for candidate in candidates:
            if col_clean == candidate.lower():
                return str(col)
    return None


def _detect_year_columns(df: pd.DataFrame) -> list[tuple[str, str]]:
    """
    检测年份列。

    Returns:
        [(列名, 年份字符串), ...] 如 [("2021年", "2021"), ("2022年", "2022")]
    """
    year_cols = []
    for col in df.columns:
        col_str = str(col).strip().replace("\n", "")
        m = YEAR_COLUMN_PATTERN.match(col_str)
        if m:
            year_cols.append((str(col), m.group(1)))
    return year_cols


def _detect_format(df: pd.DataFrame) -> str:
    """
    自动检测 Excel 的格式类型。

    Returns:
        "simple" 或 "wide"
    """
    # 检查是否有年份列
    year_cols = _detect_year_columns(df)
    if len(year_cols) >= 2:
        return "wide"

    # 检查是否有简单格式的列
    name_col = _find_column(df, COLUMN_MAPPINGS["name"])
    value_col = _find_column(df, COLUMN_MAPPINGS["value"])
    if name_col and value_col:
        return "simple"

    # 如果有年份列（哪怕只有 1 个）且有分类列，也是 wide
    if year_cols:
        for candidates in CATEGORY_COLUMNS.values():
            if _find_column(df, candidates):
                return "wide"

    return "wide"  # 默认尝试 wide 格式


def read_indicators(
    excel_path: str | Path,
    sheet_name: str | int = 0,
) -> list[Indicator]:
    """
    从 Excel 文件中读取指标列表，自动检测格式。

    Args:
        excel_path: Excel 文件路径
        sheet_name: Sheet 名称或索引

    Returns:
        Indicator 列表
    """
    excel_path = Path(excel_path)
    if not excel_path.exists():
        raise FileNotFoundError(f"Excel 文件不存在: {excel_path}")

    df = pd.read_excel(str(excel_path), sheet_name=sheet_name)
    logger.info(f"读取 Excel: {excel_path.name}, 共 {len(df)} 行, 列: {list(df.columns)}")

    fmt = _detect_format(df)
    logger.info(f"检测到 Excel 格式: {fmt}")

    if fmt == "simple":
        indicators = _read_simple_format(df)
    else:
        indicators = _read_wide_format(df)

    _extract_colors_with_win32com(excel_path, indicators)
    return indicators


def _extract_colors_with_win32com(excel_path: Path, indicators: list[Indicator]):
    """使用 win32com (WPS 或 Excel) 提取原始单元格的背景颜色"""
    try:
        import win32com.client
    except ImportError:
        logger.warning("未安装 pywin32，跳过颜色提取。")
        return

    excel = None
    wb = None
    try:
        # 尝试启动 WPS 或 Excel
        try:
            excel = win32com.client.DispatchEx("et.Application")
        except Exception:
            try:
                excel = win32com.client.DispatchEx("Ket.Application")
            except Exception:
                excel = win32com.client.DispatchEx("Excel.Application")
                
        excel.Visible = False
        excel.DisplayAlerts = False
        
        wb = excel.Workbooks.Open(str(excel_path))
        ws = wb.Sheets(1)

        # 映射列名到列索引
        col_name_to_idx = {}
        used_range = ws.UsedRange
        max_col = used_range.Columns.Count
        
        for c in range(1, max_col + 1):
            val = ws.Cells(1, c).Value
            if val is not None:
                col_name_to_idx[str(val).strip()] = c

        for ind in indicators:
            if not ind.row_index:
                ind.bg_color = "无填充"
                continue
                
            col_idx = col_name_to_idx.get(ind.col_name) if ind.col_name else None
            
            if not col_idx:
                ind.bg_color = "无填充"
                continue

            cell = ws.Cells(ind.row_index, col_idx)
            # 优先使用 DisplayFormat 获取实际显示颜色（支持条件格式）
            try:
                display_color_index = cell.DisplayFormat.Interior.ColorIndex
            except Exception:
                display_color_index = cell.Interior.ColorIndex

            if display_color_index in (-4142, None):  # xlColorIndexNone
                ind.bg_color = "无填充"
            else:
                try:
                    color_int = int(cell.DisplayFormat.Interior.Color)
                except Exception:
                    color_int = int(cell.Interior.Color)
                r = color_int & 0xFF
                g = (color_int >> 8) & 0xFF
                b = (color_int >> 16) & 0xFF
                ind.bg_color = f"#{r:02X}{g:02X}{b:02X}"

        logger.info("颜色提取完成。")

    except Exception as e:
        logger.error(f"提取单元格颜色失败: {e}")
        # 如果失败，默认为无填充
        for ind in indicators:
            ind.bg_color = "无填充"
    finally:
        if wb:
            try:
                wb.Close(False)
            except Exception:
                pass
        if excel:
            try:
                excel.Quit()
            except Exception:
                pass


def _read_simple_format(df: pd.DataFrame) -> list[Indicator]:
    """读取简单格式：指标名称 + 目标数值。"""
    name_col = _find_column(df, COLUMN_MAPPINGS["name"])
    value_col = _find_column(df, COLUMN_MAPPINGS["value"])

    if not name_col:
        raise ValueError(
            f"找不到指标名称列。期望列名之一: {COLUMN_MAPPINGS['name']}，"
            f"实际列: {list(df.columns)}"
        )
    if not value_col:
        raise ValueError(
            f"找不到目标数值列。期望列名之一: {COLUMN_MAPPINGS['value']}，"
            f"实际列: {list(df.columns)}"
        )

    unit_col = _find_column(df, COLUMN_MAPPINGS["unit"])
    alias_col = _find_column(df, COLUMN_MAPPINGS["aliases"])

    indicators: list[Indicator] = []

    for idx, row in df.iterrows():
        name = str(row[name_col]).strip()
        if not name or name == "nan":
            continue

        try:
            target_value = float(row[value_col])
        except (ValueError, TypeError):
            logger.warning(f"第 {idx + 2} 行数值无法解析: {row[value_col]}，跳过")
            continue

        unit = None
        if unit_col and pd.notna(row.get(unit_col)):
            unit = str(row[unit_col]).strip()

        aliases: list[str] = []
        if alias_col and pd.notna(row.get(alias_col)):
            raw = str(row[alias_col]).strip()
            aliases = [a.strip() for a in raw.split(",") if a.strip()]

        indicators.append(Indicator(
            id=len(indicators),
            name=name,
            target_value=target_value,
            unit=unit,
            aliases=aliases,
            row_index=idx + 2,
            col_name=value_col,
        ))

    logger.info(f"简单格式: 解析 {len(indicators)} 个指标")
    return indicators


def _read_wide_format(df: pd.DataFrame) -> list[Indicator]:
    """
    读取宽表格式：分类列 + 年份值列。

    实际列结构示例：
    一级分类 | 二级分类 | 三级分类 | 2021年 | 2022年 | ... | AI检索数据来源 | ...

    每行的三级分类作为指标名称，每个年份列的值作为一个独立 Indicator。
    """
    # 查找分类列
    cat1_col = _find_column(df, CATEGORY_COLUMNS["cat1"])
    cat2_col = _find_column(df, CATEGORY_COLUMNS["cat2"])
    cat3_col = _find_column(df, CATEGORY_COLUMNS["cat3"])

    # 至少需要一个分类列
    name_col = cat3_col or cat2_col or cat1_col
    if not name_col:
        raise ValueError(
            f"找不到分类/指标名称列。期望列名之一: "
            f"{CATEGORY_COLUMNS['cat3'] + CATEGORY_COLUMNS['cat2'] + CATEGORY_COLUMNS['cat1']}，"
            f"实际列: {list(df.columns)}"
        )

    # 检测年份列
    year_cols = _detect_year_columns(df)
    if not year_cols:
        raise ValueError(
            f"找不到年份数据列（如 '2021年'、'2022年' 等）。"
            f"实际列: {list(df.columns)}"
        )

    # 按照年份升序排序
    year_cols.sort(key=lambda x: int(x[1]))

    # 尝试寻找"AI检索数据来源"列
    ai_source_col = _find_column(df, ["AI检索数据来源", "来源", "数据来源", "source"])

    logger.info(f"分类列: cat1={cat1_col}, cat2={cat2_col}, cat3={cat3_col}")
    logger.info(f"年份列 (按升序): {[yc[0] for yc in year_cols]}")
    if ai_source_col:
        logger.info(f"找到来源列: {ai_source_col}")

    indicators: list[Indicator] = []

    # 用于前向填充分类列（合并单元格场景）
    if cat1_col:
        df[cat1_col] = df[cat1_col].ffill()
    if cat2_col:
        df[cat2_col] = df[cat2_col].ffill()

    for idx, row in df.iterrows():
        # 指标名称（取最细粒度的分类）
        raw_name = str(row[name_col]).strip() if pd.notna(row[name_col]) else ""
        if not raw_name or raw_name == "nan":
            continue

        cat1 = str(row[cat1_col]).strip() if cat1_col and pd.notna(row[cat1_col]) else ""
        cat2 = str(row[cat2_col]).strip() if cat2_col and pd.notna(row[cat2_col]) else ""

        # 构建别名：将上级分类 + 名称的组合也作为别名
        aliases: list[str] = []
        if cat2 and cat2 != raw_name:
            aliases.append(cat2)
        if cat1 and cat1 != raw_name and cat1 != cat2:
            aliases.append(cat1)

        # 添加 source_file 提取
        source_val = ""
        if ai_source_col and pd.notna(row.get(ai_source_col)):
            source_val = str(row[ai_source_col]).strip()

        # 为每个年份列创建一个 Indicator
        for col_name, year_str in year_cols:
            raw_val = row.get(col_name)

            # 跳过空值
            if pd.isna(raw_val):
                continue

            # 尝试解析数值
            try:
                target_value = float(raw_val)
            except (ValueError, TypeError):
                # 可能含有文本，尝试清理
                cleaned = str(raw_val).replace(",", "").replace("，", "").strip()
                try:
                    target_value = float(cleaned)
                except (ValueError, TypeError):
                    logger.debug(f"跳过非数值: {raw_name} / {col_name} = {raw_val}")
                    continue

            indicators.append(Indicator(
                id=len(indicators),
                name=raw_name,
                target_value=target_value,
                year=f"{year_str}年",
                category1=cat1,
                category2=cat2,
                aliases=aliases,
                row_index=idx + 2,
                col_name=col_name,
                source_file=source_val,
            ))

    logger.info(f"宽表格式: 解析 {len(indicators)} 个指标（{len(year_cols)} 个年份）")
    return indicators
