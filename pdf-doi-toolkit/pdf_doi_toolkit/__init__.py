"""
pdf-doi-toolkit — PDF DOI 重命名工具包

将散乱命名的学术 PDF（数字编号、hash 名、标题名、ScienceDirect 格式）
统一重命名为 DOI.pdf 格式。

核心流程:
  DOIMatcher.scan_pdfs()           → 扫描目录，提取元数据
  DOIMatcher.run_crossref_check()  → CrossRef 查询 + author 验证
  DOIMatcher.run_fuzzy_fallback()  → 模糊文件名匹配兜底（可选）
  DOIMatcher.run_ocr_fallback()    → DeepSeek-OCR 文本提取（可选）
  DOIMatcher.rename_matched()      → 重命名匹配的 PDF，含冲突检测
  DOIMatcher.save_report()         → 生成 Markdown 报告

兜底策略:
  XMolFallback.generate_checklist() → 生成人工确认清单（打开 x-mol 链接验证）
  DOIMatcher.verify_manual_dois()   → 用户确认后执行重命名

使用:
  from pdf_doi_toolkit import DOIMatcher, CrossRefClient, simple_rename_pipeline
"""

__version__ = "1.1.0"

from .config import (
    DEFAULT_USER_AGENT,
    CROSSREF_MAX_RETRIES,
    CROSSREF_CONCURRENCY,
    CROSSREF_REQUEST_DELAY,
)

from .utils import (
    normalize_author,
    authors_match,
    doi_safe,
    strip_supplement,
    is_doi_pdf,
    is_sciencedirect,
)

from .cache import DOICache
from .crossref import CrossRefClient
from .fuzzy import FilenameParser, FilenameParseResult
from .scanner import PDFScanner
from .sciencedirect import ScienceDirectHandler
from .title_extractor import TitleExtractor
from .xmol import XMolFallback
from .matcher import DOIMatcher


def simple_rename_pipeline(summary_dir: str, output_dir: str = None,
                           fuzzy_fallback: bool = False,
                           ocr_fallback: bool = False,
                           no_cache: bool = False,
                           ocr_dir: str = None):
    """
    一键执行：扫描 → CrossRef 查询 → 重命名匹配的 → 保存报告。

    参数:
        summary_dir: PDF 所在目录
        output_dir: 报告输出目录（默认同 summary_dir）
        fuzzy_fallback: 是否启用模糊文件名匹配兜底
        ocr_fallback: 是否启用 DeepSeek-OCR 文本提取兜底（付费、慢）
        no_cache: 是否禁用本地缓存
        ocr_dir: DeepSeek-OCR 目录位置（默认自动探测）

    返回:
        DOIMatcher 实例（含匹配结果）
    """
    matcher = DOIMatcher(summary_dir, output_dir,
                         no_cache=no_cache, ocr_dir=ocr_dir)
    matcher.scan_pdfs()
    print(f"扫描完成: {len(matcher.entries)} 篇待处理")
    matcher.run_crossref_check()
    if fuzzy_fallback:
        matcher.run_fuzzy_fallback()
    if ocr_fallback:
        matcher.run_ocr_fallback()
    renamed = matcher.rename_matched()
    print(f"重命名: {renamed['renamed']} 篇, 去重: {renamed['deduped']} 篇, 跳过: {renamed['skipped']} 篇")
    matcher.save_report()
    return matcher