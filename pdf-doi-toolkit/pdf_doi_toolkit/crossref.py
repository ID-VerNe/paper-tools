"""
CrossRef API 客户端。

功能:
  - 按标题查询 DOI（带 author 验证）
  - 按 DOI 直接查询元数据
  - 指数退避重试（应对 429 限流）
  - 多线程并发查询
"""

import json
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from .config import (
    CROSSREF_BASE_URL,
    CROSSREF_MAX_RETRIES,
    CROSSREF_CONCURRENCY,
    CROSSREF_REQUEST_DELAY,
    CROSSREF_TIMEOUT,
    DEFAULT_USER_AGENT,
    FUZZY_SEARCH_ROWS,
)
from .utils import authors_match


class CrossRefClient:
    """
    CrossRef API 客户端。

    用法:
        client = CrossRefClient()
        result = client.search_by_title("论文标题", "期望作者")
        print(result["doi"], result["author"])

        result2 = client.search_by_doi("10.xxxx/xxxxx")
        print(result2["title"], result2["author"])
    """

    def __init__(self, user_agent: str = None, max_retries: int = None,
                 concurrency: int = None, request_delay: float = None,
                 timeout: int = None):
        self.user_agent = user_agent or DEFAULT_USER_AGENT
        self.max_retries = max_retries or CROSSREF_MAX_RETRIES
        self.concurrency = concurrency or CROSSREF_CONCURRENCY
        self.request_delay = request_delay or CROSSREF_REQUEST_DELAY
        self.timeout = timeout or CROSSREF_TIMEOUT
        self._lock = threading.Lock()
        self._req_count = 0

    # ------------------------------------------------------------------
    #  公开方法
    # ------------------------------------------------------------------

    def search_by_title(self, title: str, expected_author: str = None) -> dict:
        """
        按标题在 CrossRef 搜索论文。

        参数:
            title: 论文标题（越完整越好）
            expected_author: 期望的第一作者（用于匹配加分）

        返回:
            {"doi": str|None, "author": str|None, "title": str|None, "note": str}
        """
        url = self._build_search_url(title)

        with self._lock:
            self._req_count += 1

        for attempt in range(1, self.max_retries + 1):
            time.sleep(self.request_delay)

            data = self._request(url, attempt, title)
            if data is None:
                continue  # 重试

            items = data.get("message", {}).get("items", [])
            if not items:
                return {"doi": None, "author": None, "title": None,
                        "note": "no_results"}

            best = self._pick_best(items, title, expected_author)
            if best["doi"]:
                return best
            return {"doi": None, "author": None, "title": None,
                    "note": "no_good_match"}

        return {"doi": None, "author": None, "title": None,
                "note": "retries_exhausted"}

    def search_by_doi(self, doi: str) -> dict:
        """
        通过 DOI 直接查询 CrossRef。

        参数:
            doi: 完整 DOI（如 "10.1021/acs.analchem.5c01686"）

        返回:
            {"doi": str, "author": str, "title": str, "note": "ok"}
            失败时返回 {"doi": ..., "note": "错误信息"}
        """
        url = f"{CROSSREF_BASE_URL}/{doi}"
        time.sleep(self.request_delay)

        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            return {"doi": doi, "author": None, "title": None,
                    "note": str(e)[:60]}

        msg = data.get("message", {})
        title = self._extract_title(msg)
        first_author = self._get_first_author(msg.get("author", []))
        return {"doi": doi, "author": first_author, "title": title, "note": "ok"}

    # ------------------------------------------------------------------
    #  模糊搜索（文件名线索）
    # ------------------------------------------------------------------

    def search_by_fuzzy(self, author: str = None, year: int = None,
                        keywords: list = None) -> dict:
        """
        用文件名中提取的线索模糊搜索 CrossRef。

        参数:
            author: 期望的作者姓氏（从文件名解析）
            year: 期望的年份（从文件名解析）
            keywords: 关键词列表（从文件名解析）

        返回:
            {"doi": str|None, "author": str|None, "title": str|None,
             "year": str|None, "note": str}
        """
        if not author and not year and not keywords:
            return {"doi": None, "author": None, "title": None,
                    "year": None, "note": "no_clues"}

        url = self._build_fuzzy_search_url(author, year, keywords)

        with self._lock:
            self._req_count += 1

        for attempt in range(1, self.max_retries + 1):
            time.sleep(self.request_delay)

            data = self._request(url, attempt, str(keywords or author or ""))
            if data is None:
                continue

            items = data.get("message", {}).get("items", [])
            if not items:
                return {"doi": None, "author": None, "title": None,
                        "year": None, "note": "no_results"}

            search_title = " ".join(keywords) if keywords else ""
            best = self._pick_best(items, search_title, author, expected_year=year)
            if best["doi"]:
                return best
            return {"doi": None, "author": None, "title": None,
                    "year": None, "note": "no_good_match"}

        return {"doi": None, "author": None, "title": None,
                "year": None, "note": "retries_exhausted"}

    def _build_fuzzy_search_url(self, author: str = None, year: int = None,
                                keywords: list = None) -> str:
        """构建模糊搜索的 CrossRef URL。"""
        params = {"rows": FUZZY_SEARCH_ROWS}
        if keywords:
            params["query.title"] = " ".join(keywords)
        if author:
            params["query.author"] = author
        if year is not None:
            params["filter"] = f"from-pub-date:{year},until-pub-date:{year}"
        return f"{CROSSREF_BASE_URL}?{urllib.parse.urlencode(params)}"

    @staticmethod
    def _extract_year(item: dict) -> int | None:
        """从 CrossRef 返回项中提取出版年份。"""
        for date_field in ("published-print", "published-online", "issued", "created"):
            date_parts = item.get(date_field, {}).get("date-parts", [[]])[0]
            if date_parts and date_parts[0]:
                try:
                    return int(date_parts[0])
                except (ValueError, TypeError):
                    continue
        return None

    # ------------------------------------------------------------------
    #  内部方法
    # ------------------------------------------------------------------

    def _build_search_url(self, title: str) -> str:
        params = urllib.parse.urlencode({"query.title": title, "rows": 3})
        return f"{CROSSREF_BASE_URL}?{params}"

    def _request(self, url: str, attempt: int, title_hint: str) -> dict | None:
        """发送 HTTP 请求，返回 parsed JSON；失败时根据 attempt 决定是否重试"""
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.user_agent})
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < self.max_retries:
                self._backoff(attempt, title_hint, "429 限流")
                return None
            # 非 429 或重试用尽 → 抛出 caller 可见的错误
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as e:
            if attempt < self.max_retries:
                self._backoff(attempt, title_hint, f"网络错误: {str(e)[:40]}")
                return None
            return None
        except Exception as e:
            if attempt < self.max_retries:
                self._backoff(attempt, title_hint, f"异常: {str(e)[:30]}")
                return None
            return None

    def _backoff(self, attempt: int, title_hint: str, reason: str):
        delay = (2 ** attempt) + random.uniform(0, 1)
        print(f"  [{reason}] {title_hint[:45]}... 等待 {delay:.1f}s ({attempt}/{self.max_retries})")
        time.sleep(delay)

    def _get_first_author(self, authors: list) -> str:
        """从作者列表提取第一作者"""
        for a in authors:
            if a.get("sequence") == "first":
                return " ".join(filter(None, [a.get("given", ""), a.get("family", "")]))
        if authors:
            a = authors[0]
            return " ".join(filter(None, [a.get("given", ""), a.get("family", "")]))
        return ""

    def _extract_title(self, msg: dict) -> str:
        """从 message 提取标题"""
        titles = msg.get("title", [""])
        if isinstance(titles, list):
            return titles[0] if titles else ""
        return str(titles)

    def _pick_best(self, items: list, title: str,
                   expected_author: str = None,
                   expected_year: int = None) -> dict | None:
        """
        从 CrossRef 返回结果中选最佳匹配。

        评分规则:
          - 标题词重叠数（基础分）
          - author 匹配: +50 分（高权，确保 author 优先）
          - 年份匹配: 精确 +30，差 1 年 +20
        """
        best_score = -1
        best = None
        tl = title.lower().strip()

        for item in items:
            item_title = self._extract_title(item)
            item_doi = item.get("DOI", "")
            first_author = self._get_first_author(item.get("author", []))

            # 标题词重叠
            words = set(item_title.lower().split()) & set(tl.split())
            score = len(words)

            # author 匹配 → 高权重加分
            if expected_author and authors_match(expected_author, first_author):
                score += 50

            # 年份匹配 → 加分
            if expected_year is not None:
                item_year = self._extract_year(item)
                if item_year is not None:
                    if item_year == expected_year:
                        score += 30
                    elif abs(item_year - expected_year) <= 1:
                        score += 20

            if score > best_score:
                best_score = score
                best = {
                    "doi": item_doi,
                    "author": first_author,
                    "title": item_title,
                    "year": str(self._extract_year(item)) if self._extract_year(item) else None,
                    "note": "ok",
                }

        return best