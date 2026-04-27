"""
AI 分析模块 —— 使用 DeepSeek API 分析 URL 网页文本中是否存在指标数据

功能：
1. 接收指标的分类层级（一级标题-二级标题-三级标题-数据项）和 URL 网页纯文本
2. 调用 DeepSeek API 让 AI 判断数据是否存在
3. 如果存在但需要运算，给出运算逻辑
4. 返回分析结果
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ============================================================
# DeepSeek API 配置
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_URL = "https://api.deepseek.com/v1/chat/completions"
DEEPSEEK_MODEL = "deepseek-chat"  # 或 deepseek-reasoner

# 系统提示词
SYSTEM_PROMPT = """你是一个专业的数据分析助手，擅长从网页文本中提取和验证数据。

## 任务
用户会提供：
1. **指标层级**：一级标题-二级标题-三级标题-数据项（四级标题）
2. **网页纯文本**：从目标 URL 提取的正文内容

你需要判断该指标数据是否存在于网页文本中。

## 分析要求
1. **精确匹配**：在网页文本中查找与"数据项"名称完全匹配或语义相近的内容
2. **数值验证**：如果找到匹配项，检查其数值是否与用户提供的目标值一致
3. **运算判断**：如果数据不是直接给出，而是需要通过计算得出（如求和、平均值、百分比等），请给出具体的运算逻辑
4. **不存在判断**：如果网页文本中完全不包含该数据，请明确说明

## 输出格式
请以 JSON 格式输出，格式如下：
{
    "exists": true/false,
    "confidence": "high/medium/low",
    "matched_text": "在网页中找到的匹配文本片段（如果存在）",
    "matched_value": 数值或null,
    "calculation": "如果数据需要运算得出，描述运算逻辑；否则为null",
    "calculation_detail": "运算的详细步骤说明",
    "explanation": "对分析结果的详细解释"
}

注意：
- matched_text 应从网页原文中提取，保持原样
- 如果数据不存在，exists 为 false，matched_text 和 matched_value 为 null
- 如果数据需要运算，calculation 描述运算逻辑，calculation_detail 给出详细步骤
- explanation 用中文解释你的分析过程"""


class AIAnalyzer:
    """
    AI 分析器 —— 使用 DeepSeek API 分析 URL 网页文本中的指标数据。
    """

    def __init__(self, api_key: str | None = None):
        """
        Args:
            api_key: DeepSeek API Key，不传则从环境变量 DEEPSEEK_API_KEY 读取
        """
        self.api_key = api_key or DEEPSEEK_API_KEY
        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY 未设置，AI 分析功能不可用")

    async def analyze(
        self,
        category1: str,
        category2: str,
        category3: str,
        indicator_name: str,
        target_value: float | None,
        unit: str | None,
        web_text: str,
    ) -> dict[str, Any]:
        """
        分析指标数据是否存在于网页文本中。

        Args:
            category1: 一级标题
            category2: 二级标题
            category3: 三级标题
            indicator_name: 数据项名称（四级标题）
            target_value: 目标数值
            unit: 单位
            web_text: URL 网页纯文本

        Returns:
            {
                "exists": bool,
                "confidence": str,
                "matched_text": str | None,
                "matched_value": float | None,
                "calculation": str | None,
                "calculation_detail": str | None,
                "explanation": str,
            }
        """
        if not self.api_key:
            return {
                "exists": False,
                "confidence": "low",
                "matched_text": None,
                "matched_value": None,
                "calculation": None,
                "calculation_detail": None,
                "explanation": "DeepSeek API Key 未配置，无法进行 AI 分析",
            }

        # 构建指标层级字符串
        hierarchy_parts = []
        if category1:
            hierarchy_parts.append(f"一级标题：{category1}")
        if category2:
            hierarchy_parts.append(f"二级标题：{category2}")
        if category3:
            hierarchy_parts.append(f"三级标题：{category3}")
        hierarchy_parts.append(f"数据项：{indicator_name}")

        hierarchy_str = "\n".join(hierarchy_parts)

        # 构建目标值信息
        target_str = f"{target_value}" if target_value is not None else "未提供"
        if unit:
            target_str += f" {unit}"

        # 构建用户消息
        user_message = f"""## 指标层级
{hierarchy_str}

## 目标值
{target_str}

## 网页纯文本
{web_text[:15000]}  # 限制文本长度，避免超出 token 限制
"""

        # 调用 DeepSeek API
        try:
            result = await self._call_deepseek(user_message)
            return result
        except Exception as e:
            logger.exception("AI 分析调用失败")
            return {
                "exists": False,
                "confidence": "low",
                "matched_text": None,
                "matched_value": None,
                "calculation": None,
                "calculation_detail": None,
                "explanation": f"AI 分析调用失败: {e}",
            }

    async def _call_deepseek(self, user_message: str) -> dict[str, Any]:
        """调用 DeepSeek API。"""
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                DEEPSEEK_API_URL,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_message},
                    ],
                    "temperature": 0.1,  # 低温度，提高确定性
                    "max_tokens": 2000,
                    "response_format": {"type": "json_object"},
                },
            )

            if resp.status_code != 200:
                error_detail = resp.text
                try:
                    error_json = resp.json()
                    error_detail = error_json.get("error", {}).get("message", resp.text)
                except Exception:
                    pass
                raise RuntimeError(f"DeepSeek API 返回错误 (HTTP {resp.status_code}): {error_detail}")

            data = resp.json()
            content = data["choices"][0]["message"]["content"]

            # 解析 JSON 响应
            try:
                result = json.loads(content)
            except json.JSONDecodeError:
                # 尝试从文本中提取 JSON
                import re
                json_match = re.search(r'\{.*\}', content, re.DOTALL)
                if json_match:
                    result = json.loads(json_match.group())
                else:
                    raise ValueError(f"无法解析 AI 响应为 JSON: {content[:200]}")

            return result

    @staticmethod
    def format_result(result: dict[str, Any]) -> str:
        """将 AI 分析结果格式化为可读文本。"""
        exists = result.get("exists", False)
        confidence = result.get("confidence", "low")
        matched_text = result.get("matched_text")
        matched_value = result.get("matched_value")
        calculation = result.get("calculation")
        calculation_detail = result.get("calculation_detail")
        explanation = result.get("explanation", "")

        lines = []
        lines.append(f"📊 AI 分析结果")
        lines.append(f"{'=' * 40}")
        lines.append(f"数据存在: {'✅ 是' if exists else '❌ 否'}")
        lines.append(f"置信度: {confidence}")

        if matched_text:
            lines.append(f"\n匹配文本: {matched_text[:200]}")
        if matched_value is not None:
            lines.append(f"匹配数值: {matched_value}")
        if calculation:
            lines.append(f"\n运算逻辑: {calculation}")
        if calculation_detail:
            lines.append(f"运算详情: {calculation_detail}")
        if explanation:
            lines.append(f"\n分析说明: {explanation}")

        return "\n".join(lines)
