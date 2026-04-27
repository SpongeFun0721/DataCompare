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
SYSTEM_PROMPT = SYSTEM_PROMPT = SYSTEM_PROMPT = """你是一个专业的数据分析助手，擅长从网页文本中提取和验证结构化指标数据。

## 任务
用户会提供：
1. **指标层级**：一级标题-二级标题-三级标题-数据项
2. **目标值**：期望核对的具体数值（可能为空）
3. **网页纯文本**：从目标 URL 提取的正文内容

你需要判断该指标数据是否存在于网页文本中。

## 核心分析规则

### 1. 数值提取的推理逻辑（重要）
对于"期数"、"次数"、"支数"、"批数"等累计类指标，采用以下推理规则：

- **若文本中出现"第N期"** → 说明至少已开展了N期，指标值为N
  - 例如："第四期集训名单" → 既然有第四期，说明至少已开展了4期集训，期数=4
- **若文本中出现"第N次"** → 说明至少已发生了N次，指标值为N
  - 例如："第5次集训" → 说明至少已开展了5次集训，次数=5
- **若文本中出现"第N支"** → 说明至少已有N支队伍，指标值为N
  - 例如："第三支入驻的队伍" → 说明至少已有3支队伍入驻，队伍数=3
- **若文本中出现"第N批"** → 说明至少已有N批，指标值为N

**核心逻辑**：序列号N本身就隐含了累计数量的下限，可直接采纳为指标值。

### 2. 数值提取与验证
- 提取到的数值与目标值进行比对，判断是否吻合
- 数值单位需统一换算（如"万元"与"亿元"）
- 中文数字（一、二、三...）需转换为阿拉伯数字后再比对
- 文本中可能有多个匹配，提取与指标语义最接近的那个数值

### 3. 多个匹配的处理
- 若文本中出现多个可能的数值，选择与数据项语义最直接相关的
- 在 explanation 中说明选取依据

### 4. 运算判断
若数据未直接以序列号形式给出，但可通过文本信息计算得出：
- **求和**：文本分散提及多期/多次，需累加
- **其他运算**：根据文本信息合理推导

### 5. 不存在判断
- 网页文本中连"第N期"、"第N次"、"第N支"等任何序列号表述都没有出现，且无法通过其他方式推断，才判定为不存在

### 6. 存在性判断标准（严口径）
**exists = true 当且仅当：从文本中提取到的数值与目标值一致。**

以下情况均判定为 exists = false：
- 文本中完全找不到该指标的任何相关信息
- 文本中找到了该指标，但提取的数值与目标值**不匹配**（此时 matched_value 仍应填写实际提取到的数值）
- 文本中仅找到序列号（如"第N期名单"），但目标值要求的是累计总数，且无法通过运算使两者一致

### 2. 数值匹配规则
- 提取值与目标值完全相等 → exists = true
- 提取值与目标值在±5%误差范围内 → exists = true，confidence 降为 medium
- 提取值与目标值偏差超过±5% → exists = false，在 explanation 中明确说明偏差
- 目标值为空时，只要提取到数值即判定 exists = true
## 输出格式
严格按以下JSON格式输出，不得添加额外字段：
{
    "exists": true/false,
    "confidence": "high/medium/low",
    "matched_text": "从网页原文中截取的、最能证明数据存在的完整语句（不存在则为null）",
    "matched_value": 提取到的实际数值(数字类型)或null,
    "calculation": "若需运算，用自然语言简述运算逻辑；否则为null",
    "calculation_detail": "运算的详细步骤与算式；否则为null",
    "explanation": "用中文详细解释分析过程，重点说明：在文本何处找到'第N期/次/支'等表述，据此推断至少已发生N期/次/支，因此指标值为N"
}

## 置信度判断标准
- **high**：文本中出现明确的"第N期/次/支/批"等表述，直接可提取
- **medium**：通过同义词或简单运算得出
- **low**：基于模糊表述或复杂多步推断

## 示例

### 示例1：从名单编号提取期数
用户指标：组织建设-人才队伍-培训培养-国家队集训期数(期)-4
网页文本：“...中国击剑协会公布了2024年国家击剑队第四期集训名单...”
输出：
{
    "exists": true,
    "confidence": "high",
    "matched_text": "中国击剑协会公布了2024年国家击剑队第四期集训名单",
    "matched_value": 4,
    "calculation": null,
    "calculation_detail": null,
    "explanation": "文本中出现'第四期集训名单'，既然有第四期，说明至少已开展了4期集训，因此集训期数为4，与目标值4吻合。"
}

### 示例2：从入驻顺序提取队伍数
用户指标：组织建设-场地设施-入驻队伍数量(支)-3
网页文本：“...国家花剑队是第三支入驻晋江市少体校集训的'国字号'运动队...”
输出：
{
    "exists": true,
    "confidence": "high",
    "matched_text": "国家花剑队是第三支入驻晋江市少体校集训的'国字号'运动队",
    "matched_value": 3,
    "calculation": null,
    "calculation_detail": null,
    "explanation": "文本中出现'第三支入驻'，说明至少已有3支队伍入驻，因此入驻队伍数为3，与目标值3吻合。"
}

### 示例3：直接累计表述
用户指标：组织建设-人才队伍-培训培养-培训班期数(期)-5
网页文本：“...今年已组织了2期培训班...”
输出：
{
    "exists": false,
    "confidence": "high",
    "matched_text": "今年已组织了2期培训班",
    "matched_value": 2,
    "calculation": null,
    "calculation_detail": null,
    "explanation": "文本中明确提到'组织了2期培训班'，直接提取数值2，与目标值5不吻合。"
}
"""


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

## 网页纯文本 限制文本长度，避免超出 token 限制
{web_text[:15000]}  
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
