"""标题提取:从 OCR 输出的 .md 中提取论文标题(不改 OCR 调用)。"""

from __future__ import annotations

import re

from paper_prep.config import FILTER_PREFIXES, MIN_TITLE_LEN


def extract_title_from_md(md_path: str) -> str | None:
    """从 OCR 输出的 .md 中提取论文标题。

    策略(与旧 ocr_doi_matcher.py 完全一致,勿随意简化):
      1. 优先找第一个 # 标题(真正的论文标题,非 ## 节标题)
      2. 否则遍历非空行:过滤特征词前缀、编号前缀、太短的行
      3. 含学术特征词(survey/review/approach/method...)的行优先
      4. 返回最佳候选
    """
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return None

    best = None
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        # 跳过图片、代码块
        if line.startswith((">", "```", "![")):
            continue
        if re.match(r"^!\[.*\]\(.*\)$", line):
            continue

        # 记录原始行(判断是否 # 标题)
        raw = line
        if line.startswith("#"):
            line = re.sub(r"^#+\s*", "", line).strip()

        lower = line.lower()

        # 跳过系列名、出版社等不可能是论文标题的行(书名/章节前缀)
        if re.match(
            r"^(chapter|part|the\s+series|series\s+title|risk,\s+systems)"
            r"|series\s+editors|edited\s+by|editors?\b|foreword|preface|"
            r"contents?|contributors|about\s+the\s+(author|editor)|index\b",
            lower, re.I,
        ):
            continue

        if len(line) < MIN_TITLE_LEN:
            continue

        lower = line.lower()
        # 跳过特征词起始的行
        if any(lower.startswith(p) for p in FILTER_PREFIXES):
            continue

        # 去掉编号前缀如 "1. ", "1.1 "
        line = re.sub(r"^\d+(\.\d+)*\s+", "", line).strip()
        if len(line) < MIN_TITLE_LEN:
            continue

        # 如果含常见学术标题词,优先
        if best is None or any(kw in lower for kw in (
                "survey", "review", "approach", "method", "framework",
                "model", "optimization", "detection", "control", "analysis",
                "design", "learning", "resilient", "based", "using", "for",
                "toward")):
            best = line

        # 如果第一行是 # 标题,直接用
        if raw.startswith("#"):
            return line

    return best