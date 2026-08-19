"""OCR 步骤:对 PDF 执行 OCR(或跳过已存在的 md)。"""

from __future__ import annotations

import os
from pathlib import Path

from paper_prep.state import PipelineContext


def run_ocr(ctx: PipelineContext) -> None:
    """对 article_dir 下所有 PDF 执行 OCR,输出到 ocr_output/{pdf_stem}/{pdf_stem}.md。"""
    print("=== OCR 阶段 ===")
    from pdf_doi_toolkit.title_extractor import TitleExtractor
    ex = TitleExtractor()
    pdfs = [f for f in os.listdir(ctx.article_dir) if f.lower().endswith(".pdf")]
    for i, pdf in enumerate(pdfs, 1):
        stem = Path(pdf).stem
        md_path = os.path.join(ctx.ocr_dir, stem, f"{stem}.md")
        if os.path.exists(md_path):
            print(f"  [{i}/{len(pdfs)}] {pdf[:50]} → 已存在，跳过")
            continue
        print(f"  [{i}/{len(pdfs)}] {pdf[:50]}...", end=" ")
        result = ex.extract_title(os.path.join(ctx.article_dir, pdf))
        if result["title"]:
            print("OK")
        else:
            print(f"失败: {result.get('note', '?')}")
            ctx.record_failure({"pdf": pdf, "step": "ocr", "note": result.get("note")})
    ctx.step_done("ocr")