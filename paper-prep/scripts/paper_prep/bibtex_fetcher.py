"""BibTeX 拉取:通过 DOI.org 内容协商拿 BibTeX 文本。"""

from __future__ import annotations

import ssl
import time
import urllib.request

from paper_prep.config import BIBTEX_MAX_RETRIES, BIBTEX_TIMEOUT


def fetch_bibtex(doi: str, user_agent: str) -> str | None:
    """通过 DOI.org 内容协商拉取 BibTeX。

    重试 BIBTEX_MAX_RERIES 次,指数退避。返回 BibTeX 文本或 None。
    """
    url = f"https://doi.org/{doi}"
    ctx = ssl._create_unverified_context()
    for attempt in range(1, BIBTEX_MAX_RETRIES + 1):
        time.sleep(0.3)
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": user_agent,
                "Accept": "application/x-bibtex;q=1.0",
            })
            with urllib.request.urlopen(req, timeout=BIBTEX_TIMEOUT, context=ctx) as resp:
                return resp.read().decode("utf-8")
        except Exception:
            if attempt < BIBTEX_MAX_RETRIES:
                time.sleep(2 ** attempt)
    return None