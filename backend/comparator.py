"""
比对逻辑核心模块

将 Excel 指标与 PDF 提取的数值进行比对，
综合模糊匹配分数、距离、单位一致性计算置信度。

核心流程（v2 - 按 source_file 来源搜索）：
1. 预处理：提取所有 PDF 的文本和数值（缓存）
2. 对每个 Excel 指标，解析 source_file 获取来源列表（PDF+页码 或 URL）
3. 只在这些来源的指定页码中搜索数值
4. 找不到直接标记"未找到"，不做全文兜底
"""

from __future__ import annotations

import json
import logging
import html
import re
from pathlib import Path

from backend.config import (
    TOLERANCE, SIMILARITY_THRESHOLD,
    WEIGHT_FUZZY_SCORE, WEIGHT_DISTANCE, WEIGHT_UNIT_CONSISTENCY,
    PDF_CACHE_DIR,
)
from backend.models import (
    Indicator, MatchResult, IndicatorResult, ProgressInfo, AnalysisResponse, SourcePage,
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
    2. 对每个 Excel 指标，解析 source_file 获取来源列表
    3. 只在这些来源的指定页码中搜索数值
    4. 找不到直接标记"未找到"
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

    # ============================================================
    # 缓存管理（不变）
    # ============================================================

    def _get_cache_path(self, pdf_path: Path) -> Path:
        """获取 PDF 对应的磁盘缓存文件路径。"""
        cache_name = pdf_path.stem + ".json"
        return PDF_CACHE_DIR / cache_name

    def _load_from_disk_cache(self, pdf_path: Path) -> bool:
        """尝试从磁盘缓存加载预处理数据。"""
        cache_path = self._get_cache_path(pdf_path)
        if not cache_path.exists():
            return False

        # 检查缓存是否过期（PDF 文件更新后应重新提取）
        pdf_mtime = pdf_path.stat().st_mtime
        cache_mtime = cache_path.stat().st_mtime
        if cache_mtime < pdf_mtime:
            logger.info(f"缓存过期，将重新提取: {pdf_path.name}")
            return False

        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            name = pdf_path.name
            pages = [
                PageText(
                    page_number=p["page_number"],
                    text=p["text"],
                    char_offset=p["char_offset"],
                )
                for p in data["pages"]
            ]
            full_text = data["full_text"]
            numbers = [
                ExtractedNumber(
                    value=n["value"],
                    raw_str=n["raw_str"],
                    unit=n.get("unit"),
                    position=n["position"],
                    context=n["context"],
                    context_start=n.get("context_start", n["position"]),
                )
                for n in data["numbers"]
            ]


            self._cache[name] = (pages, numbers, full_text)
            logger.info(f"从磁盘缓存加载 [{name}]: {len(pages)} 页, {len(numbers)} 个数值")
            return True
        except Exception as e:
            logger.warning(f"读取磁盘缓存失败 [{pdf_path.name}]: {e}")
            return False

    def _save_to_disk_cache(self, pdf_path: Path) -> None:
        """将预处理数据保存到磁盘缓存。"""
        name = pdf_path.name
        if name not in self._cache:
            return

        pages, numbers, full_text = self._cache[name]
        data = {
            "pages": [
                {"page_number": p.page_number, "text": p.text, "char_offset": p.char_offset}
                for p in pages
            ],
            "full_text": full_text,
            "numbers": [
                {
                    "value": n.value,
                    "raw_str": n.raw_str,
                    "unit": n.unit,
                    "position": n.position,
                    "context": n.context,
                }
                for n in numbers
            ],
        }

        cache_path = self._get_cache_path(pdf_path)
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            logger.debug(f"预处理数据已缓存到磁盘: {cache_path.name}")
        except Exception as e:
            logger.warning(f"写入磁盘缓存失败 [{cache_path.name}]: {e}")

    def preprocess_pdf(self, pdf_path: str | Path) -> None:
        """预处理单个 PDF：提取文本和数值，存入缓存。"""
        pdf_path = Path(pdf_path)
        name = pdf_path.name

        if name in self._cache:
            return

        # 优先从磁盘缓存加载
        if self._load_from_disk_cache(pdf_path):
            return

        pages = self.extractor.extract_text(pdf_path)
        full_text = self.extractor.get_full_text(pages)
        numbers = self.finder.find_all_numbers(full_text)

        self._cache[name] = (pages, numbers, full_text)
        logger.info(f"预处理完成 [{name}]: {len(pages)} 页, {len(numbers)} 个数值")

        # 保存到磁盘缓存
        self._save_to_disk_cache(pdf_path)


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

    # ============================================================
    # source_file 解析
    # ============================================================

    @staticmethod
    def _parse_source_file(source_file: str | None) -> list[SourcePage]:
        """
        从 source_file 中解析出所有数据来源。

        解析规则（不依赖分隔符，直接用正则提取）：
        1. URL 模式：https?://[^\s;；，,\n]+
        2. +P页码 模式：([^;；\n]+?)\+P(\d+)
           - 先用分隔符分割，再对每段提取 +P页码，避免跨段匹配
           - core_name 包含"年鉴" → yearbook
           - 否则 → report
        3. 去重：同一类型、同一 core_name、同一 page 的只保留一个
        """
        if not source_file:
            return []

        seen: set[tuple[str, str | None, int | None]] = set()
        results: list[SourcePage] = []

        # 模式 1：提取 URL（同时提取前面的年份标签，如 "2022年："）
        url_pattern = re.compile(r'(?:(\d{4})\s*年\s*[：:]\s*)?(https?://[^\s;；，,\n]+)')
        for match in url_pattern.finditer(source_file):
            year_str = match.group(1)  # 年份数字，如 "2022"
            url = match.group(2).strip()
            year_label = f"{year_str}年" if year_str else None
            # 使用 URL 作为 key 的一部分，允许多个不同的 URL
            key = ("url", url, None)
            if key not in seen:
                seen.add(key)
                results.append(SourcePage(
                    source_type="url",
                    url=url,
                    year_label=year_label,
                ))

        # 模式 2：提取 +P页码
        # 先用分隔符分割，再对每段提取 +P页码，避免跨段匹配
        segments = re.split(r'[;；\n]+', source_file)
        for segment in segments:
            segment = segment.strip()
            if not segment:
                continue
            # 跳过 URL 段
            if re.match(r'^https?://', segment):
                continue
            # 对每段提取 +P页码
            page_pattern = re.compile(r'(.+?)\+P(\d+)')
            for match in page_pattern.finditer(segment):
                core_name = match.group(1).strip()
                page = int(match.group(2))
                source_type = "yearbook" if "年鉴" in core_name else "report"
                key = (source_type, core_name, page)
                if key not in seen:
                    seen.add(key)
                    results.append(SourcePage(
                        source_type=source_type,
                        core_name=core_name,
                        page=page,
                    ))

        return results

    # ============================================================
    # PDF 文件名匹配
    # ============================================================

    # 定义忽略词（这些词的变化不影响核心身份）
    IGNORE_WORDS = [
        '关于', '报送', '报告', '请示', '思路', '重点', '打算',
        '及', '与', '和',  # 连词归一化
        '年度', '年终', '的', '情况', '工作','（盖章版）'  # 常见但无区分的词
    ]

    @staticmethod
    def _normalize(text: str) -> str:
        """归一化文本：去噪、统一连词、去除非核心特征"""
        # 1. 去除后缀
        text = re.sub(r'(_ocr|_trans)?\.pdf$', '', text)
        
        # 2. 去除编号前缀 (如 "10.")
        text = re.sub(r'^\d+\.', '', text)
        
        # 3. 统一连词：将所有连词(及/与)替换为"和"
        text = text.replace('及', '和').replace('与', '和')
        
        # 4. 去除忽略词
        for word in Comparator.IGNORE_WORDS:
            text = text.replace(word, '')
        
        # 5. 去除多余空格
        text = re.sub(r'\s+', '', text)
        
        return text

    @staticmethod
    def _extract_key_elements(name: str) -> dict:
        """提取关键元素：机构名、年份对"""
        # 提取机构名 (协会/中心/司/大学等)
        org_match = re.search(r'([\u4e00-\u9fa5]+(?:协会|中心|司|大学|集团|基金会|学校|报社|俱乐部|联合会))', name)
        org = org_match.group(1) if org_match else ""
        
        # 提取年份对（两个连续的4位数字）
        years = re.findall(r'(\d{4})', name)
        year_pair = tuple(years[:2]) if len(years) >= 2 else ()
        
        return {
            'org': org,
            'year_pair': year_pair,
            'normalized': Comparator._normalize(name)
        }

    @staticmethod
    def _match_pdf_name(core_name: str, pdf_names: list[str]) -> str | None:
        """
        根据 core_name 在 pdf_names 中查找匹配的实际 PDF 文件名。
        使用机构名+年份对+归一化文本相似度综合打分。
        """
        # 提取 core_name 的关键元素
        core_elem = Comparator._extract_key_elements(core_name)
        
        best_match = None
        best_score = 0
        
        for pdf_name in pdf_names:
            pdf_elem = Comparator._extract_key_elements(pdf_name)
            score = 0
            
            # 1. 机构名完全匹配 (权重 40)
            if core_elem['org'] and core_elem['org'] == pdf_elem['org']:
                score += 40
            elif core_elem['org'] and pdf_elem['org'] and core_elem['org'] in pdf_elem['org']:
                score += 20  # 部分匹配（如"中国足协" vs "中国足球协会"）
            
            # 2. 年份对完全匹配 (权重 50，最高)
            if core_elem['year_pair'] and core_elem['year_pair'] == pdf_elem['year_pair']:
                score += 50
            elif len(core_elem['year_pair']) == 2 and len(pdf_elem['year_pair']) == 2:
                # 部分年份匹配（2022→2023 vs 2022→2024）得分较低
                if core_elem['year_pair'][0] == pdf_elem['year_pair'][0]:
                    score += 25
                if core_elem['year_pair'][1] == pdf_elem['year_pair'][1]:
                    score += 25
            
            # 3. 归一化后的文本相似度（基于字符交集比例）
            common_len = len(set(core_elem['normalized']) & set(pdf_elem['normalized']))
            max_len = max(len(core_elem['normalized']), len(pdf_elem['normalized']))
            if max_len > 0:
                score += (common_len / max_len) * 30
            
            # 更新最佳匹配
            if score > best_score:
                best_score = score
                best_match = pdf_name
        
        # 阈值：至少 30 分才认为是有效匹配（避免乱匹配）
        return best_match if best_score >= 30 else None

    # ============================================================
    # 年份匹配
    # ============================================================

    @staticmethod
    def _is_year_match(ind_year: str, pdf_name: str) -> bool:
        """
        判断指标所属年份是否与 PDF 文件名中的年份匹配。
        - 司局报告文件名通常包含两个年份（如 "2022年工作总结和2023年工作计划"），
          只使用前面的年份（总结年份）进行匹配
        - 例如 PDF 名为 "10.中国击剑协会关于报送2022年工作总结和2023年工作计划的报告_ocr.pdf"
          前面的年份为 2022，指标年份为 2022 时应匹配成功
        - 如果无法提取年份，则默认匹配（返回 True）
        """
        if not ind_year:
            return True

        ind_match = re.search(r"(\d{4})", ind_year)
        if not ind_match:
            return True

        target_year = ind_match.group(1)

        # 提取 PDF 文件名中所有年份
        pdf_years = re.findall(r"(\d{4})", pdf_name)
        if not pdf_years:
            return True

        # 只使用前面的年份（总结年份）进行匹配
        first_year = pdf_years[0]
        return target_year == first_year


    # ============================================================
    # 核心比对方法
    # ============================================================

    def compare_indicator(
        self,
        indicator: Indicator,
        pdf_names: list[str] | None = None,
        yearbook_index: dict[str, str] | None = None,
    ) -> IndicatorResult:
        """
        比对单个指标在 source_file 指定来源中的匹配情况。

        Args:
            indicator: Excel 指标
            pdf_names: 已预处理的 PDF 文件名列表
            yearbook_index: 年鉴 PDF 索引，{年份: 文件名}，用于按年份匹配年鉴

        Returns:
            IndicatorResult
        """
        if pdf_names is None:
            pdf_names = list(self._cache.keys())

        result = IndicatorResult(indicator=indicator)

        # ============================================================
        # 确定来源类型和来源文本
        # 颜色映射决定了该指标使用 source_file 中的哪部分内容：
        #   - report: 只处理司局报告（+P页码，不含"年鉴"）
        #   - yearbook: 只处理年鉴（+P页码，含"年鉴"）
        #   - url: 只处理 URL
        # 三个字段互斥，由 /api/analyze 根据 color_mapping 设置
        # ============================================================
        if indicator.source_file_yearbook:
            source_type = "yearbook"
            source_text = indicator.source_file_yearbook
        elif indicator.source_file_report:
            source_type = "report"
            source_text = indicator.source_file_report
        elif indicator.source_file_url:
            source_type = "url"
            source_text = indicator.source_file_url
        else:
            # 无来源 → 未核对（无法自动判断，留待用户核对）
            print(f"    ⚠️ [{indicator.name}] 无任何来源字段，标记为未核对")
            indicator.review_status = "未核对"
            return result

        # 解析来源文本
        source_pages = self._parse_source_file(source_text)
        indicator.source_pages = source_pages

        print(f"\n>>> compare_indicator: [{indicator.name}] (id={indicator.id}, year='{indicator.year}', target={indicator.target_value})")
        print(f"    来源类型: {source_type}")
        print(f"    来源文本: {source_text}")
        print(f"    解析出 {len(source_pages)} 个来源:")
        for sp in source_pages:
            print(f"      [{sp.source_type}] core_name='{sp.core_name}', page={sp.page}, url={sp.url}")

        if not source_pages:
            print(f"    ⚠️ 来源文本为空或无法解析，标记为未核对")
            indicator.review_status = "未核对"
            return result

        # ============================================================
        # 根据颜色映射的 source_type 过滤 source_pages
        # 例如：颜色映射为 "url"，则只处理 URL 来源，忽略 PDF 来源
        # 例如：颜色映射为 "report"，则只处理 report 来源，忽略 URL 和 yearbook
        # ============================================================
        # 只处理与 source_type 匹配的来源
        filtered_pages = [sp for sp in source_pages if sp.source_type == source_type]

        # 如果过滤后为空，说明 source_file 中不包含该类型的来源
        if not filtered_pages:
            print(f"    ⚠️ 来源文本中未找到 {source_type} 类型的来源")
            # 但仍然记录所有解析出的来源到 best_matches，以便前端显示
            for sp in source_pages:
                key = sp.url if sp.source_type == "url" else sp.core_name
                if key:
                    result.best_matches[key] = None
            indicator.review_status = "未核对"
            return result

        # 标记是否有 PDF 来源被成功处理
        has_pdf_processed = False

        for sp in filtered_pages:
            if sp.source_type == "url":
                print(f"    ⏭️ URL 来源，跳过: {sp.url}")
                indicator.matched_source_type = "url"
                continue

            if sp.source_type == "yearbook" and yearbook_index:
                # 年鉴特判：不按年份严格匹配
                # 2022 年鉴也可能包含 2021 的数据，所以直接根据 AI 来源行显示对应的年鉴
                # 优先从 core_name 中提取年份匹配
                year_match = re.search(r'(\d{4})', sp.core_name)
                if year_match:
                    year_str = year_match.group(1)
                    matched_name = yearbook_index.get(year_str)
                if not matched_name:
                    # 尝试用指标年份匹配
                    ind_year_match = re.search(r'(\d{4})', indicator.year)
                    if ind_year_match:
                        matched_name = yearbook_index.get(ind_year_match.group(1))
                if not matched_name:
                    # 使用默认年鉴
                    matched_name = yearbook_index.get("default")
                if not matched_name and yearbook_index:
                    # 兜底：取第一个可用的年鉴
                    matched_name = list(yearbook_index.values())[0]
            else:
                # 司局报告：使用原有的匹配逻辑
                matched_name = self._match_pdf_name(sp.core_name, pdf_names)

            if not matched_name:
                print(f"    ⏭️ [{sp.core_name}] 未匹配到任何 PDF 文件")
                # 仍然记录到 best_matches，标记为 None，以便前端知道这个来源
                result.best_matches[sp.core_name] = None
                continue

            if matched_name not in self._cache:
                print(f"    ⏭️ [{matched_name}] 不在缓存中")
                # 仍然记录到 best_matches，标记为 None，以便前端知道这个 PDF 被关联了
                result.best_matches[matched_name] = None
                continue

            # 年份过滤（仅对司局报告生效，年鉴特判不按年份过滤）
            if sp.source_type != "yearbook":
                if not self._is_year_match(indicator.year, matched_name):
                    print(f"    ⏭️ [{matched_name}] 年份不匹配: indicator.year='{indicator.year}'")
                    # 仍然记录到 best_matches，标记为 None，以便前端知道这个 PDF 被关联了
                    result.best_matches[matched_name] = None
                    continue

            # 在指定页及前后一页查找匹配，取最先匹配到的页码
            pages_to_try = [sp.page]
            if sp.page > 1:
                pages_to_try.append(sp.page - 1)
            pages_to_try.append(sp.page + 1)

            matched_page = None
            all_matches: list[MatchResult] = []
            for try_page in pages_to_try:
                print(f"    ✅ [{matched_name}] 第 {try_page} 页 开始匹配...")
                page_matches = self._find_matches_on_page(indicator, matched_name, try_page)
                if page_matches:
                    matched_page = try_page
                    all_matches = page_matches
                    print(f"    ✅ [{matched_name}] 第 {try_page} 页 匹配成功（最先匹配到的页码）")
                    break
                else:
                    print(f"    ❌ [{matched_name}] 第 {try_page} 页 未找到匹配")

            result.matches[matched_name] = all_matches

            # 无论是否匹配到数值，都记录来源信息到 indicator 级别
            indicator.matched_source_type = sp.source_type
            indicator.matched_pdf_name = matched_name
            indicator.matched_page = matched_page or sp.page
            has_pdf_processed = True

            # 取置信度最高的作为 best match
            if all_matches:
                best = max(all_matches, key=lambda m: m.confidence)
                result.best_matches[matched_name] = best
            else:
                result.best_matches[matched_name] = None

        # 自动判断状态：匹配到的自动已核对，未匹配到的保持未核对（留待用户判断）
        has_any = any(m is not None for m in result.best_matches.values())
        if not has_any:
            indicator.review_status = "未核对"
        else:
            # 统计所有 PDF 中的绝对数值匹配（is_match 为 True）数量
            exact_matches = []
            for pdf_matches in result.matches.values():
                exact_matches.extend([m for m in pdf_matches if m.is_match])

            # 若全局仅出现 1 次完全匹配，且置信度较高，则自动确认为"已核对"
            if len(exact_matches) == 1 and exact_matches[0].confidence >= 70.0:
                indicator.review_status = "已核对"

        return result

    def _find_matches_on_page(
        self,
        indicator: Indicator,
        pdf_name: str,
        page_number: int,
    ) -> list[MatchResult]:
        """
        在 PDF 的指定页码中查找指标的所有匹配。

        匹配策略（值优先）：
        1. 在该页文本中查找所有与 Excel 目标值相等的数值
        2. 对每个值匹配，做模糊匹配验证
        3. 按模糊分数降序排列
        """
        pages, numbers, full_text = self._cache[pdf_name]

        # 找到指定页码的文本
        page_text = None
        for p in pages:
            if p.page_number == page_number:
                page_text = p.text
                break

        if page_text is None:
            print(f"    ⚠️ 第 {page_number} 页不存在（PDF 共 {len(pages)} 页）")
            return []

        target = indicator.target_value

        print(f"    === [{pdf_name}] 第 {page_number} 页 匹配指标: {indicator.name}, target_value={target} ===")
        print(f"    该页文本长度: {len(page_text)} 字符")

        # 在该页文本中搜索目标数值
        target_numbers = self.finder.find_target_number(page_text, target, indicator.name)
        print(f"    值匹配（target={target}）找到: {len(target_numbers)} 个")
        for i, num in enumerate(target_numbers):
            print(f"      [{i}] value={num.value}, raw_str='{num.raw_str}', pos={num.position}")
            print(f"          context: ...{num.context[max(0, num.position-50):num.position+50]}...")

        if not target_numbers:
            print(f"    ❌ 第 {page_number} 页未找到数值匹配")
            return []

        # 对每个值匹配做模糊匹配验证
        scored: list[tuple[ExtractedNumber, float]] = []
        for num in target_numbers:
            match_result = self.matcher.match(
                indicator.name, num.context, indicator.aliases
            )
            print(f"      模糊匹配: raw_str='{num.raw_str}', score={match_result.score}")
            scored.append((num, match_result.score))

        # 按模糊分数降序
        scored.sort(key=lambda x: x[1], reverse=True)

        # 去重：相近位置的合并（80 字符内视为同一处）
        deduplicated: list[tuple[ExtractedNumber, float]] = []
        for num, fuzzy_score in scored:
            is_dup = False
            for existing_num, _ in deduplicated:
                if (abs(num.position - existing_num.position) < 80
                        and abs(num.value - existing_num.value) < 0.001):
                    is_dup = True
                    break
            if not is_dup:
                deduplicated.append((num, fuzzy_score))

        total = len(deduplicated)
        results: list[MatchResult] = []

        for idx, (num, fuzzy_score) in enumerate(deduplicated):
            # 置信度：值匹配基线 70 分，模糊匹配再加分
            confidence = 70.0 + 0.3 * fuzzy_score

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

    # ============================================================
    # 辅助方法（不变）
    # ============================================================

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

    # ============================================================
    # 分析入口（不变）
    # ============================================================

    def run_analysis_on_preprocessed(
        self,
        indicators: list[Indicator],
        pdf_names: list[str],
        yearbook_index: dict[str, str] | None = None,
    ) -> AnalysisResponse:
        """
        对已预处理的 PDF 执行比对分析（不重新扫描目录）。

        Args:
            indicators: Excel 指标列表
            pdf_names: 已预处理的 PDF 文件名列表
            yearbook_index: 年鉴 PDF 索引，{年份: 文件名}

        Returns:
            AnalysisResponse 完整的分析结果
        """
        results: list[IndicatorResult] = []
        for indicator in indicators:
            result = self.compare_indicator(indicator, pdf_names, yearbook_index)
            results.append(result)

        progress = self._calc_progress(indicators)

        return AnalysisResponse(
            indicators=indicators,
            pdf_names=pdf_names,
            results=results,
            progress=progress,
        )

    def run_full_analysis(
        self,
        indicators: list[Indicator],
        pdf_dir: str | Path,
        yearbook_index: dict[str, str] | None = None,
    ) -> AnalysisResponse:
        """
        执行完整的比对分析。

        Args:
            indicators: Excel 指标列表
            pdf_dir: PDF 文件目录
            yearbook_index: 年鉴 PDF 索引，{年份: 文件名}

        Returns:
            AnalysisResponse 完整的分析结果
        """
        pdf_names = self.preprocess_pdfs(pdf_dir)

        results: list[IndicatorResult] = []
        for indicator in indicators:
            result = self.compare_indicator(indicator, pdf_names, yearbook_index)
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
        """计算核对进度。仅统计已核对。"""
        total = len(indicators)
        confirmed = sum(1 for i in indicators if i.review_status == "已核对")
        unchecked = total - confirmed

        return ProgressInfo(
            total=total,
            confirmed=confirmed,
            unchecked=unchecked,
        )
