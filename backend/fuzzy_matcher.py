"""
模糊匹配模块

使用 rapidfuzz 对指标名称与 PDF 上下文进行模糊匹配，
支持精确包含匹配和相似度匹配。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz

from backend.config import SIMILARITY_THRESHOLD

logger = logging.getLogger(__name__)


@dataclass
class MatchScore:
    """匹配得分结果"""
    score: float          # 0-100 的匹配分数
    is_exact: bool        # 是否精确包含匹配
    matched_term: str     # 实际匹配到的词（可能是别名）


class FuzzyMatcher:
    """
    指标名称模糊匹配器

    匹配策略（优先级从高到低）：
    1. 精确包含：指标名称完整出现在上下文中 → 100 分
    2. 别名精确包含：指标的某个别名完整出现在上下文中 → 95 分
    3. 模糊匹配：使用 partial_ratio 计算相似度 → 实际分数

    示例:
        matcher = FuzzyMatcher(threshold=60)
        result = matcher.match("营业收入", "公司实现营业总收入1234万元")
        # result.score ≈ 82, result.matched_term = "营业收入"
    """

    def __init__(self, threshold: int = SIMILARITY_THRESHOLD):
        """
        Args:
            threshold: 相似度阈值，低于此值视为不匹配
        """
        self.threshold = threshold

    def match(
        self,
        indicator_name: str,
        context: str,
        aliases: list[str] | None = None,
    ) -> MatchScore:
        """
        将指标名称与上下文文本进行匹配。

        Args:
            indicator_name: 指标名称，如 "营业收入"
            context: PDF 上下文文本
            aliases: 可选的指标别名列表

        Returns:
            MatchScore 包含分数和匹配详情
        """
        # 所有候选词：原名 + 别名
        candidates = [indicator_name] + (aliases or [])

        best = MatchScore(score=0, is_exact=False, matched_term="")

        for term in candidates:
            result = self._match_single(term, context)
            if result.score > best.score:
                best = result

            # 精确匹配直接返回，无需继续
            if best.is_exact:
                return best

        return best

    def _match_single(self, term: str, context: str) -> MatchScore:
        """
        单个词与上下文的匹配。

        Args:
            term: 待匹配词
            context: 上下文文本

        Returns:
            MatchScore
        """
        # 1. 精确包含匹配
        if term in context:
            return MatchScore(score=100.0, is_exact=True, matched_term=term)

        # 2. 模糊匹配（partial_ratio 更适合子串匹配场景）
        score = fuzz.partial_ratio(term, context)

        return MatchScore(score=score, is_exact=False, matched_term=term)

    def is_above_threshold(self, score: float) -> bool:
        """判断分数是否达到阈值。"""
        return score >= self.threshold

    def find_best_match(
        self,
        indicator_name: str,
        contexts: list[str],
        aliases: list[str] | None = None,
    ) -> tuple[int, MatchScore]:
        """
        在多个上下文中找到最佳匹配。

        Args:
            indicator_name: 指标名称
            contexts: 上下文列表
            aliases: 别名列表

        Returns:
            (最佳匹配的索引, MatchScore)
        """
        best_idx = -1
        best_score = MatchScore(score=0, is_exact=False, matched_term="")

        for i, ctx in enumerate(contexts):
            result = self.match(indicator_name, ctx, aliases)
            if result.score > best_score.score:
                best_score = result
                best_idx = i

        return best_idx, best_score
