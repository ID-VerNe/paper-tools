"""verify 步骤:对已命名的 ocr_output 文件夹做内容校验(三向比对)。

独立于 match,专门解决"内容 vs 期望身份"——按文件夹名推断 DOI,
反查 Crossref 期望标题与 OCR 标题比对,可选叠加 bib 与 litrev metadata。
"""

from __future__ import annotations

import glob
import json
import os
import sys

from paper_prep.config import VERIFY_TITLE_THRESHOLD
from paper_prep.state import PipelineContext
from paper_prep.title_extractor import extract_title_from_md
from paper_prep.verify import crossref_canonical, doi_safe, title_similarity


def run_verify(ctx: PipelineContext, bib_manifest=None, litrev_dir=None) -> None:
    print("=== 内容验证阶段 ===")
    if not os.path.isdir(ctx.ocr_dir):
        print("错误: ocr_output 目录不存在")
        sys.exit(1)

    from pdf_doi_toolkit.crossref import CrossRefClient
    client = CrossRefClient()
    verify_results = []
    folders = sorted(os.listdir(ctx.ocr_dir))

    for i, folder in enumerate(folders, 1):
        folder_path = os.path.join(ctx.ocr_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        # 文件夹名是历史 match 产物,不代表论文真实身份;下划线→斜杠只转第一个
        expected_doi = folder.replace("_", "/", 1) if "_" in folder else folder

        md_files = [f for f in os.listdir(folder_path) if f.endswith(".md")]
        if not md_files:
            print(f"  [{i}/{len(folders)}] {folder[:50]} → 无 md 文件，跳过")
            continue
        md_path = os.path.join(folder_path, md_files[0])
        ocr_title = extract_title_from_md(md_path)
        print(f"  [{i}/{len(folders)}] {folder[:50]}...")

        canon = crossref_canonical(client, expected_doi)
        if not canon.get("ok"):
            print("    → 无法从 Crossref 获取 DOI 信息")
            verify_results.append({
                "folder": folder, "expected_doi": expected_doi,
                "ocr_title": ocr_title, "canonical_title": None,
                "first_author": None, "title_sim": 0.0, "status": "cannot_verify",
            })
            continue

        sim = title_similarity(ocr_title, canon.get("title") or "")
        verified = sim >= VERIFY_TITLE_THRESHOLD

        # bib 三方比对
        bib_sim = None
        if bib_manifest:
            bib_entry = bib_manifest.get(expected_doi.lower())
            if bib_entry and bib_entry.get("title"):
                bib_sim = title_similarity(ocr_title, bib_entry["title"])
                if verified and bib_sim < VERIFY_TITLE_THRESHOLD:
                    verified = False

        # litrev metadata 三方比对(多模型取最高相似度)
        litrev_sim = None
        if litrev_dir:
            litrev_sim = _best_litrev_sim(litrev_dir, expected_doi, ocr_title)
            if litrev_sim is not None and litrev_sim < VERIFY_TITLE_THRESHOLD:
                verified = False

        status = "verified" if verified else "mismatch"
        print(f"    expected DOI: {expected_doi}")
        print(f"    expected title: {str(canon.get('title'))[:70]}")
        print(f"    OCR title: {str(ocr_title)[:70] if ocr_title else 'None'}")
        print(f"    相似度: {sim:.2f} | {'✓' if verified else '⚠ 需人工'}")
        if bib_sim is not None:
            print(f"    bib 比对: {bib_sim:.2f}")
        if litrev_sim is not None:
            print(f"    litrev 比对: {litrev_sim:.2f}")

        verify_results.append({
            "folder": folder, "expected_doi": expected_doi,
            "ocr_title": ocr_title, "canonical_title": canon.get("title"),
            "first_author": canon.get("first_author"),
            "title_sim": round(sim, 3),
            "bib_sim": round(bib_sim, 3) if bib_sim is not None else None,
            "litrev_sim": round(litrev_sim, 3) if litrev_sim is not None else None,
            "status": status,
        })

    _write_verify_report(ctx, verify_results)
    ctx.step_done("verify")


def _best_litrev_sim(litrev_dir: str, doi: str, ocr_title: str):
    """扫描所有模型的 metadata JSON,返回与 OCR 标题最高的相似度。"""
    safe = doi_safe(doi)
    meta_files = sorted(glob.glob(os.path.join(litrev_dir, f"{safe}_metadata_*.json")))
    best = None
    for meta_file in meta_files:
        try:
            with open(meta_file, "r", encoding="utf-8") as f:
                meta = json.load(f)
            meta_title = meta.get("citation", {}).get("title")
            if not meta_title:
                continue
            sim_m = title_similarity(ocr_title, meta_title)
            if best is None or sim_m > best:
                best = sim_m
        except Exception:
            continue
    return best


def _write_verify_report(ctx: PipelineContext, verify_results: list[dict]) -> None:
    import time
    verified_count = len([r for r in verify_results if r["status"] == "verified"])
    mismatch_count = len([r for r in verify_results if r["status"] == "mismatch"])
    cannot_count = len([r for r in verify_results if r["status"] == "cannot_verify"])

    lines = [
        "# 内容验证报告",
        f"扫描目录: `{ctx.article_dir}`",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "> 注:文件夹名来自历史 match 产物,不代表论文真实身份。此报告比对「OCR 内容」与「该 DOI 在 Crossref 的权威标题」。",
        "",
        "## 总览",
        "| 分类 | 数量 |",
        "|------|:----:|",
        f"| 总文件夹 | {len(verify_results)} |",
        f"| 已验证一致 | {verified_count} |",
        f"| 内容不符 | {mismatch_count} |",
        f"| 无法验证 | {cannot_count} |",
        "",
    ]
    if verified_count:
        lines.append("## ✅ 已验证一致\n")
        for r in verify_results:
            if r["status"] == "verified":
                lines.append(f"- `{r['folder'][:45]}` — 相似度 {r['title_sim']:.2f}")
    if mismatch_count:
        lines.append("## ⚠️ 内容不符（需人工）\n")
        lines.append("| # | 文件夹 | 期望 DOI | OCR 标题 | Crossref 标题 | 相似度 |")
        lines.append("|---|--------|:--------:|----------|:-------------:|:-----:|")
        for i, r in enumerate([v for v in verify_results if v["status"] == "mismatch"], 1):
            lines.append(f"| {i} | `{r['folder'][:35]}` | `{r['expected_doi']}` | {str(r['ocr_title'])[:40] if r['ocr_title'] else '?'} | {str(r['canonical_title'])[:40] if r['canonical_title'] else '?'} | {r['title_sim']:.2f} |")
    if cannot_count:
        lines.append("## ❓ 无法验证\n")
        for r in verify_results:
            if r["status"] == "cannot_verify":
                lines.append(f"- `{r['folder'][:45]}` — 无法从 Crossref 获取信息")

    with open(ctx.verify_report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  → 已保存: {ctx.verify_report_path}")
    print(f"  一致: {verified_count}, 不符: {mismatch_count}, 无法验证: {cannot_count}")