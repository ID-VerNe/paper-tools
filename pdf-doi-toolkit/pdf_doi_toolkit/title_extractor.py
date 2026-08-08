"""
标题提取器 — 通过子进程隔离调用 DeepSeek-OCR 提取 PDF 标题。

DeepSeek-OCR 是独立的 uv 项目（仓库根目录下 DeepSeek-OCR/），
有自己的 venv、settings.json（付费 API 密钥）。因此这里用
subprocess 调用其 CLI，而不是直接 import。

用法:
    extractor = TitleExtractor()
    result = extractor.extract_title("path/to/paper.pdf")
    # → {"title": "论文标题", "note": "ok"}
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path

from .config import OCR_TIMEOUT, OCR_MIN_TITLE_LENGTH


# 出现在标题前、说明这是正文/版权信息而非标题的起始标记
_TITLE_FILTER_PREFIXES = (
    "abstract", "introduction", "received", "accepted", "published",
    "doi:", "doi ", "journal", "volume", "page", "copyright",
    "corresponding", "author", "keywords", "available",
)


class TitleExtractor:
    """
    通过 DeepSeek-OCR 提取 PDF 标题。

    参数:
        ocr_dir: DeepSeek-OCR 项目目录。None 则自动探测。
        timeout: 单篇 PDF OCR 超时（秒）。
    """

    def __init__(self, ocr_dir: str = None, timeout: int = OCR_TIMEOUT,
                 persistent_dir: str = None):
        self.ocr_dir = ocr_dir
        self.timeout = timeout
        self._ocr_persistent_dir = persistent_dir or tempfile.gettempdir()

    # ------------------------------------------------------------------
    #  主入口
    # ------------------------------------------------------------------

    def extract_title(self, pdf_path: str) -> dict:
        """
        提取 PDF 标题。

        返回:
            {"title": str|None, "note": str}
        """
        ocr_dir = self._locate_ocr_dir()
        if not ocr_dir:
            return {"title": None, "note": "ocr_dir_not_found"}

        name = Path(pdf_path).stem
        md_path = self._run_ocr(pdf_path, ocr_dir, name)
        if md_path is None:
            return {"title": None, "note": self._last_error}

        title = self._extract_title_from_md(self._read_md(md_path))
        if title:
            return {"title": title, "note": "ok"}
        return {"title": None, "note": "no_title_in_md"}

    # ------------------------------------------------------------------
    #  子进程调用 DeepSeek-OCR
    # ------------------------------------------------------------------

    def _run_ocr(self, pdf_path: str, ocr_dir: Path, name: str) -> Path | None:
        """
        调用 DeepSeek-OCR CLI，返回生成的 .md 文件路径。

        输出结构: {tmp_dir}/{name}/{name}.md
        """
        self._last_error = ""
        with tempfile.TemporaryDirectory(prefix="pdf_doi_ocr_") as tmp:
            cmd = ["uv", "run", "python", "ocr_cli.py", pdf_path, "--output", tmp]
            try:
                proc = subprocess.run(
                    cmd, cwd=str(ocr_dir), timeout=self.timeout,
                    capture_output=True, text=True,
                )
            except FileNotFoundError:
                self._last_error = "uv_not_found"
                return None
            except subprocess.TimeoutExpired:
                self._last_error = "ocr_timeout"
                return None

            if proc.returncode != 0:
                self._last_error = f"ocr_failed: {proc.stderr.strip()[:80]}"
                return None

            md_path = Path(tmp) / name / f"{name}.md"
            if not md_path.exists():
                self._last_error = "ocr_output_missing"
                return None

            # 在 TemporaryDirectory 销毁前读入内容，写入持久化位置
            content = self._read_md(md_path)
            if not content:
                self._last_error = "ocr_output_empty"
                return None

            persistent = Path(self._ocr_persistent_dir) / f"{name}.md"
            persistent.parent.mkdir(parents=True, exist_ok=True)
            persistent.write_text(content, encoding="utf-8")
            return persistent

    # ------------------------------------------------------------------
    #  从 Markdown 提取标题
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_title_from_md(md_text: str) -> str | None:
        """
        从 OCR 输出的 Markdown 中提取标题。

        策略:
          - 取前若干非空行
          - 过滤: markdown 标题标记、图片引用、含特征词的行
          - 第一行通过过滤的即为标题
        """
        if not md_text:
            return None

        for line in md_text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith(("#", ">", "```", "![")):
                continue
            # 过滤图片引用
            if re.match(r"^!\[.*\]\(.*\)$", line):
                continue
            if len(line) < OCR_MIN_TITLE_LENGTH:
                continue
            # 过滤以特征词起始的行（正文开头、版权信息等）
            lower = line.lower()
            if any(lower.startswith(p) for p in _TITLE_FILTER_PREFIXES):
                continue
            return line
        return None

    @staticmethod
    def _read_md(md_path: Path) -> str:
        try:
            return md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    # ------------------------------------------------------------------
    #  DeepSeek-OCR 目录定位
    # ------------------------------------------------------------------

    def _locate_ocr_dir(self) -> Path | None:
        """
        定位 DeepSeek-OCR 项目目录。

        优先级:
          1. 构造时传入的 ocr_dir
          2. 相对 pdf_doi_toolkit 包向上两级到仓库根下的 DeepSeek-OCR
          3. 当前工作目录下的 DeepSeek-OCR
        """
        if self.ocr_dir:
            candidate = Path(self.ocr_dir)
            if self._is_valid_ocr_dir(candidate):
                return candidate
            return None

        # 从包目录向上找仓库根: pdf_doi_toolkit/../..  → 仓库根
        here = Path(__file__).resolve()
        repo_candidate = here.parent.parent.parent / "DeepSeek-OCR"
        if self._is_valid_ocr_dir(repo_candidate):
            return repo_candidate

        cwd_candidate = Path.cwd() / "DeepSeek-OCR"
        if self._is_valid_ocr_dir(cwd_candidate):
            return cwd_candidate

        return None

    @staticmethod
    def _is_valid_ocr_dir(path: Path) -> bool:
        """判断目录是否为可用的 DeepSeek-OCR 项目（含 ocr_cli.py）。"""
        return (path / "ocr_cli.py").is_file()