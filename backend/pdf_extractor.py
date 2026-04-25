"""
PDF 文本提取模块

使用 pdfplumber 提取 PDF 的纯文本内容和表格数据。
支持逐页提取，保留页码信息用于后续定位。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pdfplumber
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class PageText:
    """单页文本数据"""

    page_number: int        # 页码（从 1 开始）
    text: str               # 该页纯文本
    char_offset: int = 0    # 在全文中的字符偏移量


@dataclass
class TableData:
    """表格数据"""

    page_number: int                    # 所在页码
    table_index: int                    # 该页中的第几个表格
    dataframe: pd.DataFrame = field(default_factory=pd.DataFrame)  # 解析后的 DataFrame
    raw_data: list[list] = field(default_factory=list)             # 原始二维列表


class PDFTextExtractor:
    """
    PDF 文本提取器

    功能：
    1. 逐页提取纯文本，保留段落结构和页码信息
    2. 识别并单独提取表格数据
    3. 合并全文文本，记录每页的字符偏移量

    使用示例：
        extractor = PDFTextExtractor()
        pages = extractor.extract_text("report.pdf")
        full_text = extractor.get_full_text(pages)
        tables = extractor.extract_tables("report.pdf")
    """

    def extract_text(self, pdf_path: str | Path) -> list[PageText]:
        """
        提取 PDF 全文，逐页返回。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            按页码排列的 PageText 列表
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

        pages: list[PageText] = []
        char_offset = 0

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    text = page.extract_text() or ""
                    text = text.replace(" ", "").replace("　", "")
                    page_text = PageText(
                        page_number=i + 1,
                        text=text,
                        char_offset=char_offset,
                    )
                    pages.append(page_text)
                    # 每页之间加换行符，+1 用于换行符
                    char_offset += len(text) + 1

            logger.info(f"成功提取 {pdf_path.name}：共 {len(pages)} 页")
        except Exception as e:
            logger.error(f"提取 PDF 文本失败 [{pdf_path.name}]: {e}")
            raise

        return pages

    def extract_tables(self, pdf_path: str | Path) -> list[TableData]:
        """
        提取 PDF 中的所有表格。

        Args:
            pdf_path: PDF 文件路径

        Returns:
            所有检测到的表格数据列表
        """
        pdf_path = Path(pdf_path)
        tables: list[TableData] = []

        try:
            with pdfplumber.open(str(pdf_path)) as pdf:
                for i, page in enumerate(pdf.pages):
                    page_tables = page.extract_tables()
                    if not page_tables:
                        continue

                    for j, raw_table in enumerate(page_tables):
                        if not raw_table or len(raw_table) < 2:
                            continue

                        # 第一行作为表头，后续行作为数据
                        header = [
                            str(cell).strip() if cell else f"列{k}"
                            for k, cell in enumerate(raw_table[0])
                        ]
                        data_rows = raw_table[1:]

                        df = pd.DataFrame(data_rows, columns=header)
                        tables.append(TableData(
                            page_number=i + 1,
                            table_index=j,
                            dataframe=df,
                            raw_data=raw_table,
                        ))

            logger.info(f"从 {pdf_path.name} 中提取到 {len(tables)} 个表格")
        except Exception as e:
            logger.error(f"提取表格失败 [{pdf_path.name}]: {e}")

        return tables

    @staticmethod
    def get_full_text(pages: list[PageText]) -> str:
        """
        将逐页文本合并为全文。

        Args:
            pages: PageText 列表

        Returns:
            合并后的全文字符串
        """
        return "\n".join(p.text for p in pages)

    @staticmethod
    def get_page_for_position(pages: list[PageText], position: int) -> int:
        """
        根据字符位置反查页码。

        Args:
            pages: PageText 列表
            position: 在全文中的字符位置

        Returns:
            页码（从 1 开始）
        """
        for p in reversed(pages):
            if position >= p.char_offset:
                return p.page_number
        return 1
