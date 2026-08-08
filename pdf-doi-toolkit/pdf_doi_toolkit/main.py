"""
pdf-doi-toolkit CLI 入口。

用法:
    pdf-doi --dir ./pdfs --fuzzy
    pdf-doi --dir ./pdfs --ocr --ocr-dir ../DeepSeek-OCR
    pdf-doi --dir ./pdfs --fuzzy --checklist
    pdf-doi --dir ./pdfs --apply-manual manual_dois.json
"""

import argparse
import json
import os
import sys

from .matcher import DOIMatcher
from .config import CACHE_DEFAULT_FILENAME


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-doi",
        description="PDF DOI 重命名工具 — 将散乱命名的学术 PDF 重命名为 DOI.pdf 格式",
    )

    parser.add_argument("--dir", required=True,
                        help="PDF 所在目录（必需）")
    parser.add_argument("--fuzzy", action="store_true",
                        help="启用模糊文件名匹配兜底")
    parser.add_argument("--ocr", action="store_true",
                        help="启用 DeepSeek-OCR 文本提取（付费、慢）")
    parser.add_argument("--ocr-dir",
                        help="DeepSeek-OCR 目录位置（默认自动探测）")
    parser.add_argument("--no-cache", action="store_true",
                        help="禁用本地缓存")
    parser.add_argument("--cache-file",
                        help="缓存文件路径（默认: {dir}/pdf_doi_cache.json）")
    parser.add_argument("--checklist", action="store_true",
                        help="生成 x-mol 人工确认清单")
    parser.add_argument("--apply-manual",
                        help="应用人工确认 JSON 文件")
    parser.add_argument("--no-rename", action="store_true",
                        help="仅生成报告，不重命名")
    parser.add_argument("--report",
                        help="报告输出路径（默认: {dir}/PDF_DOI_匹配报告.md）")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # 检查目录存在
    pdf_dir = os.path.abspath(args.dir)
    if not os.path.isdir(pdf_dir):
        print(f"错误: 目录不存在 — {pdf_dir}", file=sys.stderr)
        sys.exit(1)

    # 缓存文件路径
    cache_path = None
    if not args.no_cache:
        cache_path = args.cache_file or os.path.join(pdf_dir, CACHE_DEFAULT_FILENAME)

    # 创建 Matcher
    matcher = DOIMatcher(
        summary_dir=pdf_dir,
        cache_path=cache_path,
        no_cache=args.no_cache,
        ocr_dir=args.ocr_dir,
    )

    # 1. 扫描
    print(f"扫描目录: {pdf_dir}")
    matcher.scan_pdfs()
    print(f"找到 {len(matcher.entries)} 篇 PDF")

    # 2. 人工确认模式（跳过网络查询）
    if args.apply_manual:
        manual_path = os.path.abspath(args.apply_manual)
        if not os.path.exists(manual_path):
            print(f"错误: 人工确认文件不存在 — {manual_path}", file=sys.stderr)
            sys.exit(1)
        with open(manual_path, "r", encoding="utf-8") as f:
            manual_map = json.load(f)
        print(f"应用人工确认: {len(manual_map)} 篇...")
        matcher.verify_manual_dois(manual_map)
        matcher.save_report(args.report)
        return

    # 3. CrossRef 查询
    matcher.run_crossref_check()

    # 4. 模糊文件名匹配
    if args.fuzzy:
        matcher.run_fuzzy_fallback()

    # 5. OCR 文本提取
    if args.ocr:
        matcher.run_ocr_fallback()

    # 6. 生成 x-mol 确认清单
    if args.checklist:
        fuzzy_results = [r for r in matcher.results if r.get("fuzzy_score")]
        review_items = [
            r for r in fuzzy_results
            if r.get("cr_doi")
        ]
        if review_items:
            from .xmol import XMolFallback
            checklist_path = os.path.join(
                args.report and os.path.dirname(args.report) or pdf_dir,
                "xmol_待确认_清单.md",
            )
            XMolFallback.generate_checklist(review_items, output_path=checklist_path)

    # 7. 重命名
    if not args.no_rename:
        rename_result = matcher.rename_matched()
        if rename_result["conflicts"]:
            print(f"DOI 冲突: {len(rename_result['conflicts'])} 组，跳过")
        print(f"重命名: {rename_result['renamed']} 篇, "
              f"去重: {rename_result['deduped']} 篇, "
              f"跳过: {rename_result['skipped']} 篇")

    # 8. 报告
    matcher.save_report(args.report)

    # 9. 摘要
    s = matcher.summary()
    print()
    print("=" * 50)
    print("摘要")
    print("=" * 50)
    print(f"  扫描总数:     {s['total_pdfs_scanned']}")
    print(f"  CrossRef 匹配: {s['crossref_matched']}")
    if s['fuzzy_attempted']:
        print(f"  模糊匹配:     {s['fuzzy_matched']} / {s['fuzzy_attempted']}")
    if s['ocr_attempted']:
        print(f"  OCR 匹配:     {s['ocr_matched']} / {s['ocr_attempted']}")
    if s['cache_hits']:
        print(f"  缓存命中:     {s['cache_hits']} 条")


if __name__ == "__main__":
    main()