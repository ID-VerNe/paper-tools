"""
文件名模糊解析器 — 从 PDF 文件名中提取作者、年份、关键词。

用法:
    result = FilenameParser.parse("Smith_2023_Quantum.pdf")
    print(result.author)     # "Smith"
    print(result.year)       # 2023
    print(result.keywords)   # ["Quantum"]
"""

import re

from .config import FUZZY_YEAR_MIN, FUZZY_YEAR_MAX, FUZZY_COMMON_WORDS


class FilenameParseResult:
    """文件名解析结果。"""

    __slots__ = ("author", "year", "keywords", "raw_segments", "confidence")

    def __init__(
        self,
        author: str = "",
        year: int | None = None,
        keywords: list[str] | None = None,
        raw_segments: list[str] | None = None,
        confidence: float = 0.0,
    ):
        self.author = author
        self.year = year
        self.keywords = keywords or []
        self.raw_segments = raw_segments or []
        self.confidence = confidence

    def __repr__(self) -> str:
        return (
            f"FilenameParseResult(author={self.author!r}, year={self.year}, "
            f"keywords={self.keywords}, confidence={self.confidence:.2f})"
        )


class FilenameParser:
    """文件名解析器，纯静态方法。"""

    @staticmethod
    def parse(filename: str) -> FilenameParseResult:
        """
        从 PDF 文件名中解析出作者、年份、关键词。

        处理模式:
          - Author_Year_Keyword.pdf
          - Year_Author_Keyword.pdf
          - Author_Year.pdf
          - Author_Keyword.pdf
          - Author_Year_Journal.pdf
        """
        # 1. 去后缀
        stem = filename
        if stem.lower().endswith(".pdf"):
            stem = stem[:-4]

        # 2. 分词
        segments = FilenameParser._split_filename(stem)
        if not segments:
            return FilenameParseResult(raw_segments=[], confidence=0.0)

        # 3. 找年份
        year = None
        year_idx = -1
        for i, seg in enumerate(segments):
            if FilenameParser._is_year(seg):
                year = int(seg)
                year_idx = i
                break

        # 4. 找作者
        # 策略: 年份存在时，优先取年份前紧邻的段，其次取年份后紧邻的段
        #       (取第一个非年份、非停用词、长度>=2 的段)
        #       年份后的段仅当后面还有非年段时才认为是作者
        #       (避免 "2023_Quantum.pdf" 把 Quantum 误判为作者，
        #        此时 Quantum 应作为关键词参与标题搜索)
        author = ""
        author_idx = -1
        if year_idx >= 0:
            for i in range(0, year_idx):
                seg = segments[i]
                if seg.isdigit():
                    continue
                if FilenameParser._is_common_word(seg):
                    continue
                if len(seg) >= 2:
                    author = seg
                    author_idx = i
                    break

            if not author:
                for i in range(year_idx + 1, len(segments)):
                    seg = segments[i]
                    if seg.isdigit():
                        continue
                    if FilenameParser._is_common_word(seg):
                        continue
                    if len(seg) >= 2 and i + 1 < len(segments):
                        author = seg
                        author_idx = i
                        break
        else:
            for i, seg in enumerate(segments):
                if seg.isdigit():
                    continue
                if FilenameParser._is_common_word(seg):
                    continue
                if len(seg) >= 2:
                    author = seg
                    author_idx = i
                    break

        # 5. 提取关键词: 去除作者和年份的剩余段
        #    同时处理连字符作者名的子段去重
        author_parts = set()
        if author and "-" in author:
            author_parts = set(author.lower().split("-"))

        keywords = []
        for i, seg in enumerate(segments):
            if i == year_idx or i == author_idx:
                continue
            if seg.isdigit():
                continue
            if FilenameParser._is_common_word(seg):
                continue
            if seg.lower() == author.lower():
                continue
            if author_parts and seg.lower() in author_parts:
                continue
            keywords.append(seg)

        # 6. 解析置信度
        confidence = 0.0
        if year is not None:
            confidence += 0.3
        if author and len(author) >= 2:
            confidence += 0.3
        if len(segments) >= 3 and year is not None:
            confidence += 0.2
        if len(segments) >= 2:
            confidence += 0.2
        confidence = min(confidence, 1.0)

        return FilenameParseResult(
            author=author,
            year=year,
            keywords=keywords,
            raw_segments=segments,
            confidence=confidence,
        )

    @staticmethod
    def _is_year(segment: str) -> bool:
        """判断是否为有效年份。"""
        if not re.match(r"^\d{4}$", segment):
            return False
        try:
            y = int(segment)
            return FUZZY_YEAR_MIN <= y <= FUZZY_YEAR_MAX
        except ValueError:
            return False

    @staticmethod
    def _is_common_word(segment: str) -> bool:
        """判断是否为不太可能是作者姓氏的常见英文词。"""
        return segment.lower() in FUZZY_COMMON_WORDS

    @staticmethod
    def _split_filename(stem: str) -> list[str]:
        """按分隔符分裂文件名，过滤空串。

        分隔符规则:
          - 下划线 _ 始终是分隔符
          - 空格 始终是分隔符
          - 连字符 - 仅当分隔符两边至少有一个是数字时才分裂
            (避免把 "Gonzalez-Cabaleiro" 这样的连字符姓分裂)
        """
        segments = []
        for part in re.split(r"[_\s]+", stem):
            if not part:
                continue
            sub = re.split(r"(?<=[A-Za-z])-(?=\d)|(?<=\d)-(?=[A-Za-z])", part)
            sub = [s for s in sub if s]
            segments.extend(sub)
        return segments