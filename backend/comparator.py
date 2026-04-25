"""
比对逻辑核心模块

将 Excel 指标与 PDF 提取的数值进行比对，
综合模糊匹配分数、距离、单位一致性计算置信度。
"""

from __future__ import annotations

import logging
import html
import re
from pathlib import Path

from backend.config import (
    TOLERANCE, SIMILARITY_THRESHOLD,
    WEIGHT_FUZZY_SCORE, WEIGHT_DISTANCE, WEIGHT_UNIT_CONSISTENCY,
)
from backend.models import (
    Indicator, MatchResult, IndicatorResult, ProgressInfo, AnalysisResponse,
)
from backend.pdf_extractor import PDFTextExtractor, PageText
from backend.number_finder import NumberContextFinder, ExtractedNumber
from backend.fuzzy_matcher import FuzzyMatcher

logger = logging.getLogger(__name__)


class Comparator:
    """
    核心比对引擎

    流程：
    1. 预处理：提取所有 PDF 的文本和数值（缓存）
    2. 对每个 Excel 指标，在每个 PDF 中查找最匹配的上下文
    3. 从最匹配的上下文中取距关键词最近的数值
    4. 综合计算置信度，判断是否一致
    """

    def __init__(
        self,
        context_window: int = 50,
        similarity_threshold: int = SIMILARITY_THRESHOLD,
        tolerance: float = TOLERANCE,
    ):
        self.extractor = PDFTextExtractor()
        self.finder = NumberContextFinder(context_window=context_window)
        self.matcher = FuzzyMatcher(threshold=similarity_threshold)
        self.tolerance = tolerance

        # 缓存：PDF 名 → (pages, numbers, full_text)
        self._cache: dict[str, tuple[list[PageText], list[ExtractedNumber], str]] = {}

    def preprocess_pdf(self, pdf_path: str | Path) -> None:
        """预处理单个 PDF：提取文本和数值，存入缓存。"""
        pdf_path = Path(pdf_path)
        name = pdf_path.name

        if name in self._cache:
            return

        pages = self.extractor.extract_text(pdf_path)
        full_text = self.extractor.get_full_text(pages)
        numbers = self.finder.find_all_numbers(full_text)

        self._cache[name] = (pages, numbers, full_text)
        logger.info(f"预处理完成 [{name}]: {len(pages)} 页, {len(numbers)} 个数值")

    def preprocess_pdfs(self, pdf_dir: str | Path) -> list[str]:
        """预处理目录下的所有 PDF 文件。"""
        pdf_dir = Path(pdf_dir)
        pdf_files = sorted(pdf_dir.glob("*.pdf"))

        if not pdf_files:
            logger.warning(f"在 {pdf_dir} 中未找到 PDF 文件")
            return []

        for pdf_path in pdf_files:
            self.preprocess_pdf(pdf_path)

        return [p.name for p in pdf_files]

    def compare_indicator(
        self,
        indicator: Indicator,
        pdf_names: list[str] | None = None,
    ) -> IndicatorResult:
        """
        比对单个指标在所有 PDF 中的匹配情况。

        Args:
            indicator: Excel 指标
            pdf_names: 要比对的 PDF 列表，默认全部

        Returns:
            IndicatorResult
        """
        if pdf_names is None:
            pdf_names = list(self._cache.keys())

        result = IndicatorResult(indicator=indicator)

        for pdf_name in pdf_names:
            if pdf_name not in self._cache:
                continue

            if not self._is_year_match(indicator.year, pdf_name):
                result.matches[pdf_name] = []
                result.best_matches[pdf_name] = None
                continue

            matches = self._find_matches_in_pdf(indicator, pdf_name)
            result.matches[pdf_name] = matches

            # 取置信度最高的作为 best match
            if matches:
                best = max(matches, key=lambda m: m.confidence)
                result.best_matches[pdf_name] = best
            else:
                result.best_matches[pdf_name] = None

        # 自动判断状态
        has_any = any(m is not None for m in result.best_matches.values())
        if not has_any:
            indicator.review_status = "未找到"
        else:
            # 统计所有 PDF 中的绝对数值匹配（is_match 为 True）数量
            exact_matches = []
            for pdf_matches in result.matches.values():
                exact_matches.extend([m for m in pdf_matches if m.is_match])
            
            # 若全局仅出现 1 次完全匹配，且置信度较高，则自动确认为“已确认”（前端变绿）
            if len(exact_matches) == 1 and exact_matches[0].confidence >= 70.0:
                indicator.review_status = "已确认"

        return result

    def _is_year_match(self, ind_year: str, pdf_name: str) -> bool:
        """
        判断指标所属年份是否与 PDF 文件名中的年份匹配。
        - 以前面的年份为准（例如 2022-2023 取 2022）
        - 如果无法提取年份，则默认匹配（返回 True）
        """
        if not ind_year:
            return True

        ind_match = re.search(r"(\d{4})", ind_year)
        if not ind_match:
            return True

        target_year = ind_match.group(1)

        pdf_match = re.search(r"(\d{4})", pdf_name)
        if not pdf_match:
            return True

        pdf_year = pdf_match.group(1)
        return target_year == pdf_year

    def _find_matches_in_pdf(
        self,
        indicator: Indicator,
        pdf_name: str,
    ) -> list[MatchResult]:
        """
        在单个 PDF 中查找指标的所有匹配。

        匹配策略（值优先）：
        1. 先在 PDF 全文中查找所有与 Excel 目标值相等的数值（允许容差）
        2. 对每个值匹配，截取上下文并做模糊匹配验证
        3. 按 (值匹配 > 模糊分数) 排序，值匹配为准
        """
        pages, numbers, full_text = self._cache[pdf_name]

        if not numbers:
            return []

        target = indicator.target_value

        # ========================================
        # 第一步：按数值匹配，找出所有值相等的数
        # ========================================
        value_matched: list[tuple[ExtractedNumber, bool]] = []  # (num, is_exact_value_match)

        target_numbers = self.finder.find_target_number(full_text, target, indicator.name)
        for num in target_numbers:
            value_matched.append((num, True))

        # ========================================
        # 第二步：对值匹配的结果做模糊匹配验证上下文
        # ========================================
        scored: list[tuple[ExtractedNumber, float, bool]] = []  # (num, fuzzy_score, is_val_match)

        for num, is_val in value_matched:
            match_result = self.matcher.match(
                indicator.name, num.context, indicator.aliases
            )
            scored.append((num, match_result.score, True))

        # ========================================
        # 第三步：如果值匹配为空，回退到纯模糊匹配
        # （但标记为非值匹配，置信度会较低）
        # ========================================
        if not scored:
            for num in numbers:
                match_result = self.matcher.match(
                    indicator.name, num.context, indicator.aliases
                )
                if self.matcher.is_above_threshold(match_result.score):
                    scored.append((num, match_result.score, False))

        if not scored:
            return []

        # 排序：值匹配优先，然后按模糊分数降序
        scored.sort(key=lambda x: (x[2], x[1]), reverse=True)

        # 去重：相近位置的合并（80 字符内视为同一处）
        deduplicated: list[tuple[ExtractedNumber, float, bool]] = []
        for num, fuzzy_score, is_val in scored:
            is_dup = False
            for existing_num, _, _ in deduplicated:
                if (abs(num.position - existing_num.position) < 80
                        and abs(num.value - existing_num.value) < 0.001):
                    is_dup = True
                    break
            if not is_dup:
                deduplicated.append((num, fuzzy_score, is_val))

        total = len(deduplicated)
        results: list[MatchResult] = []

        for idx, (num, fuzzy_score, is_val_match) in enumerate(deduplicated):
            page_number = self.extractor.get_page_for_position(pages, num.position)

            # 置信度：值匹配给予高基线
            if is_val_match:
                # 值匹配成功：基线 70 分，模糊匹配再加分
                confidence = 70.0 + 0.3 * fuzzy_score
            else:
                # 仅模糊匹配：按原权重计算
                confidence = self._calc_confidence(
                    fuzzy_score=fuzzy_score, num=num, indicator=indicator,
                )

            # 值比对结果
            is_match, difference = self._compare_values(
                num.value, num.unit, indicator.target_value, indicator.unit
            )

            # 高亮上下文
            highlighted = self._highlight_context(
                num.context, indicator.name, num.raw_str, indicator.aliases
            )

            results.append(MatchResult(
                pdf_name=pdf_name,
                page_number=page_number,
                matched_value=num.value,
                matched_value_raw=num.raw_str,
                context=num.context,
                context_highlighted=highlighted,
                confidence=round(min(100.0, confidence), 1),
                is_match=is_match,
                difference=round(difference, 4) if difference is not None else None,
                unit=num.unit,
                fuzzy_score=round(fuzzy_score, 1),
                match_index=idx,
                total_matches=total,
            ))

        return results

    def _is_value_match(
        self,
        pdf_value: float,
        pdf_unit: str | None,
        excel_value: float,
        excel_unit: str | None,
    ) -> bool:
        """
        判断 PDF 中的数值是否与 Excel 目标值相等。
        支持直接比较和单位换算比较。
        """
        # 直接比较（最常见场景：单位相同或都无单位）
        if abs(pdf_value - excel_value) <= self.tolerance:
            return True

        # 单位换算后比较
        from backend.config import UNIT_MULTIPLIERS

        pdf_base = pdf_value
        excel_base = excel_value

        if pdf_unit and pdf_unit in UNIT_MULTIPLIERS:
            pdf_base = pdf_value * UNIT_MULTIPLIERS[pdf_unit]
        if excel_unit and excel_unit in UNIT_MULTIPLIERS:
            excel_base = excel_value * UNIT_MULTIPLIERS[excel_unit]

        if pdf_base != pdf_value or excel_base != excel_value:
            if abs(pdf_base - excel_base) <= self.tolerance:
                return True

        return False

    def _calc_confidence(
        self,
        fuzzy_score: float,
        num: ExtractedNumber,
        indicator: Indicator,
    ) -> float:
        """
        计算综合置信度。

        confidence = W1 * fuzzy_score + W2 * distance_score + W3 * unit_score
        """
        # 模糊匹配分数 (0-100)
        f_score = fuzzy_score

        # 距离分数：关键词在上下文中离数值越近，分数越高
        d_score = self._calc_distance_score(
            num.context, indicator.name, num.raw_str, indicator.aliases
        )

        # 单位一致性分数
        u_score = self._calc_unit_score(num.unit, indicator.unit)

        confidence = (
            WEIGHT_FUZZY_SCORE * f_score
            + WEIGHT_DISTANCE * d_score
            + WEIGHT_UNIT_CONSISTENCY * u_score
        )

        return min(100.0, max(0.0, confidence))

    def _calc_distance_score(
        self,
        context: str,
        indicator_name: str,
        raw_num: str,
        aliases: list[str] | None = None,
    ) -> float:
        """计算关键词与数值在上下文中的距离分数。"""
        # 查找关键词位置
        terms = [indicator_name] + (aliases or [])
        kw_pos = -1
        for term in terms:
            pos = context.find(term)
            if pos != -1:
                kw_pos = pos
                break

        if kw_pos == -1:
            return 50.0  # 找不到关键词，给中等分

        # 查找数值位置
        num_pos = context.find(raw_num)
        if num_pos == -1:
            return 50.0

        # 距离越小，分数越高
        distance = abs(kw_pos - num_pos)
        max_distance = len(context)

        if max_distance == 0:
            return 100.0

        score = max(0, 100 - (distance / max_distance) * 100)
        return score

    def _calc_unit_score(
        self,
        pdf_unit: str | None,
        excel_unit: str | None,
    ) -> float:
        """计算单位一致性分数。"""
        if excel_unit is None or pdf_unit is None:
            return 70.0  # 无法判断时给中等分

        if pdf_unit == excel_unit:
            return 100.0

        return 30.0  # 单位不一致

    def _compare_values(
        self,
        pdf_value: float,
        pdf_unit: str | None,
        excel_value: float,
        excel_unit: str | None,
    ) -> tuple[bool, float | None]:
        """
        比对 PDF 值与 Excel 值，考虑单位换算。

        Returns:
            (是否一致, 差值)
        """
        # 如果单位相同或都为 None，直接比较
        if pdf_unit == excel_unit:
            diff = abs(pdf_value - excel_value)
            return diff <= self.tolerance, pdf_value - excel_value

        # 尝试单位换算后比较
        from backend.config import UNIT_MULTIPLIERS

        pdf_base = pdf_value
        excel_base = excel_value

        if pdf_unit and pdf_unit in UNIT_MULTIPLIERS:
            pdf_base = pdf_value * UNIT_MULTIPLIERS[pdf_unit]
        if excel_unit and excel_unit in UNIT_MULTIPLIERS:
            excel_base = excel_value * UNIT_MULTIPLIERS[excel_unit]

        # 先尝试同单位比较（不换算）
        diff_raw = abs(pdf_value - excel_value)
        if diff_raw <= self.tolerance:
            return True, pdf_value - excel_value

        # 再尝试换算后比较
        if pdf_base != pdf_value or excel_base != excel_value:
            diff_converted = abs(pdf_base - excel_base)
            if diff_converted <= self.tolerance:
                return True, pdf_base - excel_base

        return False, pdf_value - excel_value

    def _highlight_context(
        self,
        context: str,
        indicator_name: str,
        raw_num: str,
        aliases: list[str] | None = None,
    ) -> str:
        """
        生成高亮后的上下文 HTML。
        - 指标名称 → 蓝色加粗
        - 匹配数值 → 红色加粗
        """
        text = html.escape(context)

        # 高亮数值（红色加粗）
        escaped_num = html.escape(raw_num)
        if escaped_num in text:
            text = text.replace(
                escaped_num,
                f'<span class="hl-number">{escaped_num}</span>',
                1,
            )

        # 高亮指标名称和别名（蓝色加粗）
        terms = [indicator_name] + (aliases or [])
        for term in terms:
            escaped_term = html.escape(term)
            if escaped_term in text:
                text = text.replace(
                    escaped_term,
                    f'<span class="hl-keyword">{escaped_term}</span>',
                    1,
                )

        return text

    def run_full_analysis(
        self,
        indicators: list[Indicator],
        pdf_dir: str | Path,
    ) -> AnalysisResponse:
        """
        执行完整的比对分析。

        Args:
            indicators: Excel 指标列表
            pdf_dir: PDF 文件目录

        Returns:
            AnalysisResponse 完整的分析结果
        """
        pdf_names = self.preprocess_pdfs(pdf_dir)

        results: list[IndicatorResult] = []
        for indicator in indicators:
            result = self.compare_indicator(indicator, pdf_names)
            results.append(result)

        progress = self._calc_progress(indicators)

        return AnalysisResponse(
            indicators=indicators,
            pdf_names=pdf_names,
            results=results,
            progress=progress,
        )

    @staticmethod
    def _calc_progress(indicators: list[Indicator]) -> ProgressInfo:
        """计算核对进度。"""
        total = len(indicators)
        confirmed = sum(1 for i in indicators if i.review_status == "已确认")
        disputed = sum(1 for i in indicators if i.review_status == "存疑")
        not_found = sum(1 for i in indicators if i.review_status == "未找到")
        unchecked = total - confirmed - disputed - not_found

        return ProgressInfo(
            total=total,
            confirmed=confirmed,
            disputed=disputed,
            not_found=not_found,
            unchecked=unchecked,
        )
