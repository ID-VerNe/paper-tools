"""
PDF 扫描器：遍历目录，从 full_extraction JSON 中提取元数据。

扫描逻辑:
  1. 遍历指定目录下所有 .pdf 文件
  2. 跳过已是 DOI 格式的（10.xxxx_xxx.pdf）
  3. 跳过 ScienceDirect 格式的（1-s2.0-xxxx.pdf）
  4. 查找同名文件夹下的 derived/ 中的 full_extraction JSON
  5. 提取 title、first_author、doi

注: ScienceDirect 需要额外调用 scanner.scan_sciencedirect()。
"""

import json
import os

from .utils import is_doi_pdf, is_sciencedirect


class PDFScanner:
    """
    扫描 PDF 目录并提取元数据。

    用法:
        scanner = PDFScanner("path/to/pdfs")
        entries = scanner.scan()
        # 或单独扫 ScienceDirect:
        sd_entries = scanner.scan_sciencedirect()
    """

    def __init__(self, summary_dir: str):
        self.summary_dir = summary_dir

    # ------------------------------------------------------------------
    #  扫描入口
    # ------------------------------------------------------------------

    def scan(self) -> list:
        """
        扫描所有非 DOI、非 ScienceDirect 格式的 PDF。

        返回: list[dict] — 每个条目包含:
          pdf_name, folder_exists, json_title, json_author, json_doi
        """
        return self._scan_filtered(
            lambda f: f.lower().endswith(".pdf")
                      and not is_doi_pdf(f)
                      and not is_sciencedirect(f)
        )

    def scan_sciencedirect(self) -> list:
        """
        仅扫描 ScienceDirect 格式 PDF（1-s2.0-xxx.pdf）。

        返回: 同 scan()
        """
        return self._scan_filtered(
            lambda f: is_sciencedirect(f)
        )

    def scan_all_pdfs(self) -> list:
        """
        扫描全部 PDF（包括已命名的 DOI 文件），仅返回文件名列表。
        """
        return sorted([
            f for f in os.listdir(self.summary_dir)
            if f.lower().endswith(".pdf")
        ])

    # ------------------------------------------------------------------
    #  内部
    # ------------------------------------------------------------------

    def _scan_filtered(self, filter_fn) -> list:
        entries = []
        for f in sorted(os.listdir(self.summary_dir)):
            if not filter_fn(f):
                continue

            stem = f[:-4]
            folder_path = os.path.join(self.summary_dir, stem)
            has_folder = os.path.isdir(folder_path)
            json_title, json_author, json_doi = "", "", ""

            if has_folder:
                json_title, json_author, json_doi = self._read_extraction(folder_path)

            entries.append({
                "pdf_name": f,
                "folder_exists": has_folder,
                "json_title": json_title,
                "json_author": json_author,
                "json_doi": json_doi,
            })

        return entries

    def _read_extraction(self, folder_path: str) -> tuple:
        """
        从文件夹的 derived/ 中读取 full_extraction JSON。
        返回 (title, author, doi)。
        """
        derived = os.path.join(folder_path, "derived")
        if not os.path.isdir(derived):
            return "", "", ""

        for jf in sorted(os.listdir(derived)):
            if "full_extraction" in jf and jf.endswith(".json"):
                try:
                    with open(os.path.join(derived, jf), "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                    meta = data.get("paper_metadata", {})
                    t = (meta.get("title", "") or "").strip()
                    a = (meta.get("first_author", "") or "").strip()
                    d = (meta.get("doi", "") or "").strip()

                    if t == "not reported":
                        t = ""
                    if a == "not reported":
                        a = ""
                    if d in ("not reported", "None", ""):
                        d = ""

                    return t, a, d
                except (json.JSONDecodeError, KeyError, OSError):
                    pass

        return "", "", ""