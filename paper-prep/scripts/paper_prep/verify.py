"""校验工具:标题相似度、Crossref 反查、bib manifest 解析。"""

from __future__ import annotations

import re

from paper_prep.config import TITLE_STOPWORDS, VERIFY_TITLE_THRESHOLD


def title_similarity(title_a: str, title_b: str) -> float:
    """归一化标题相似度(token 重叠率,0~1)。

    对小写、去标点、去停用词后的 token 集合算 Jaccard 重叠,
    与『同一篇论文标题』时通常 >0.5;与『一篇不相关的论文』时通常 <0.2。
    """
    def tokens(t: str) -> set[str]:
        t = t.lower()
        t = re.sub(r"[^\w\s]", " ", t)
        t = re.sub(r"\d+", " ", t)
        words = {w for w in t.split() if w and w not in TITLE_STOPWORDS}
        return words

    a = tokens(title_a or "")
    b = tokens(title_b or "")
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def crossref_canonical(client, doi: str) -> dict:
    """按 DOI 反查 CrossRef 权威标题与第一作者。

    返回 {"title": str|None, "first_author": str|None, "ok": bool}
    """
    try:
        res = client.search_by_doi(doi)
        title = res.get("title")
        author = res.get("author")
        if title:
            return {"title": title, "first_author": author or "", "ok": True}
        return {"title": None, "first_author": None, "ok": False}
    except Exception:
        return {"title": None, "first_author": None, "ok": False}


def load_bib_manifest(bib_path: str) -> dict[str, dict]:
    """从 references.bib 解析 {normalized_doi: {title, first_author}}。

    用于 --bib 选项:把用户登记的期望条目作为第三方真值参与校验。
    """
    manifest: dict[str, dict] = {}
    try:
        with open(bib_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
    except OSError:
        print(f"  [WARN] 无法读取 bib 文件: {bib_path}")
        return manifest

    entries = re.split(r"@\w+\{", content)
    for e in entries:
        key_m = re.match(r"([^,]+)", e)
        doi_m = re.search(r"DOI\s*=\s*\{([^}]+)\}", e)
        title_m = re.search(r"title\s*=\s*\{([^}]+)\}", e)
        author_m = re.search(r"author\s*=\s*\{([^}]+)\}", e)
        if not key_m or not doi_m:
            continue
        title = title_m.group(1) if title_m else None
        authors = author_m.group(1) if author_m else None
        # 第一作者(清理 LaTeX 转义与花括号)
        first_author = ""
        if authors:
            parts = authors.split(" and ")
            if parts:
                fa = re.sub(r"[\\{}~^'\"]", "", parts[0]).strip()
                first_author = fa.split(",")[0].strip() if "," in fa else fa
        manifest[doi_m.group(1).strip().lower()] = {
            "title": title,
            "first_author": first_author,
        }
    return manifest


def doi_safe(doi: str) -> str:
    """DOI 字符串转文件名安全形式(斜杠→下划线)。"""
    return doi.replace("/", "_")


def make_canonical_sim_pair_decision(sim: float, llm_ok, canon_ok: bool):
    """双通道综合决策。

    返回 (status, verified, should_skip):
      status: "matched" 或 "no_confidence"
      verified: bool(仅 matched 时有意义)
      should_skip: True 表示该条目应跳过后续 append(已 append 为 no_confidence)

    决策表(与 match 步骤中的注释一致):
      余弦≥0.5 且 LLM 不否定     → matched, verified=True
      余弦≥0.5 但 LLM 明确 NO     → matched, verified=False
      余弦<0.5 但 LLM 明确 YES    → matched, verified=False
      余弦<0.5 且 LLM 明确 NO      → no_confidence
      LLM 不可用(None)时        → 退化为仅余弦判断
    """
    if not canon_ok:
        return "matched", False, False
    if sim >= VERIFY_TITLE_THRESHOLD and llm_ok is not False:
        return "matched", True, False
    if sim < VERIFY_TITLE_THRESHOLD and llm_ok is False:
        return "no_confidence", False, True
    return "matched", False, False