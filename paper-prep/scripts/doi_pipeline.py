#!/usr/bin/env python3
"""
paper-prep 流水线入口 — 薄编排层，串起各子步骤。

用法:
    python doi_pipeline.py --article-dir <dir> --step all
    python doi_pipeline.py --article-dir <dir> --step match
    python doi_pipeline.py --article-dir <dir> --step match --llm-verify
    python doi_pipeline.py --article-dir <dir> --step bibtex
    python doi_pipeline.py --article-dir <dir> --step rename --dry-run
"""

from __future__ import annotations

import argparse
import sys

# 确保本包所在路径可导入
sys.path.insert(0, __import__("os").path.dirname(__file__))

from paper_prep import _resolve_toolkit_path  # noqa 触发了环境就绪
from paper_prep.config import VERIFY_TITLE_THRESHOLD  # noqa 确保 Windows SSL 兼容已执行
from paper_prep.llm_verifier import LLMVerifier
from paper_prep.state import PipelineContext
from paper_prep.steps.step_ocr import run_ocr
from paper_prep.steps.step_match import run_match
from paper_prep.steps.step_report import run_report
from paper_prep.steps.step_verify import run_verify
from paper_prep.steps.step_bibtex import run_bibtex
from paper_prep.steps.step_rename import run_rename
from paper_prep.verify import load_bib_manifest
from pdf_doi_toolkit.config import DEFAULT_USER_AGENT


def run_pipeline(article_dir: str, steps: list[str], dry_run: bool = False,
                 args_force: bool = False,
                 bib_manifest: dict[str, dict] | None = None,
                 verify_litrev_dir: str | None = None,
                 llm_verifier: LLMVerifier | None = None):
    """编排所有 step,按顺序执行请求的步骤。"""
    ctx = PipelineContext.create(article_dir)

    # ---- Step: ocr ----
    if "ocr" in steps and ctx.step_pending("ocr"):
        run_ocr(ctx)

    # ---- Step: match ----
    if "match" in steps and ctx.step_pending("match"):
        run_match(ctx, llm_verifier=llm_verifier)

    # ---- Step: verify ----
    if "verify" in steps and ctx.step_pending("verify"):
        run_verify(ctx, bib_manifest=bib_manifest, litrev_dir=verify_litrev_dir)

    # ---- Step: report ----
    if "report" in steps and ctx.step_pending("report"):
        run_report(ctx)

    # ---- Step: bibtex ----
    if "bibtex" in steps and ctx.step_pending("bibtex"):
        run_bibtex(ctx, user_agent=DEFAULT_USER_AGENT)

    # ---- Step: rename ----
    if "rename" in steps and ctx.step_pending("rename"):
        run_rename(ctx, dry_run=dry_run, force=args_force)


def main():
    parser = argparse.ArgumentParser(
        description="paper-prep 流水线 — OCR→DOI→BibTeX→Rename（含内容校验+双通道验证）"
    )
    parser.add_argument("--article-dir", required=True, help="PDF 所在目录")
    parser.add_argument("--step", default="all",
                        choices=["ocr", "match", "verify", "report", "bibtex", "rename", "all"],
                        help="执行步骤（默认 all）")
    parser.add_argument("--dry-run", action="store_true", help="rename 阶段仅预览，不执行")
    parser.add_argument("--force", dest="args_force", action="store_true",
                        help="rename 阶段放行未通过验证的条目（人工确认后使用）")
    parser.add_argument("--bib", dest="bib_path", default=None,
                        help="references.bib 路径，作为第三方期望真值参与 verify 三方比对")
    parser.add_argument("--verify-with-litrev", dest="litrev_derived", default=None,
                        help="litrev-extract 的 derived 目录路径，复用已有 metadata JSON 做深度内容校验")
    parser.add_argument("--llm-verify", dest="llm_verify", action="store_true",
                        help="启用 LLM 双通道验证（环境变量: OPENAI_API_KEY/OPENAI_BASE_URL/LLM_MODEL_NAME）")
    parser.add_argument("--llm-model", dest="llm_model", default=None,
                        help="覆盖 LLM_MODEL_NAME 环境变量指定的模型")
    args = parser.parse_args()

    if not __import__("os").path.isdir(args.article_dir):
        print(f"错误: 目录不存在 — {args.article_dir}", file=sys.stderr)
        sys.exit(1)

    bib_manifest = None
    if args.bib_path:
        bib_manifest = load_bib_manifest(args.bib_path)

    llm_verifier = None
    if args.llm_verify:
        try:
            llm_verifier = LLMVerifier(model_name=args.llm_model)
            print(f"LLM 双通道验证已启用（模型: {llm_verifier.model_name}）")
        except ValueError as e:
            print(f"[WARN] {e} — 降级为仅余弦相似度验证")

    steps = ["ocr", "match", "verify", "report", "bibtex", "rename"] if args.step == "all" else [args.step]
    run_pipeline(args.article_dir, steps, dry_run=args.dry_run,
                 args_force=args.args_force,
                 bib_manifest=bib_manifest,
                 verify_litrev_dir=args.litrev_derived,
                 llm_verifier=llm_verifier)
    if llm_verifier is not None:
        llm_verifier.close()


if __name__ == "__main__":
    main()