"""
DOIMatcher — PDF DOI 匹配主引擎。

编排流程:
  1. scan_pdfs() / scan_sciencedirect()  → 扫描 PDF 并提取元数据
  2. run_crossref_check()                  → CrossRef 查询 + author 验证
  3. resolve_supplement_dois()             → 处理 .s001 后缀问题
  4. rename_matched()                      → 重命名匹配的 PDF
  5. verify_manual_dois()                  → 用户从 x-mol 确认后执行
  6. report() / save_report()              → 生成报告

典型用法:
    matcher = DOIMatcher("path/to/pdfs")
    matcher.scan_pdfs()
    matcher.run_crossref_check()
    matcher.rename_matched()
    print(matcher.summary())
    matcher.save_report()
"""

import os

from concurrent.futures import ThreadPoolExecutor, as_completed

from .config import CROSSREF_CONCURRENCY
from .crossref import CrossRefClient
from .scanner import PDFScanner
from .utils import (
    authors_match,
    doi_safe,
    strip_supplement,
)


class DOIMatcher:
    """
    PDF DOI 匹配主引擎。

    参数:
        summary_dir: PDF 所在目录
        output_dir: 报告输出目录（默认同 summary_dir）
        crossref_client: 可选的 CrossRefClient 实例
    """

    def __init__(self, summary_dir: str, output_dir: str = None,
                 crossref_client: CrossRefClient = None):
        self.summary_dir = summary_dir
        self.output_dir = output_dir or summary_dir
        self.cr = crossref_client or CrossRefClient()
        self.scanner = PDFScanner(summary_dir)

        self.entries = []    # 所有扫描到的条目
        self.results = []    # CrossRef 查询结果

        # 分类缓存
        self._cat_a = []
        self._cat_b = []
        self._cat_c = []

    # ------------------------------------------------------------------
    #  扫描
    # ------------------------------------------------------------------

    def scan_pdfs(self):
        """扫描非 DOI、非 ScienceDirect 格式的 PDF"""
        self.entries = self.scanner.scan()
        self._classify()
        return self

    def scan_sciencedirect(self):
        """仅扫描 ScienceDirect 格式 PDF"""
        self.entries = self.scanner.scan_sciencedirect()
        self._classify()
        return self

    def _classify(self):
        """按 title/author 有无分类"""
        self._cat_a = [e for e in self.entries if e["json_title"] and e["json_author"]]
        self._cat_b = [e for e in self.entries if e["json_title"] and not e["json_author"]]
        self._cat_c = [e for e in self.entries if not e["json_title"]]

    # ------------------------------------------------------------------
    #  CrossRef 全量查询
    # ------------------------------------------------------------------

    def run_crossref_check(self, concurrency: int = None) -> list:
        """
        对 A 组（有 title+author）和 B 组（有 title 无 author）运行 CrossRef 查询。

        参数:
            concurrency: 并发数（默认 6）

        返回: list[dict] — 每条含:
          pdf_name, final_title, final_author,
          cr_doi, cr_author, match (bool), note
        """
        concurrency = concurrency or CROSSREF_CONCURRENCY
        self.results = []

        # A 组: 有 title+author → 精确验证
        if self._cat_a:
            print(f"CrossRef A 组（有 title+author）: {len(self._cat_a)} 篇...")
            with ThreadPoolExecutor(max_workers=concurrency) as ex:
                futures = {ex.submit(self._check_one, e): e for e in self._cat_a}
                for i, f in enumerate(as_completed(futures), 1):
                    self.results.append(f.result())
                    if i % 10 == 0 or i == len(self._cat_a):
                        print(f"  进度: {i}/{len(self._cat_a)}")

        # B 组: 有 title 无 author → 查到但标记不可验证
        if self._cat_b:
            print(f"CrossRef B 组（有 title 无 author）: {len(self._cat_b)} 篇...")
            for e in self._cat_b:
                res = self.cr.search_by_title(e["json_title"])
                self.results.append({
                    "pdf_name": e["pdf_name"],
                    "final_title": e["json_title"],
                    "final_author": e["json_author"],
                    "cr_doi": res["doi"],
                    "cr_author": res["author"],
                    "match": bool(res["doi"]),
                    "note": f"found: {res['doi']} (no author verify)" if res["doi"]
                            else (res.get("note") or "not_found"),
                })

        return self.results

    def _check_one(self, entry: dict) -> dict:
        """
        单篇查询 + author 验证 + .s001 自动兜底。

        流程:
          1. 按标题查 CrossRef
          2. author 匹配 → ok
          3. author 不匹配且 DOI 带 .s001 → 去掉后缀再查
        """
        title = entry["json_title"]
        author = entry["json_author"]

        # 第 1 步：正常查询
        res = self.cr.search_by_title(title, author)

        result = dict(entry)
        result["cr_doi"] = res["doi"]
        result["cr_author"] = res["author"]
        result["match"] = False

        if res["doi"] and author:
            if authors_match(author, res.get("author", "")):
                result["match"] = True
                result["note"] = "ok"
            else:
                # 第 2 步：尝试去掉 .s001/.s002 后缀
                stripped = strip_supplement(res["doi"])
                if stripped != res["doi"]:
                    res2 = self.cr.search_by_doi(stripped)
                    if res2.get("author") and authors_match(author, res2["author"]):
                        result["cr_doi"] = stripped
                        result["cr_author"] = res2["author"]
                        result["match"] = True
                        result["note"] = f"ok (stripped suffix)"
                    else:
                        result["note"] = (
                            f"author_mismatch: {author} vs {res.get('author', '?')}"
                        )
                else:
                    result["note"] = (
                        f"author_mismatch: {author} vs {res.get('author', '?')}"
                    )
        elif res["doi"]:
            result["note"] = "no_author_to_verify"
        else:
            result["note"] = res.get("note", "not_found")

        return result

    # ------------------------------------------------------------------
    #  .s001 批量处理
    # ------------------------------------------------------------------

    def resolve_supplement_dois(self, pdf_doi_map: dict = None) -> list:
        """
        对一批 PDF 执行 .s001 后缀剥离 + 重新验证。

        参数:
            pdf_doi_map: {pdf_name: real_doi} — 直接使用已知的 DOI
                         None — 自动从已有结果中提取 DOI 并剥离后缀

        返回: list[dict] — 每条含 pdf_name, cr_doi, cr_author, match
        """
        results = []
        for entry in self.entries:
            pdf_name = entry["pdf_name"]

            if pdf_doi_map and pdf_name in pdf_doi_map:
                doi = pdf_doi_map[pdf_name]
            else:
                r = self._find_result(pdf_name)
                if not r or not r.get("cr_doi"):
                    continue
                doi = strip_supplement(r["cr_doi"])
                if doi == r["cr_doi"]:
                    continue

            res = self.cr.search_by_doi(doi)
            author = entry.get("json_author", "")
            match = bool(author) and authors_match(author, res.get("author", ""))

            results.append({
                "pdf_name": pdf_name,
                "cr_doi": doi,
                "cr_author": res.get("author", "?"),
                "json_author": author,
                "match": match,
                "cr_title": res.get("title", "")[:60],
            })

            status = "✅" if match else "❌"
            print(f"  [{status}] {pdf_name[:45]} → {doi}  "
                  f"author={res.get('author', '?')}")

        return results

    # ------------------------------------------------------------------
    #  用户确认后执行
    # ------------------------------------------------------------------

    def verify_manual_dois(self, manual_map: dict) -> list:
        """
        用用户从 x-mol 确认的 (pdf_name → doi) 映射执行重命名。

        参数:
            manual_map = {"old_name.pdf": "10.xxxx/xxxxx", ...}
        """
        results = []
        for pdf_name, doi in manual_map.items():
            src = os.path.join(self.summary_dir, pdf_name)
            if not os.path.exists(src):
                print(f"  [MISS] {pdf_name[:50]}")
                continue
            safe = doi_safe(doi)
            dst = os.path.join(self.summary_dir, f"{safe}.pdf")

            try:
                if os.path.exists(dst):
                    os.remove(src)
                    print(f"  [DELETE] {pdf_name[:50]} → 目标已存在")
                else:
                    os.rename(src, dst)
                    print(f"  [RENAME] {pdf_name[:50]} → {safe}.pdf")
                results.append({"pdf_name": pdf_name, "doi": doi, "success": True})
            except PermissionError as e:
                print(f"  [LOCKED] {pdf_name[:50]} — 文件被占用")
                results.append({"pdf_name": pdf_name, "doi": doi, "success": False})

        return results

    # ------------------------------------------------------------------
    #  重命名
    # ------------------------------------------------------------------

    def rename_matched(self, results: list = None) -> int:
        """
        将所有匹配的 PDF 重命名为 DOI 格式。

        参数:
            results: 待处理的匹配列表（默认用 self.results 中 match=True 的）

        返回:
            成功处理的数量
        """
        if results is None:
            results = self.results

        matched = [r for r in results if r.get("match")]
        if not matched:
            print("没有匹配的条目可重命名。")
            return 0

        count = 0
        for r in matched:
            doi = r.get("cr_doi")
            if not doi:
                continue

            src = os.path.join(self.summary_dir, r["pdf_name"])
            if not os.path.exists(src):
                continue

            safe = doi_safe(doi)
            dst = os.path.join(self.summary_dir, f"{safe}.pdf")

            try:
                if os.path.exists(dst):
                    os.remove(src)
                    print(f"  [DELETE] {r['pdf_name'][:50]} (目标已存在)")
                else:
                    os.rename(src, dst)
                    print(f"  [RENAME] {r['pdf_name'][:50]} → {safe}.pdf")
                count += 1
            except PermissionError as e:
                print(f"  [LOCKED] {r['pdf_name'][:50]} — 文件被占用")

        return count

    # ------------------------------------------------------------------
    #  报告生成
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """返回当前状态的摘要统计"""
        matched = sum(1 for r in self.results if r.get("match"))
        mismatched = sum(
            1 for r in self.results
            if not r.get("match") and r.get("cr_doi")
        )
        return {
            "total_pdfs_scanned": len(self.entries),
            "cat_a": len(self._cat_a),
            "cat_b": len(self._cat_b),
            "cat_c": len(self._cat_c),
            "crossref_matched": matched,
            "crossref_mismatched": mismatched,
            "crossref_notfound": sum(
                1 for r in self.results if not r.get("cr_doi")
            ),
            "results_checked": len(self.results),
        }

    def report(self) -> str:
        """生成 Markdown 格式的完整匹配报告"""
        lines = [
            "# PDF DOI 匹配报告",
            "",
            f"扫描目录: `{self.summary_dir}`",
            f"报告生成: {__import__('time').strftime('%Y-%m-%d %H:%M')}",
            "",
        ]

        s = self.summary()
        lines.append("## 总览\n")
        lines.append("| 分类 | 数量 | 说明 |")
        lines.append("|------|:----:|------|")
        lines.append(f"| 扫描 PDF 总数 | {s['total_pdfs_scanned']} | |")
        lines.append(f"| A. 有 title+author | {s['cat_a']} | 可精确验证 |")
        lines.append(f"| B. 有 title 无 author | {s['cat_b']} | 查到但无法验证 |")
        lines.append(f"| C. 无 title | {s['cat_c']} | 需读 PDF |")
        lines.append("")

        # 匹配的
        matched = [r for r in self.results if r.get("match")]
        mismatched = [
            r for r in self.results
            if not r.get("match") and r.get("cr_doi") and r.get("final_author")
        ]
        notfound = [r for r in self.results if not r.get("cr_doi")]
        b_items = [r for r in self.results if not r.get("final_author")]

        if matched:
            lines.append(f"## ✅ 可安全重命名: {len(matched)} 篇\n")
            lines.append("| # | PDF 名 | DOI |")
            lines.append("|---|--------|:---:|")
            for i, r in enumerate(matched, 1):
                lines.append(
                    f"| {i} | `{r['pdf_name'][:45]}` | `{r['cr_doi']}` |"
                )
            lines.append("")

        if mismatched:
            lines.append(f"## ❓ Author 不匹配: {len(mismatched)} 篇\n")
            lines.append("| # | PDF 名 | JSON 作者 | CrossRef 作者 | DOI |")
            lines.append("|---|--------|:--------:|:----------:|:---:|")
            for i, r in enumerate(mismatched, 1):
                lines.append(
                    f"| {i} | `{r['pdf_name'][:42]}` | "
                    f"{r['final_author'][:18]} | "
                    f"{r.get('cr_author','?')[:18]} | "
                    f"`{r['cr_doi']}` |"
                )
            lines.append("")

        if notfound:
            lines.append(f"## ❌ 未找到: {len(notfound)} 篇\n")
            for r in notfound:
                lines.append(f"- `{r['pdf_name'][:45]}` — {r.get('note', '?')}")
            lines.append("")

        if b_items:
            lines.append(f"## 📋 B 组（无 author）: {len(b_items)} 篇\n")
            for r in b_items:
                doi = r.get("cr_doi", "(未找到)")
                lines.append(f"- `{r['pdf_name'][:45]}` → {doi}")
            lines.append("")

        return "\n".join(lines)

    def save_report(self, path: str = None):
        """保存 Markdown 报告到文件"""
        if path is None:
            path = os.path.join(self.output_dir, "PDF_DOI_匹配报告.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.report())
        print(f"报告已保存: {path}")

    # ------------------------------------------------------------------
    #  辅助
    # ------------------------------------------------------------------

    def _find_result(self, pdf_name: str) -> dict | None:
        for r in self.results:
            if r["pdf_name"] == pdf_name:
                return r
        return None

    # 以下属性提供快捷访问（保持与 __init__.py 导出一致）
    @property
    def cat_a(self):
        return self._cat_a

    @property
    def cat_b(self):
        return self._cat_b

    @property
    def cat_c(self):
        return self._cat_c