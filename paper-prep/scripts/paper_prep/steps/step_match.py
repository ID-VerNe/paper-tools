"""match 步骤:OCR 标题 → Crossref search_by_title → DOI → 双通道验证。"""

from __future__ import annotations

import json
import os
import sys

from paper_prep.config import VERIFY_TITLE_THRESHOLD
from paper_prep.state import PipelineContext
from paper_prep.title_extractor import extract_title_from_md
from paper_prep.verify import (
    crossref_canonical,
    make_canonical_sim_pair_decision,
    title_similarity,
)


def run_match(ctx: PipelineContext, llm_verifier=None) -> None:
    """对 ocr_output 每个 md 提取标题,查 Crossref,做双通道验证,写 doi_map.json。"""
    print("=== DOI 匹配阶段 ===")
    if not os.path.isdir(ctx.ocr_dir):
        print("错误: ocr_output 目录不存在，请先执行 OCR 阶段")
        sys.exit(1)

    from pdf_doi_toolkit.crossref import CrossRefClient
    client = CrossRefClient()

    entries = _collect_entries(ctx.ocr_dir)

    results = []
    for i, entry in enumerate(entries, 1):
        pdf_name = entry["pdf_name"]
        print(f"  [{i}/{len(entries)}] {pdf_name[:50]}...")
        if not entry["title"]:
            print("    → 无法提取标题")
            results.append({"pdf_name": pdf_name, "title": None, "doi": None,
                            "author": None, "year": None, "status": "no_title"})
            continue
        print(f"    title: {entry['title'][:70]}")
        cr = client.search_by_title(entry["title"])
        if not cr.get("doi"):
            print(f"    → {cr.get('note', 'not_found')}")
            results.append({"pdf_name": pdf_name, "title": entry["title"], "doi": None,
                            "author": None, "year": None, "status": "not_found"})
            continue

        doi = cr["doi"]
        print(f"    DOI: {doi}")
        # ── 校验 1:用 DOI 反查 CrossRef 权威标题,比对相似度 ──
        canon = crossref_canonical(client, doi)
        sim = title_similarity(entry["title"], canon.get("title") or "")
        canon_ok = canon.get("ok")

        # ── 校验 2:LLM 双通道语义判断(可选,--llm-verify 时启用)──
        llm_ok = None
        if llm_verifier is not None and canon_ok:
            llm_ok = llm_verifier.verify(entry["title"], canon.get("title") or "")
            label = {True: "YES", False: "NO", None: "ERR"}[llm_ok]
            print(f"    LLM 判断: {label}（同论文?）")

        status, verified, should_skip = make_canonical_sim_pair_decision(sim, llm_ok, canon_ok)

        if should_skip:
            print(f"    → 双通道一致否定（相似度 {sim:.2f}），疑似未在 Crossref 注册（博士论文/预印本/技术报告）")
            results.append({
                "pdf_name": pdf_name, "title": entry["title"], "doi": None,
                "author": None, "year": None, "status": "no_confidence",
                "nearest_doi": doi, "nearest_title": canon.get("title"),
                "title_sim": round(sim, 3),
            })
            continue

        print(f"    canonical: {str(canon.get('title'))[:70]} | 相似度 {sim:.2f} | {'✓' if verified else '⚠ 需人工'}")
        results.append({
            "pdf_name": pdf_name, "title": entry["title"], "doi": doi,
            "author": cr.get("author"), "year": cr.get("year"), "status": status,
            "verified": verified, "canonical_title": canon.get("title"),
            "title_sim": round(sim, 3), "llm_ok": llm_ok,
        })
        ctx.state["processed_count"] = ctx.state.get("processed_count", 0) + 1

    with open(ctx.map_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"  → 已保存: {ctx.map_path}")

    matched = [r for r in results if r["status"] == "matched"]
    not_found = [r for r in results if r["status"] == "not_found"]
    no_title = [r for r in results if r["status"] == "no_title"]
    no_conf = [r for r in results if r["status"] == "no_confidence"]
    print(f"  匹配: {len(matched)}, 未找到: {len(not_found)}, 无标题: {len(no_title)}, 疑似未注册: {len(no_conf)}")
    ctx.step_done("match")


def _collect_entries(ocr_dir: str) -> list[dict]:
    """遍历 ocr_output,为每个含 md 的文件夹收集 (folder, pdf_name, title, md_path)。"""
    entries = []
    for folder in sorted(os.listdir(ocr_dir)):
        folder_path = os.path.join(ocr_dir, folder)
        if not os.path.isdir(folder_path):
            continue
        md_files = [f for f in os.listdir(folder_path) if f.endswith(".md")]
        if not md_files:
            continue
        md_path = os.path.join(folder_path, md_files[0])
        title = extract_title_from_md(md_path)
        entries.append({"folder": folder, "pdf_name": folder + ".pdf",
                        "title": title, "md_path": md_path})
    return entries