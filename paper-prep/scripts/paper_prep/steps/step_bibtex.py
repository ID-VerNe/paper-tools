"""bibtex 步骤:按 doi_map.json 中的 matched 条目拉取 BibTeX。"""

from __future__ import annotations

import json
import os

from paper_prep.bibtex_fetcher import fetch_bibtex
from paper_prep.state import PipelineContext
from paper_prep.verify import doi_safe


def run_bibtex(ctx: PipelineContext, user_agent: str) -> None:
    print("=== BibTeX 拉取阶段 ===")
    if not os.path.exists(ctx.map_path):
        print("错误: doi_map.json 不存在，请先执行 match 阶段")
        __import__("sys").exit(1)
    with open(ctx.map_path, "r", encoding="utf-8") as f:
        results = json.load(f)
    matched = [r for r in results if r["status"] == "matched"]
    os.makedirs(ctx.bib_dir, exist_ok=True)

    ok = skip = fail = 0
    for r in matched:
        doi = r["doi"]
        safe = doi_safe(doi)
        bib_path = os.path.join(ctx.bib_dir, f"{safe}.bib")
        if os.path.exists(bib_path):
            skip += 1
            continue
        print(f"  [FETCH] {doi}...", end=" ")
        bib = fetch_bibtex(doi, user_agent)
        if bib:
            with open(bib_path, "w", encoding="utf-8") as f:
                f.write(bib)
            print("OK")
            ok += 1
        else:
            print("失败")
            fail += 1
            ctx.record_failure({"doi": doi, "step": "bibtex"})
    print(f"  BibTeX: {ok} 成功, {skip} 跳过, {fail} 失败")
    ctx.step_done("bibtex")