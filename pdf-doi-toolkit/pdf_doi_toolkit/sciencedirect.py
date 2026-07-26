"""
ScienceDirect 格式处理工具。

`1-s2.0-` 格式说明:
  - PII = Publisher Item Identifier, Elsevier 的内部文章标识
  - 格式: 1-s2.0-S[ISSN 8位][年份 2位][文章序号 5-6位]
  - 无法直接解码为 DOI（需要期刊缩写）
  - 必须通过 CrossRef 按标题查询真实 DOI
"""

import re


class ScienceDirectHandler:
    """
    ScienceDirect PDF 解析与说明。

    用法:
        sd = ScienceDirectHandler()
        info = sd.describe("1-s2.0-S0956566324010431-main.pdf")
        print(info)
    """

    @staticmethod
    def extract_pii(filename: str) -> str:
        """从 ScienceDirect 文件名提取 PII

        示例: "1-s2.0-S0956566324010431-main.pdf" → "S0956566324010431"
        """
        m = re.match(r"1-s2\.0-(S\d+)", filename)
        return m.group(1) if m else ""

    @staticmethod
    def parse_pii(pii: str) -> dict:
        """解析 PII 为结构化信息

        返回: {"issn": str, "year": str, "seq": str} 或 None
        """
        m = re.match(r"S(\d{8})(\d{2})(\d{5,6})", pii)
        if not m:
            return None
        return {
            "issn": m.group(1),
            "year": "20" + m.group(2),
            "seq": m.group(3),
        }

    @staticmethod
    def describe(filename: str) -> str:
        """解析文件名并返回人类可读的说明

        示例:
            "1-s2.0-S0956566324010431-main.pdf"
            → "PII: S0956566324010431 | ISSN: 09565663 | 年份: 2024 | ...
               需通过 CrossRef 按标题查询真实 DOI"
        """
        pii = ScienceDirectHandler.extract_pii(filename)
        if not pii:
            return f"{filename} — 不是 ScienceDirect 格式"

        info = ScienceDirectHandler.parse_pii(pii)
        if info:
            return (
                f"PII: {pii} | ISSN: {info['issn']} | "
                f"年份: {info['year']} | 序号: {info['seq']} | "
                f"需通过 CrossRef 按标题查询真实 DOI"
            )
        return f"PII: {pii} (格式异常)"