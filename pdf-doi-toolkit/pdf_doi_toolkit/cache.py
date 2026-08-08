"""
本地查询结果缓存 — 持久化 CrossRef 查询结果，避免重复网络请求。

用法:
    cache = DOICache("path/to/cache.json")
    cache.load()
    entry = cache.get("Smith_2023.pdf")
    if entry is None:
        # 调 API 查询
        cache.set("Smith_2023.pdf", result)
    cache.save()
"""

import json
import os
from datetime import datetime, timezone

from .config import CACHE_DEFAULT_FILENAME


CACHE_VERSION = 1


class DOICache:
    """
    DOI 查询结果缓存。

    参数:
        path: 缓存文件路径。None 表示不持久化（内存级开关）。
    """

    def __init__(self, path: str | None = None):
        self.path = path
        self._entries: dict[str, dict] = {}
        self._dirty = False

    # ------------------------------------------------------------------
    #  持久化
    # ------------------------------------------------------------------

    def load(self) -> dict[str, dict]:
        """
        从磁盘加载缓存。

        返回:
            self._entries — 加载后的条目字典
        """
        if not self.path:
            return self._entries

        if not os.path.exists(self.path):
            self._entries = {}
            return self._entries

        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            # 文件损坏 → 清空重新开始
            self._entries = {}
            return self._entries

        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            self._entries = {}
            return self._entries

        self._entries = data.get("entries", {})
        return self._entries

    def save(self) -> bool:
        """
        写入磁盘（仅当有变更时）。

        返回:
            True 写入成功，False 无写入（无变更 / 无路径）
        """
        if not self.path or not self._dirty:
            return False

        data = {
            "version": CACHE_VERSION,
            "entries": self._entries,
        }

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        self._dirty = False
        return True

    # ------------------------------------------------------------------
    #  读写
    # ------------------------------------------------------------------

    def get(self, pdf_name: str) -> dict | None:
        """
        按文件名查找缓存条目。

        返回:
            dict — 完整条目，或 None
        """
        entry = self._entries.get(pdf_name)
        if entry is None:
            return None
        # 返回副本，避免调用方意外修改缓存
        return dict(entry)

    def set(self, pdf_name: str, result: dict):
        """
        写入缓存条目。

        result 中应包含: doi, title, author, note, match, source 等字段。
        """
        entry = dict(result)
        entry.setdefault("ts", datetime.now(timezone.utc).isoformat())
        self._entries[pdf_name] = entry
        self._dirty = True

    def record_rename(self, pdf_name: str, new_doi_name: str):
        """
        记录重命名历史，标记源文件已处理。

        参数:
            pdf_name: 原始文件名
            new_doi_name: 重命名后的文件名（不含路径）
        """
        entry = self._entries.get(pdf_name)
        if entry:
            entry["renamed_to"] = new_doi_name
            entry["renamed_at"] = datetime.now(timezone.utc).isoformat()
            self._dirty = True

    # ------------------------------------------------------------------
    #  查询
    # ------------------------------------------------------------------

    def __contains__(self, pdf_name: str) -> bool:
        return pdf_name in self._entries

    def __len__(self) -> int:
        return len(self._entries)

    def keys(self):
        return self._entries.keys()

    def clear(self):
        """清空所有缓存条目。"""
        self._entries = {}
        self._dirty = True