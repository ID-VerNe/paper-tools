"""rename 步骤:把 PDF/OCR 目录/md 重命名为 {doi_safe}。

带验证门禁:verified=False 的条目默认跳过,需 --force 放行。
no_confidence 条目天然不进(不在 matched 列表)。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from paper_prep.state import PipelineContext
from paper_prep.verify import doi_safe


def run_rename(ctx: PipelineContext, dry_run: bool = False, force: bool = False) -> None:
    print("=== 重命名阶段 ===")
    if not os.path.exists(ctx.map_path):
        print("错误: doi_map.json 不存在")
        __import__("sys").exit(1)
    with open(ctx.map_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    matched = [r for r in results if r["status"] == "matched"]

    renamed = skipped = 0
    for r in matched:
        # ── 验证门禁:unverified 条目不进 rename,需人工用 --force 放行 ──
        if not force and r.get("verified") is False:
            print(f"  [BLOCK] {r['pdf_name'][:45]} → 标题/DOI 未通过验证，跳过（需人工处理或用 --force 放行）")
            skipped += 1
            continue

        doi = r["doi"]
        safe = doi_safe(doi)
        pdf_name = r["pdf_name"]
        stem = Path(pdf_name).stem

        src_pdf = os.path.join(ctx.article_dir, pdf_name)
        dst_pdf = os.path.join(ctx.article_dir, f"{safe}.pdf")
        src_ocr = os.path.join(ctx.ocr_dir, stem)
        dst_ocr = os.path.join(ctx.ocr_dir, safe)
        src_md = os.path.join(ctx.ocr_dir, stem, f"{stem}.md")
        dst_md = os.path.join(ctx.ocr_dir, safe, f"{safe}.md")

        if dry_run:
            if os.path.exists(src_pdf):
                print(f"  [DRY-RUN] {pdf_name[:45]} → {safe}.pdf")
            continue

        if os.path.exists(dst_pdf):
            print(f"  [SKIP] {pdf_name[:45]} → 目标 {safe}.pdf 已存在")
            skipped += 1
            continue

        try:
            os.rename(src_pdf, dst_pdf)
            print(f"  [RENAME] {pdf_name[:45]} → {safe}.pdf")
            if os.path.isdir(src_ocr):
                os.rename(src_ocr, dst_ocr)
            if os.path.exists(src_md):
                os.makedirs(os.path.dirname(dst_md), exist_ok=True)
                os.rename(src_md, dst_md)
            renamed += 1
        except OSError as e:
            print(f"  [FAIL] {pdf_name[:45]} → {e}")
            ctx.record_failure({"pdf": pdf_name, "step": "rename", "error": str(e)})

    print(f"  重命名: {renamed} 篇, 跳过: {skipped} 篇")
    ctx.step_done("rename")