"""
数值提取与上下文关联模块

使用正则表达式从 PDF 全文中提取所有数值，
并截取每个数值前后 N 个字符作为上下文句子。
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

from backend.config import CONTEXT_WINDOW, UNIT_MULTIPLIERS

logger = logging.getLogger(__name__)

# 数值匹配正则：支持千分位、空格分隔、小数、前后空格、负号、百分号、括号负数
NUMBER_PATTERN = re.compile(
    r"(?:\(\s*(\d{1,3}(?:(?:,\s*|\s+)\d{3})*(?:\s*\.\s*\d+)?)\s*\))"          # 括号负数 ( 1,234.56 )
    r"|(?:([-\u2212])?\s*(\d{1,3}(?:(?:,\s*|\s+)\d{3})*(?:\s*\.\s*\d+)?|\d+(?:\s*\.\s*\d+)?)\s*(%)?)",  # 普通数值
)

# 单位检测正则
UNIT_PATTERN = re.compile(
    r"^[\s,，]*(" + "|".join(re.escape(u) for u in UNIT_MULTIPLIERS.keys()) + r")"
)


@dataclass
class ExtractedNumber:
    """提取出的数值及其上下文信息"""
    raw_str: str
    value: float
    position: int
    context: str
    unit: str | None
    context_start: int


class NumberContextFinder:
    """
    数值提取器：正则取数 + 上下文截取 + 单位检测

    示例:
        finder = NumberContextFinder(context_window=50)
        numbers = finder.find_all_numbers(full_text)
    """

    def __init__(self, context_window: int = CONTEXT_WINDOW):
        self.context_window = context_window

    def find_target_number(self, text: str, value: float, indicator_name: str) -> list[ExtractedNumber]:
        """在文本中根据指标名称（提取单位）搜索特定的数值，并添加边界约束。"""
        results: list[ExtractedNumber] = []

        # 从三级分类提取括号内的单位
        unit = None
        unit_match = re.search(r"[(（](.*?)[)）]", indicator_name)
        if unit_match:
            unit = unit_match.group(1)

        # 构建搜索正则
        # 将数值转换为字符串，避免如 2.0 被格式化为 2.0 但文本中是 2
        val_str = str(value).rstrip('0').rstrip('.') if str(value).endswith('.0') else str(value)
        
        if unit:
            # text 已经去过空格，但这里仍然保留 \s* 以防万一
            pattern_str = fr"(?<![0-9.]){re.escape(val_str)}\s*{re.escape(unit)}(?![0-9.])"
        else:
            pattern_str = fr"(?<![0-9.]){re.escape(val_str)}(?![0-9.])"

        try:
            pattern = re.compile(pattern_str)
        except re.error as e:
            logger.error(f"正则编译失败: {pattern_str} - {e}")
            return results

        for match in pattern.finditer(text):
            raw_str = match.group(0)
            position = match.start()

            boundary_chars = "。！？.!?"
            start_idx = -1
            search_limit_start = max(0, position - 300)
            for char in boundary_chars:
                idx = text.rfind(char, search_limit_start, position)
                if idx > start_idx:
                    start_idx = idx

            if start_idx == -1:
                start = search_limit_start
            else:
                start = start_idx + 1

            end_search_start = position + len(raw_str)
            search_limit_end = min(len(text), end_search_start + 300)
            end_idx = len(text)

            for char in boundary_chars:
                idx = text.find(char, end_search_start, search_limit_end)
                if idx != -1 and idx < end_idx:
                    end_idx = idx

            if end_idx == len(text):
                end = search_limit_end
            else:
                end = end_idx + 1

            context = text[start:end].replace("\n", "").strip()

            results.append(ExtractedNumber(
                raw_str=raw_str, value=value, position=position,
                context=context, unit=unit, context_start=start,
            ))

        return results

    def find_all_numbers(self, text: str) -> list[ExtractedNumber]:
        """从文本中提取所有数值及其上下文。"""
        results: list[ExtractedNumber] = []

        for match in NUMBER_PATTERN.finditer(text):
            raw_str = match.group(0)
            position = match.start()

            value = self._parse_number(match)
            if value is None:
                continue

            # 过滤页码（如 - 1 - 或 第 1 页）
            if self._is_page_number(value, text, position, raw_str):
                continue

            # 不再过滤任何数值（包含年份、小数字等）

            # 寻找上下文：向前找最近的句号，向后找最近的句号，并加上最大长度限制防止段落过长
            # 不使用 \n 截止，因为 PDF 提取常带有大量无意义换行
            boundary_chars = "。！？.!?"
            
            # 向前找最近的边界
            start_idx = -1
            search_limit_start = max(0, position - 300)
            for char in boundary_chars:
                idx = text.rfind(char, search_limit_start, position)
                if idx > start_idx:
                    start_idx = idx
            
            if start_idx == -1:
                start = search_limit_start
            else:
                start = start_idx + 1  # 不包含前一个句号
                
            # 向后找最近的边界
            end_search_start = position + len(raw_str)
            search_limit_end = min(len(text), end_search_start + 300)
            end_idx = len(text)
            
            for char in boundary_chars:
                idx = text.find(char, end_search_start, search_limit_end)
                if idx != -1 and idx < end_idx:
                    end_idx = idx
                    
            if end_idx == len(text):
                end = search_limit_end
            else:
                end = end_idx + 1  # 包含后一个句号
                
            # 取出整句后，剔除其中的换行符，让句子连贯
            context = text[start:end].replace("\n", "").strip()

            # 检测单位
            after_text = text[match.end():match.end() + 10]
            unit_match = UNIT_PATTERN.match(after_text)
            unit = unit_match.group(1) if unit_match else None

            results.append(ExtractedNumber(
                raw_str=raw_str, value=value, position=position,
                context=context, unit=unit, context_start=start,
            ))

        logger.debug(f"从文本中提取到 {len(results)} 个数值")
        return results

    def _parse_number(self, match: re.Match) -> float | None:
        """解析正则匹配为浮点数。"""
        try:
            paren = match.group(1)
            if paren:
                clean_str = re.sub(r"[, ]", "", paren)
                return -float(clean_str)

            main_num = match.group(3)
            if not main_num:
                return None
            sign = match.group(2)
            clean_str = re.sub(r"[, ]", "", main_num)
            value = float(clean_str)
            if sign:
                value = -value
            return value
        except (ValueError, AttributeError):
            return None

    def _is_likely_year(self, value: float, text: str, position: int) -> bool:
        """判断数值是否可能是年份。"""
        if not (1900 <= value <= 2100) or value != int(value):
            return False
        end_pos = position + len(str(int(value)))
        after = text[end_pos:end_pos + 3]
        if "年" in after:
            return True
        before = text[max(0, position - 5):position]
        return any(kw in before for kw in ["年度", "年报", "财年", "会计", "报告期"])

    @staticmethod
    def get_value_with_unit(value: float, unit: str | None) -> float:
        """将数值乘以单位系数，转换为基础单位。"""
        if unit and unit in UNIT_MULTIPLIERS:
            return value * UNIT_MULTIPLIERS[unit]
        return value

    def _is_page_number(self, value: float, text: str, position: int, raw_str: str) -> bool:
        """判断数值是否可能是页码（如 - 1 - 或 第 1 页）。"""
        if value != int(value) or not (-1000 <= value <= 1000):
            return False
            
        # 提取前后各 5 个字符，并移除所有空格以统一判断
        start = max(0, position - 5)
        end = min(len(text), position + len(raw_str) + 5)
        surround_text = text[start:end].replace(" ", "")
        
        # 取绝对值字符串
        abs_val_str = str(int(abs(value)))
        
        # 判断模式
        if f"-{abs_val_str}-" in surround_text:
            return True
        if f"第{abs_val_str}页" in surround_text:
            return True
            
        return False

