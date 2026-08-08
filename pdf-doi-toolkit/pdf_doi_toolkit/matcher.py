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

from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from .cache import DOICache
from .config import (
    CROSSREF_CONCURRENCY,
    FUZZY_AUTO_RENAME_THRESHOLD,
    FUZZY_REVIEW_THRESHOLD,
    FUZZY_SCORE_AUTHOR,
    FUZZY_SCORE_YEAR_EXACT,
    FUZZY_SCORE_YEAR_NEAR,
    FUZZY_SCORE_KEYWORD_MAX,
)
from .crossref import CrossRefClient
from .fuzzy import FilenameParser
from .scanner import PDFScanner
from .title_extractor import TitleExtractor
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
                 crossref_client: CrossRefClient = None,
                 cache_path: str = None, no_cache: bool = False,
                 ocr_dir: str = None):
        self.summary_dir = summary_dir
        self.output_dir = output_dir or summary_dir
        self.cr = crossref_client or CrossRefClient()
        self.scanner = PDFScanner(summary_dir)

        self.entries = []
        self.results = []

        self._cat_a = []
        self._cat_b = []
        self._cat_c = []

        # 缓存
        self._cache_path = None
        if not no_cache and cache_path:
            self._cache_path = cache_path
        self.cache = DOICache(self._cache_path)
        self.cache.load()
        self._cache_hits = 0  # 本次运行的实际缓存命中计数

        # OCR
        self._title_extractor = TitleExtractor(ocr_dir)

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
                    res = f.result()
                    self.results.append(res)
                    self._cache_result(res)
                    if i % 10 == 0 or i == len(self._cat_a):
                        print(f"  进度: {i}/{len(self._cat_a)}")

        # B 组: 有 title 无 author → 查到但标记不可验证
        if self._cat_b:
            print(f"CrossRef B 组（有 title 无 author）: {len(self._cat_b)} 篇...")
            for e in self._cat_b:
                res = self._check_with_cache(
                    e["pdf_name"],
                    lambda: self.cr.search_by_title(e["json_title"]),
                )
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
                self._cache_result(self.results[-1])

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

        # 缓存优先
        cached = self.cache.get(entry["pdf_name"])
        if cached and cached.get("doi"):
            self._cache_hits += 1
            result = dict(entry)
            result["final_title"] = result.get("json_title", "")
            result["final_author"] = result.get("json_author", "")
            result["cr_doi"] = cached["doi"]
            result["cr_author"] = cached.get("author")
            result["match"] = cached.get("match", False)
            result["note"] = cached.get("note", "ok")
            return result

        # 第 1 步：正常查询
        res = self.cr.search_by_title(title, author)

        result = dict(entry)
        result["final_title"] = result.get("json_title", "")
        result["final_author"] = result.get("json_author", "")
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

    def _check_with_cache(self, pdf_name: str, api_call: callable) -> dict:
        """
        缓存优先的 CrossRef 查询。

        参数:
            pdf_name: 文件名（缓存键）
            api_call: 无参数可调用，返回 {"doi": ..., "author": ..., ...}

        返回:
            dict — 与 api_call 相同的结构
        """
        cached = self.cache.get(pdf_name)
        if cached and cached.get("doi"):
            return cached
        res = api_call()
        if self._cache_path and res.get("doi"):
            self.cache.set(pdf_name, {
                "doi": res["doi"],
                "author": res.get("author"),
                "title": res.get("title"),
                "note": res.get("note"),
                "match": bool(res.get("doi")),
                "source": "crossref",
            })
        return res

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

            # 源和目标相同 → 已正确命名，跳过
            if src == dst:
                print(f"  [SKIP] {pdf_name[:50]} → 已正确命名")
                results.append({"pdf_name": pdf_name, "doi": doi, "success": True})
                continue

            try:
                if os.path.exists(dst):
                    os.remove(src)
                    print(f"  [DELETE] {pdf_name[:50]} → 目标已存在")
                else:
                    os.rename(src, dst)
                    print(f"  [RENAME] {pdf_name[:50]} → {safe}.pdf")
                results.append({"pdf_name": pdf_name, "doi": doi, "success": True})
                self.cache.set(pdf_name, {
                    "doi": doi,
                    "note": "manual_verify",
                    "match": True,
                    "source": "manual",
                })
            except PermissionError as e:
                print(f"  [LOCKED] {pdf_name[:50]} — 文件被占用")
                results.append({"pdf_name": pdf_name, "doi": doi, "success": False})

        return results

    # ------------------------------------------------------------------
    #  模糊文件名匹配兜底
    # ------------------------------------------------------------------

    def run_fuzzy_fallback(self) -> list:
        """
        对 Cat C 和失败 Cat B 执行模糊文件名匹配。

        在 run_crossref_check() 之后调用。
        评分 >= FUZZY_AUTO_RENAME_THRESHOLD → match=True（自动重命名）
        评分 >= FUZZY_REVIEW_THRESHOLD     → 生成 x-mol 人工确认清单
        """
        to_process = list(self._cat_c)

        for entry in self._cat_b:
            result = self._find_result(entry["pdf_name"])
            if result and not result.get("cr_doi"):
                to_process.append(entry)

        if not to_process:
            return []

        print(f"模糊文件名匹配: {len(to_process)} 篇...")

        fuzzy_results = []
        for i, entry in enumerate(to_process, 1):
            pdf_name = entry["pdf_name"]
            parsed = FilenameParser.parse(pdf_name)

            if not parsed.year and not parsed.keywords and not parsed.author:
                fuzzy_results.append({
                    "pdf_name": pdf_name,
                    "final_title": "",
                    "final_author": "",
                    "cr_doi": None, "cr_author": None,
                    "match": False,
                    "note": "fuzzy: insufficient_clues",
                    "fuzzy_score": 0,
                })
                continue

            # 缓存优先
            cached = self.cache.get(pdf_name)
            if cached and cached.get("doi"):
                self._cache_hits += 1
                score = cached.get("fuzzy_score", 0)
                auto = score >= FUZZY_AUTO_RENAME_THRESHOLD
                fuzzy_results.append({
                    "pdf_name": pdf_name,
                    "final_title": cached.get("title", ""),
                    "final_author": cached.get("author", ""),
                    "cr_doi": cached["doi"],
                    "cr_author": cached.get("author", ""),
                    "match": cached.get("match", auto),
                    "note": f"fuzzy: score={score} (cached)",
                    "fuzzy_score": score,
                })
                continue

            res = self.cr.search_by_fuzzy(
                author=parsed.author or None,
                year=parsed.year,
                keywords=parsed.keywords or None,
            )

            if res["doi"]:
                score = self._compute_fuzzy_score(parsed, res)
                auto = score >= FUZZY_AUTO_RENAME_THRESHOLD
                result = {
                    "pdf_name": pdf_name,
                    "final_title": res.get("title", ""),
                    "final_author": res.get("author", ""),
                    "cr_doi": res["doi"],
                    "cr_author": res.get("author", ""),
                    "match": auto,
                    "note": f"fuzzy: score={score}" + (" (auto)" if auto else " (review)"),
                    "fuzzy_score": score,
                    "source": "fuzzy",
                }
                fuzzy_results.append(result)
                self._cache_result(result)
                status = "AUTO" if auto else "REVIEW"
                print(f"  [{status}] {i}/{len(to_process)} {pdf_name[:40]} -> {res['doi']}  score={score}")
            else:
                fuzzy_results.append({
                    "pdf_name": pdf_name,
                    "final_title": "",
                    "final_author": "",
                    "cr_doi": None, "cr_author": None,
                    "match": False,
                    "note": f"fuzzy: {res.get('note', 'not_found')}",
                    "fuzzy_score": 0,
                })
                print(f"  [SKIP] {i}/{len(to_process)} {pdf_name[:40]} -> {res.get('note', 'not_found')}")

        self.results.extend(fuzzy_results)

        # 需要人工确认的 → 生成 x-mol 确认清单
        review_items = [
            r for r in fuzzy_results
            if FUZZY_REVIEW_THRESHOLD <= (r.get("fuzzy_score", 0) or 0) < FUZZY_AUTO_RENAME_THRESHOLD
        ]
        if review_items:
            from .xmol import XMolFallback
            checklist_path = os.path.join(self.output_dir, "fuzzy_待确认_清单.md")
            XMolFallback.generate_checklist(review_items, output_path=checklist_path)

        return fuzzy_results

    def _compute_fuzzy_score(self, parsed, cr_result: dict) -> int:
        """
        计算模糊匹配的置信度评分 (0-100)。

        评分分量:
          - 作者匹配 0-50: 文件名作者 vs CrossRef 第一作者
          - 年份匹配 0-30: 精确=30, 邻近=20
          - 关键词覆盖 0-20: 关键词在标题中的占比
        """
        score = 0

        # 1. 作者匹配
        if parsed.author and cr_result.get("author"):
            cr_first = cr_result["author"]
            cr_surname = cr_first.split()[-1] if cr_first else ""
            if authors_match(parsed.author, cr_surname):
                score += FUZZY_SCORE_AUTHOR

        # 2. 年份匹配
        if parsed.year is not None and cr_result.get("year"):
            try:
                cr_year = int(cr_result["year"])
                if cr_year == parsed.year:
                    score += FUZZY_SCORE_YEAR_EXACT
                elif abs(cr_year - parsed.year) <= 1:
                    score += FUZZY_SCORE_YEAR_NEAR
            except (ValueError, TypeError):
                pass

        # 3. 关键词覆盖
        if parsed.keywords and cr_result.get("title"):
            title_lower = cr_result["title"].lower()
            matched = sum(1 for kw in parsed.keywords if kw.lower() in title_lower)
            if parsed.keywords:
                ratio = matched / len(parsed.keywords)
                score += int(ratio * FUZZY_SCORE_KEYWORD_MAX)

        return min(score, 100)

    # ------------------------------------------------------------------
    #  OCR 标题提取兜底
    # ------------------------------------------------------------------

    def run_ocr_fallback(self) -> list:
        """
        对剩余未匹配的 PDF 调 DeepSeek-OCR 提取标题，再查 CrossRef。

        在 run_crossref_check() / run_fuzzy_fallback() 之后调用。
        OCR 是付费 API，只处理确认无 title 的条目。
        """
        to_process = self._get_unmatched_entries()
        if not to_process:
            return []

        if not self._title_extractor._locate_ocr_dir():
            print("[OCR] DeepSeek-OCR 目录未找到，跳过 OCR 兜底")
            for e in to_process:
                self._mark_ocr_failed(e, "ocr_dir_not_found")
            return []

        print(f"[OCR] 调 DeepSeek-OCR 提取标题: {len(to_process)} 篇...")

        ocr_results = []
        for i, entry in enumerate(to_process, 1):
            pdf_name = entry["pdf_name"]
            pdf_path = os.path.join(self.summary_dir, pdf_name)

            if not os.path.exists(pdf_path):
                self._mark_ocr_failed(entry, "pdf_missing")
                continue

            ex = self._title_extractor.extract_title(pdf_path)
            title = ex.get("title")
            if not title:
                self._mark_ocr_failed(entry, ex.get("note", "ocr_failed"))
                print(f"  [SKIP] {i}/{len(to_process)} {pdf_name[:40]} -> {ex.get('note')}")
                continue

            res = self.cr.search_by_title(title)
            result = {
                "pdf_name": pdf_name,
                "final_title": title,
                "final_author": "",
                "cr_doi": res.get("doi"),
                "cr_author": res.get("author"),
                "match": bool(res.get("doi")),
                "note": f"ocr: found {res.get('doi')}" if res.get("doi")
                        else f"ocr: {res.get('note', 'not_found')}",
                "source": "ocr",
            }
            ocr_results.append(result)
            self._cache_result(result)
            if res.get("doi"):
                print(f"  [OK] {i}/{len(to_process)} {pdf_name[:40]} -> {res['doi']}")
            else:
                print(f"  [SKIP] {i}/{len(to_process)} {pdf_name[:40]} -> not found")

        self.results.extend(ocr_results)
        return ocr_results

    def _get_unmatched_entries(self) -> list:
        """收集所有尚未匹配到 DOI 的条目（Cat C + 失败的 Cat B）。"""
        matched_pdfs = {
            r["pdf_name"] for r in self.results if r.get("cr_doi")
        }
        to_process = [
            e for e in self._cat_c if e["pdf_name"] not in matched_pdfs
        ]
        for entry in self._cat_b:
            result = self._find_result(entry["pdf_name"])
            if result and not result.get("cr_doi"):
                to_process.append(entry)
        return to_process

    def _mark_ocr_failed(self, entry: dict, note: str):
        """为 OCR 处理失败的条目标记。"""
        self.results.append({
            "pdf_name": entry["pdf_name"],
            "final_title": "",
            "final_author": "",
            "cr_doi": None, "cr_author": None,
            "match": False,
            "note": f"ocr: {note}",
        })

    # ------------------------------------------------------------------
    #  缓存辅助
    # ------------------------------------------------------------------

    def _cache_result(self, result: dict):
        """把查询结果写入缓存。"""
        if not self._cache_path:
            return
        self.cache.set(result["pdf_name"], {
            "doi": result.get("cr_doi"),
            "title": result.get("final_title"),
            "author": result.get("cr_author") or result.get("final_author"),
            "note": result.get("note"),
            "match": result.get("match", False),
            "fuzzy_score": result.get("fuzzy_score"),
            "source": result.get("source", "crossref"),
        })

    def save_cache(self):
        """持久化缓存到磁盘。"""
        self.cache.save()
        if self._cache_path:
            print(f"缓存已保存: {self._cache_path}")

    # ------------------------------------------------------------------
    #  冲突检测
    # ------------------------------------------------------------------

    def _get_conflicts(self, results: list = None) -> list:
        """
        检测多个 PDF 映射到同一 DOI 的冲突。

        返回: list[dict] — 每条含 doi 和映射到该 DOI 的 pdf 列表
        """
        if results is None:
            results = self.results

        groups = defaultdict(list)
        for r in results:
            doi = r.get("cr_doi")
            if doi:
                groups[doi].append(r["pdf_name"])

        return [
            {"doi": doi, "pdfs": pdfs}
            for doi, pdfs in groups.items() if len(pdfs) > 1
        ]

    # ------------------------------------------------------------------
    #  重命名
    # ------------------------------------------------------------------

    def rename_matched(self, results: list = None) -> dict:
        """
        将所有匹配的 PDF 重命名为 DOI 格式。

        参数:
            results: 待处理的匹配列表（默认用 self.results 中 match=True 的）

        返回:
            {"renamed": int, "conflicts": list, "deduped": int, "skipped": int}
        """
        if results is None:
            results = self.results

        matched = [r for r in results if r.get("match")]
        if not matched:
            print("没有匹配的条目可重命名。")
            return {"renamed": 0, "conflicts": [], "deduped": 0, "skipped": 0}

        # 第 1 步：检测 DOI 冲突
        conflicts = self._get_conflicts(matched)
        conflict_dois = {c["doi"] for c in conflicts}
        conflict_pdfs = {pdf for c in conflicts for pdf in c["pdfs"]}

        if conflicts:
            print(f"检测到 {len(conflicts)} 组分 DOI 冲突，跳过冲突项:")

        named = 0
        deduped = 0
        skipped = 0
        for r in matched:
            doi = r.get("cr_doi")
            if not doi:
                continue

            src = os.path.join(self.summary_dir, r["pdf_name"])
            if not os.path.exists(src):
                continue

            # 冲突项跳过
            if doi in conflict_dois or r["pdf_name"] in conflict_pdfs:
                skipped += 1
                continue

            safe = doi_safe(doi)
            dst = os.path.join(self.summary_dir, f"{safe}.pdf")

            # 源和目标相同 → 已正确命名，跳过
            if src == dst:
                named += 1
                continue

            try:
                if os.path.exists(dst):
                    os.remove(src)
                    print(f"  [DEDUP] {r['pdf_name'][:50]} (目标已存在)")
                    deduped += 1
                else:
                    os.rename(src, dst)
                    print(f"  [RENAME] {r['pdf_name'][:50]} → {safe}.pdf")
                    named += 1
                self.cache.record_rename(r["pdf_name"], f"{safe}.pdf")
            except PermissionError as e:
                print(f"  [LOCKED] {r['pdf_name'][:50]} — 文件被占用")
                skipped += 1

        return {
            "renamed": named,
            "conflicts": conflicts,
            "deduped": deduped,
            "skipped": skipped,
        }

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
        fuzzy_entries = [r for r in self.results if r.get("note", "").startswith("fuzzy:")]
        fuzzy_matched = sum(1 for r in fuzzy_entries if r.get("match"))
        fuzzy_review = sum(1 for r in fuzzy_entries
                           if r.get("cr_doi") and not r.get("match"))
        fuzzy_skipped = sum(1 for r in fuzzy_entries if not r.get("cr_doi"))
        ocr_entries = [r for r in self.results if r.get("note", "").startswith("ocr:")]
        ocr_matched = sum(1 for r in ocr_entries if r.get("match"))
        ocr_failed = sum(1 for r in ocr_entries if not r.get("cr_doi"))
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
            "fuzzy_attempted": len(fuzzy_entries),
            "fuzzy_matched": fuzzy_matched,
            "fuzzy_needs_review": fuzzy_review,
            "fuzzy_failed": fuzzy_skipped,
            "ocr_attempted": len(ocr_entries),
            "ocr_matched": ocr_matched,
            "ocr_failed": ocr_failed,
            "cache_hits": self._cache_hits if self._cache_path else 0,
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
            lines.append(f"## B 组（无 author）: {len(b_items)} 篇\n")
            for r in b_items:
                doi = r.get("cr_doi", "(未找到)")
                lines.append(f"- `{r['pdf_name'][:45]}` → {doi}")
            lines.append("")

        # 模糊匹配结果
        fuzzy_entries = [r for r in self.results if r.get("note", "").startswith("fuzzy:")]
        fuzzy_high = [r for r in fuzzy_entries if r.get("match")]
        fuzzy_med = [r for r in fuzzy_entries if r.get("cr_doi") and not r.get("match")]

        if fuzzy_high:
            lines.append(f"## Fuzzy 高置信度匹配: {len(fuzzy_high)} 篇\n")
            lines.append("| # | PDF 名 | 匹配标题 | DOI | 评分 |")
            lines.append("|---|--------|---------|:---:|:---:|")
            for i, r in enumerate(fuzzy_high, 1):
                lines.append(
                    f"| {i} | `{r['pdf_name'][:35]}` | "
                    f"{r.get('final_title','')[:40]} | "
                    f"`{r['cr_doi']}` | {r.get('fuzzy_score',0)} |"
                )
            lines.append("")

        if fuzzy_med:
            lines.append(f"## Fuzzy 需人工确认: {len(fuzzy_med)} 篇\n")
            for r in fuzzy_med:
                lines.append(
                    f"- `{r['pdf_name'][:40]}` -> `{r['cr_doi']}` "
                    f"(评分: {r.get('fuzzy_score',0)})"
                )
            lines.append("")

        # OCR 结果
        ocr_entries = [r for r in self.results if r.get("note", "").startswith("ocr:")]
        ocr_ok = [r for r in ocr_entries if r.get("match")]
        ocr_fail = [r for r in ocr_entries if not r.get("cr_doi")]

        if ocr_ok:
            lines.append(f"## OCR 标题提取匹配: {len(ocr_ok)} 篇\n")
            lines.append("| # | PDF 名 | 提取标题 | DOI |")
            lines.append("|---|--------|---------|:---:|")
            for i, r in enumerate(ocr_ok, 1):
                lines.append(
                    f"| {i} | `{r['pdf_name'][:35]}` | "
                    f"{r.get('final_title','')[:40]} | "
                    f"`{r['cr_doi']}` |"
                )
            lines.append("")

        if ocr_fail:
            lines.append(f"## OCR 处理失败: {len(ocr_fail)} 篇\n")
            for r in ocr_fail:
                lines.append(f"- `{r['pdf_name'][:45]}` — {r.get('note', '?')}")
            lines.append("")

        # 冲突检测
        conflicts = self._get_conflicts()
        if conflicts:
            lines.append(f"## ⚠️ DOI 冲突: {len(conflicts)} 组\n")
            lines.append("以下 DOI 被多篇 PDF 映射到，已跳过重命名：\n")
            for c in conflicts:
                lines.append(f"- `{c['doi']}`")
                for pdf in c["pdfs"]:
                    lines.append(f"  - `{pdf}`")
            lines.append("")

        # 缓存信息
        if self._cache_path:
            lines.append(f"## 缓存\n")
            lines.append(f"- 缓存文件: `{self._cache_path}`")
            lines.append(f"- 缓存条目: {len(self.cache)} 条")
            lines.append("")

        return "\n".join(lines)

    def save_report(self, path: str = None):
        """保存 Markdown 报告到文件，同时持久化缓存。"""
        if path is None:
            path = os.path.join(self.output_dir, "PDF_DOI_匹配报告.md")
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.report())
        self.save_cache()
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