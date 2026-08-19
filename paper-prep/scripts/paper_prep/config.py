"""全局常量与 LLM 配置解析。

集中在此,所有子模块 from paper_prep.config import ...,
避免常量散落各处导致改名时漏改。
"""

from __future__ import annotations

import configparser
import os
from pathlib import Path

# 标题提取:过滤掉这些前缀开头的行(不可能是论文标题)
FILTER_PREFIXES = (
    "abstract", "introduction", "received", "accepted", "published",
    "doi:", "doi ", "journal", "volume", "page", "copyright",
    "corresponding", "author", "keywords", "available", "chapter",
    "references", "acm", "ccs concepts", "additional key", "index terms",
)
MIN_TITLE_LEN = 15  # 标题最短长度

# BibTeX 拉取
BIBTEX_MAX_RETRIES = 3
BIBTEX_TIMEOUT = 15

# 双通道验证
VERIFY_TITLE_THRESHOLD = 0.5  # 标题相似度阈值:≥0.5 → verified; <0.5 → 需人工
LLM_TIMEOUT = 15              # LLM 判断单次调用超时(秒),被 ini 的 timeout 覆盖

# 标题相似度停用词(算 token 重叠时忽略这些词)
TITLE_STOPWORDS = {
    "a", "an", "the", "of", "and", "for", "in", "on", "with", "based",
    "using", "via", "toward", "towards", "article", "review", "paper",
    "research", "study", "analysis", "method", "approach", "system",
}

# ---------------------------------------------------------------------------
#  LLM 配置解析
# ---------------------------------------------------------------------------

# 本 ini 与本文件同目录,跟随版本库(无密钥)
_LLM_INI_PATH = Path(__file__).resolve().parent / "llm.ini"


def _load_ini_defaults() -> dict:
    """读 llm.ini 的默认值。ini 缺失时返回内建兜底。"""
    defaults = {
        "api_key_env": "LLM_API_KEY",
        "base_url": "http://localhost:37183/v1",
        "model": "deepseek-v4-flash",
        "timeout": "15",
    }
    if not _LLM_INI_PATH.exists():
        return defaults
    cp = configparser.ConfigParser()
    try:
        cp.read(_LLM_INI_PATH, encoding="utf-8")
    except Exception:
        return defaults
    if not cp.has_section("llm"):
        return defaults
    for k in defaults:
        if cp.has_option("llm", k):
            defaults[k] = cp.get("llm", k).strip()
    return defaults


def resolve_llm_config(cli_model: str | None = None) -> dict | None:
    """按优先级链解析 LLM 配置。

    返回 {"api_key","base_url","model","timeout"} 或 None(全无可用配置)。

    优先级(高 → 低):
      1. --llm-model CLI(只覆盖 model)
      2. LLM_API_KEY / LLM_BASE_URL / LLM_MODEL_NAME 环境变量(专名,避开 OPENAI_* 污染)
      3. llm.ini 默认值(base_url/model/timeout)
      4. OPENAI_API_KEY / OPENAI_BASE_URL 兼容兜底(仅非 Claude 会话、用户自设全局时)
    """
    ini = _load_ini_defaults()

    # model: CLI > LLM_MODEL_NAME > ini
    model = cli_model or os.environ.get("LLM_MODEL_NAME", "") or ini["model"]

    # base_url: LLM_BASE_URL > ini > OPENAI_BASE_URL(兜底)
    base_url = os.environ.get("LLM_BASE_URL", "") or ini["base_url"] or os.environ.get("OPENAI_BASE_URL", "")

    # api_key: 专名优先(LM_API_KEY 由 ini 指定,默认 LLM_API_KEY),
    # 再兜底 OPENAI_API_KEY(但 PROXY_MANAGED 占位要忽略)
    api_key = os.environ.get(ini.get("api_key_env") or "LLM_API_KEY", "")
    if api_key in ("", "PROXY_MANAGED"):
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if api_key == "PROXY_MANAGED":
            api_key = ""

    try:
        timeout = int(ini.get("timeout", "15"))
    except ValueError:
        timeout = 15

    if not api_key or not base_url or not model:
        return None
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "timeout": timeout,
    }
