"""
工具函数：作者名标准化、匹配、DOI 格式处理等。
"""

import re
import unicodedata

from .config import AUTHOR_MATCH_CHAR_THRESHOLD


def normalize_author(name: str) -> str:
    """
    标准化作者名。

    处理:
      - Unicode NFC 规范化（é 单字符 vs e+组合符号 → 统一）
      - 统一各类连字符/破折号到普通连字符
      - 去掉所有空格/连字符/下划线/点
      - 转小写

    示例:
      "Kutálek" → "kutalek"
      "Lara González‐Cabaleiro" → "lara-gonzalez---cabaleiro" → "laragonzálezcabaleiro"
    """
    name = unicodedata.normalize("NFC", name)
    name = re.sub(r"[‐‑‒–—―−]", "-", name)
    return re.sub(r"[\s\-_.‑]+", "", name.lower().strip())


def authors_match(json_author: str, cr_author: str) -> bool:
    """
    宽松的作者匹配。

    匹配策略（按优先级）:
      1. 互相包含（处理后缀/前缀差异）
      2. 姓氏相同（容忍中间名缩写/缺失）
      3. 姓氏字符级相似度 ≥ 阈值（容忍微小拼写差异）

    示例:
      "Kutalek" vs "Kutálek"            → True  (unicode 差异)
      "Kalahdaran" vs "Kaladharan"      → True  (字符级相似)
      "Jae- Eul Shim" vs "Jae-Eul Shim" → True  (空格差异)
      "Wei Zhou" vs "Wonil Nam"         → False (完全无关)
    """
    if not json_author or not cr_author:
        return False

    a = normalize_author(json_author)
    b = normalize_author(cr_author)

    if a in b or b in a:
        return True

    a_parts = a.replace(",", "").split()
    b_parts = b.replace(",", "").split()

    # 只要姓氏相同就算匹配（容忍中间名的差异）
    if len(a_parts) >= 2 and len(b_parts) >= 2:
        if a_parts[-1] == b_parts[-1]:
            return True

    # 字符级相似度（容忍微小拼写差异）
    a_last = a_parts[-1] if a_parts else ""
    b_last = b_parts[-1] if b_parts else ""
    if a_last and b_last and len(set(a_last)) >= 4 and len(set(b_last)) >= 4:
        common = sum(1 for c in a_last if c in b_last)
        if common / max(len(set(a_last)), len(set(b_last))) >= AUTHOR_MATCH_CHAR_THRESHOLD:
            return True

    return False


def doi_safe(doi: str) -> str:
    """将 DOI 转为安全文件名（/ → _）

    示例: "10.1016/j.bios.2024.117036" → "10.1016_j.bios.2024.117036"
    """
    return doi.replace("/", "_")


def strip_supplement(doi: str) -> str:
    """去掉 .s001 / .s002 / .s004 等补充材料后缀

    示例: "10.1021/acs.analchem.5c01686.s001" → "10.1021/acs.analchem.5c01686"
    """
    return re.sub(r"\.s\d{3,}$", "", doi)


def is_doi_pdf(filename: str) -> bool:
    """判断文件名是否为 DOI 格式

    示例: "10.1021_acs_analchem_2c01450.pdf" → True
    """
    return bool(re.match(r"^10[._]\d{4,}", filename))


def is_sciencedirect(filename: str) -> bool:
    """判断是否为 ScienceDirect 格式

    示例: "1-s2.0-S0956566324010431-main.pdf" → True
    """
    return filename.startswith("1-s2.0-") and filename.lower().endswith(".pdf")