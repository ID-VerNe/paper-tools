"""
x-mol.net 兜底查询器。

x-mol 是学术论文搜索引擎，搜标题即可返回完整的论文元数据（DOI、期刊、年份、作者等）。

API 端点（通过浏览器开发者工具抓到）:
  GET https://www.x-mol.net/api/u/paper/search
    ?option={标题}
    &pageIndex=1&searchSort=&impactFactorStart=&impactFactorEnd=
    &year=&matchPhrase=&searchField=&fj=

惊喜: 这个 API **不需要 cookie**，直接请求就能返回完整数据。
      底层是通过 Express Session 自动分配 JSESSIONID（但对我们透明）。

三种使用方式:
  1. 全自动: 直接调 API（无 cookie 也可用）
  2. 手动: 生成搜索链接，浏览器打开确认
  3. 确认清单: 生成 Markdown 让人工确认
"""

import json
import time
import urllib.parse
import urllib.request


class XMolFallback:
    """
    x-mol.net 兜底查询器。

    用法:
        # 方式 1: 全自动查询（无需 cookie）
        xm = XMolFallback()
        result = xm.query_by_title("论文标题")
        print(result["doi"], result["journal"], result["authors"])

        # 方式 2: 生成搜索链接（浏览器手动确认）
        url = XMolFallback.search_url("论文标题")

        # 方式 3: 生成人工确认清单
        checklist = XMolFallback.generate_checklist(entries)
    """

    SEARCH_URL = "https://www.x-mol.net/paper/search"
    API_URL = "https://www.x-mol.net/api/u/paper/search"

    def __init__(self, delay: float = 0.5):
        """
        参数:
            delay: 每次查询间隔（秒），避免触发限流
        """
        self.delay = delay

    # ------------------------------------------------------------------
    #  全自动查询（不需要 cookie）
    # ------------------------------------------------------------------

    def query_by_title(self, title: str, timeout: int = 15) -> dict:
        """
        通过 x-mol API 按标题搜索论文。

        参数:
            title: 论文标题（越完整越好）
            timeout: HTTP 超时秒数

        返回:
            {"doi": str|None, "title": str|None, "title_zh": str|None,
             "authors": list|None, "journal": str|None, "journal_id": int|None,
             "year": str|None, "impact_factor": str|None,
             "is_oa": bool|None, "publish_date": str|None,
             "paper_id": str|None, "summary": str|None,
             "note": "ok" | "未找到" | "错误信息"}
        """
        url = self._build_api_url(title)
        headers = self._build_headers()

        try:
            req = urllib.request.Request(url, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                data = json.loads(raw)
        except urllib.error.HTTPError as e:
            return {"doi": None, "note": f"HTTP {e.code}: {e.reason[:60] if e.reason else ''}"}
        except Exception as e:
            return {"doi": None, "note": str(e)[:80]}

        return self._extract_first_result(data)

    def query_by_doi(self, doi: str, timeout: int = 15) -> dict:
        """通过 x-mol API 按 DOI 查询论文详情"""
        return self.query_by_title(doi, timeout)

    def batch_query(self, titles: list) -> list:
        """
        批量查询多篇论文。

        参数:
            titles: 论文标题列表

        返回:
            list[dict] — 每项为 query_by_title 的结果
        """
        results = []
        for i, title in enumerate(titles):
            if i > 0:
                time.sleep(self.delay)
            print(f"  x-mol 查询 [{i+1}/{len(titles)}]: {title[:50]}...")
            result = self.query_by_title(title)
            results.append(result)
            if result.get("doi"):
                print(f"    → DOI: {result['doi']}  |  {result.get('journal', '')}")
            else:
                print(f"    → {result.get('note', '?')}")
        return results

    # ------------------------------------------------------------------
    #  搜索链接生成
    # ------------------------------------------------------------------

    @staticmethod
    def search_url(title: str) -> str:
        """生成 x-mol 搜索链接（浏览器打开即可看到期刊、DOI、IF、作者等信息）"""
        return f"{XMolFallback.SEARCH_URL}?option={urllib.parse.quote(title)}"

    # ------------------------------------------------------------------
    #  确认清单生成
    # ------------------------------------------------------------------

    @staticmethod
    def generate_checklist(entries: list, output_path: str = None) -> str:
        """
        生成人工确认清单（Markdown 格式）。

        参数:
            entries: list[dict] — 每项含 pdf_name、final_title、cr_doi 等
            output_path: 可选，保存到文件

        返回:
            Markdown 文本
        """
        lines = [
            "# 待确认 PDF DOI 重命名列表",
            "",
            "> 请逐条过目，告诉我编号和对应的真实 DOI。",
            "> 信息来源: **x-mol.net** — 点击搜索链接在浏览器打开即可验证。",
            "",
            "| # | PDF 名 | 标题 | CrossRef DOI | x-mol 搜索链接 |",
            "|---|--------|------|:-----------:|:-------------:|",
        ]

        for i, e in enumerate(entries, 1):
            title = (e.get("final_title") or e.get("title") or "")[:50]
            doi = e.get("cr_doi", "(未找到)")
            search_url = XMolFallback.search_url(title)
            pdf_short = e.get("pdf_name", "")[:40]

            lines.append(
                f"| {i} | `{pdf_short}` | {title} | `{doi}` | [搜索]({search_url}) |"
            )

        lines.extend([
            "",
            "---",
            "### 回复格式",
            "",
            "```",
            "[OK] 1, 3, 5    # 这些 DOI 正确，可以重命名",
            "[NO] 2, 4       # 这些不对",
            "---",
            "1 → 10.xxxx/xxxxx   # 或者逐条告诉我正确的 DOI",
            "```",
        ])

        text = "\n".join(lines)

        if output_path:
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"确认清单已保存: {output_path}")

        return text

    # ------------------------------------------------------------------
    #  内部方法
    # ------------------------------------------------------------------

    def _build_api_url(self, title: str) -> str:
        params = {
            "option": title,
            "pageIndex": "1",
            "searchSort": "",
            "impactFactorStart": "",
            "impactFactorEnd": "",
            "year": "",
            "matchPhrase": "",
            "searchField": "",
            "fj": "",
        }
        return f"{self.API_URL}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _build_headers() -> dict:
        return {
            "accept": "application/json",
            "accept-language": "zh-CN,zh;q=0.9",
            "content-type": "application/json",
            "referer": XMolFallback.SEARCH_URL,
            "user-agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/150.0.0.0 Safari/537.36"
            ),
        }

    def _extract_first_result(self, data: dict) -> dict:
        """
        从 x-mol API 响应中提取第一篇论文的信息。

        实际响应结构:
        {
          "jsonType": "JsonResult",
          "value": {
            "paperSimpleSearchResult": {
              "pageResults": {
                "pageNo": 1, "pageSize": 30, "totalRecord": N, "totalPage": M,
                "results": [
                  {
                    "thesisId": "...", "paperId": "...", "journalId": ...,
                    "doi": "10.xxxx/xxxxx",
                    "type": "TYPELESS",
                    "title": "<span style='...'>论文标题</span>",
                    "titleZh": "中文标题",
                    "summary": "...", "summaryZh": "...",
                    "hasTranslation": true,
                    "publishStatus": "Current",
                    "publishDate": 1748188800000,
                    "isOa": false, "oaStatus": "closed",
                    ...
                  }
                ]
              }
            }
          }
        }
        """
        import re

        try:
            # 解嵌套: value → paperSimpleSearchResult → pageResults → results
            value = data.get("value", {})
            search_result = value.get("paperSimpleSearchResult", {})
            page_results = search_result.get("pageResults", {})
            items = page_results.get("results", [])

            if not items:
                # 尝试其他可能的路径
                items = data.get("list") or data.get("results") or []

            if not items or len(items) == 0:
                return {"doi": None, "note": "未找到"}

            item = items[0]

            # 提取 DOI
            doi = item.get("doi", "") or ""

            # 提取标题（去 HTML 标签）
            title = item.get("title", "") or ""
            title = re.sub(r"<[^>]+>", "", title)

            # 提取中文标题
            title_zh = item.get("titleZh", "") or ""

            # 提取期刊
            journal = item.get("journalName") or item.get("journal") or ""

            # 提取年份
            year = str(item.get("year", "") or "")

            # 提取 IF
            impact_factor = str(item.get("impactFactor") or item.get("if_") or "")

            # 提取作者（实际字段名: author 为逗号分隔字符串, authorList 为列表）
            authors = []
            author_list = item.get("authorList")
            if isinstance(author_list, list) and len(author_list) > 0:
                authors = author_list
            else:
                author_str = item.get("author", "")
                if isinstance(author_str, str) and author_str.strip():
                    authors = [a.strip() for a in author_str.split(",") if a.strip()]

            # 提取 volume / page
            volume = str(item.get("volume", "") or "")
            page = str(item.get("page", "") or "")

            # 提取摘要
            summary = item.get("summary", "") or ""
            summary = re.sub(r"<[^>]+>", "", summary)

            # 提取 paperId
            paper_id = item.get("paperId") or item.get("thesisId") or ""

            result = {
                "doi": doi or None,
                "title": title or None,
                "title_zh": title_zh or None,
                "authors": authors,
                "journal": journal or None,
                "journal_id": item.get("journalId"),
                "journal_short": item.get("journalShortName", ""),
                "year": year or None,
                "volume": volume or None,
                "page": page or None,
                "impact_factor": impact_factor or None,
                "is_oa": item.get("isOa"),
                "pub_date": item.get("pubDate", ""),
                "paper_id": item.get("paperId") or item.get("thesisId") or "",
                "url": item.get("url", ""),
                "keywords": item.get("keywordList", []),
                "summary": summary or None,
                "note": "ok" if doi else "未找到 DOI",
            }
            return result

        except (KeyError, IndexError, TypeError, AttributeError) as e:
            return {"doi": None, "note": f"解析失败: {str(e)[:60]}"}