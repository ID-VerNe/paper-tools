"""report 步骤:读 doi_map.json 生成可读的 doi_match_report.md。"""

from __future__ import annotations

import json
import time

from paper_prep.state import PipelineContext


def run_report(ctx: PipelineContext) -> None:
    """读 doi_map.json,写 doi_match_report.md。"""
    print("=== 报告生成阶段 ===")
    if not __import__("os").path.exists(ctx.map_path):
        print("错误: doi_map.json 不存在，请先执行 match 阶段")
        __import__("sys").exit(1)

    with open(ctx.map_path, "r", encoding="utf-8") as f:
        results = json.load(f)

    matched = [r for r in results if r["status"] == "matched"]
    not_found = [r for r in results if r["status"] == "not_found"]
    no_title = [r for r in results if r["status"] == "no_title"]
    no_conf = [r for r in results if r["status"] == "no_confidence"]

    lines = [
        "# DOI 匹配报告",
        f"扫描目录: `{ctx.article_dir}`",
        f"生成时间: {time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "## 总览",
        "| 分类 | 数量 |",
        "|------|:----:|",
        f"| 总 PDF | {len(results)} |",
        f"| 已匹配 | {len(matched)} |",
        f"| 未找到 | {len(not_found)} |",
        f"| 无标题 | {len(no_title)} |",
        f"| 疑似未注册论文 | {len(no_conf)} |",
        "",
    ]
    if matched:
        lines.append("## ✅ 已匹配\n")
        lines.append("| # | 文件名 | 标题 | DOI | 作者 | 年份 | 验证 |")
        lines.append("|---|--------|------|:---:|:----:|:---:|:----:|")
        for i, r in enumerate(matched, 1):
            v = "✓" if r.get("verified") else "⚠ 需人工"
            lines.append(f"| {i} | `{r['pdf_name'][:45]}` | {r['title'][:40] if r['title'] else '?'} | `{r['doi']}` | {r['author'][:20] if r['author'] else '?'} | {r['year'] or '?'} | {v} |")
    if not_found:
        lines.append("## ❌ 未匹配\n")
        for r in not_found:
            lines.append(f"- `{r['pdf_name'][:50]}` — 标题: {r['title'][:50] if r['title'] else '?'}")
    if no_title:
        lines.append("## ⚠️ 无法提取标题\n")
        for r in no_title:
            lines.append(f"- `{r['pdf_name'][:50]}`")
    if no_conf:
        lines.append("## ❓ 疑似未注册论文（在 Crossref 上无独立 DOI）\n")
        lines.append("| # | 文件名 | OCR 标题 | 最接近候选 | 相似度 | 处置建议 |")
        lines.append("|---|--------|----------|:----------:|:-----:|---------|")
        for i, r in enumerate(no_conf, 1):
            lines.append(f"| {i} | `{r['pdf_name'][:45]}` | {str(r['title'])[:45] if r['title'] else '?'} | `{r.get('nearest_doi','?')}` | {r.get('title_sim', '?')} | 博士论文/预印本? 建议归档到独立目录或重下正确论文 |")

    with open(ctx.report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"  → 已保存: {ctx.report_path}")
    ctx.step_done("report")