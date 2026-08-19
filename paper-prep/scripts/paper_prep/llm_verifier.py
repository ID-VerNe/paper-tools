"""LLM 双通道验证器。

用 LLM 判断「OCR 提取标题」与「Crossref canonical 标题」是否指同一篇论文。
这是唯一能抓住「标题相似但内容不同」的手段——余弦相似度只数词重叠,
读不出语义;LLM 直接读标题就能判断。

配置走 resolve_llm_config 的优先级链(见 config.py):
  --llm-model CLI > LLM_* 环境变量 > llm.ini 默认 > OPENAI_* 兜底 > 降级
与 litrev-extract 共用同一接口(gateway: localhost:37183, 变量名 LLM_API_KEY)。
"""

from __future__ import annotations

from paper_prep.config import resolve_llm_config

_SYSTEM_PROMPT = (
    "You are a research librarian. Determine whether two paper titles "
    "refer to the same academic paper. Reply with a single word: YES or NO."
)
_USER_TEMPLATE = (
    "Title A: {a}\n\nTitle B: {b}\n\nAre these the same paper? Reply YES or NO."
)
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


class LLMVerifier:
    """binary 判断(YES/NO),输入为两个标题,输出单个词。

    用法:
        v = LLMVerifier()        # 按配置链解析,key 缺失时抛 ValueError
        v.verify(title_a, title_b)  # → True / False / None
        v.close()
    """

    def __init__(self, model_name: str | None = None):
        cfg = resolve_llm_config(cli_model=model_name)
        if cfg is None:
            raise ValueError(
                "LLM 验证无可用配置。设 LLM_API_KEY,或参考 scripts/paper_prep/llm.ini"
            )
        self.api_key = cfg["api_key"]
        self.api_base = cfg["base_url"]
        self.model_name = cfg["model"]
        self._timeout = cfg["timeout"]
        self._client = None

    @property
    def client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                api_key=self.api_key,
                base_url=self.api_base,
                default_headers={"User-Agent": _BROWSER_UA},
                timeout=self._timeout,
            )
        return self._client

    def verify(self, ocr_title: str, canonical_title: str):
        """返回 True(同论文)/ False(不同)/ None(调用失败,需降级)。"""
        if not ocr_title or not canonical_title:
            return None
        user = _USER_TEMPLATE.format(a=ocr_title, b=canonical_title)
        try:
            resp = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
            )
            ans = (resp.choices[0].message.content or "").strip().upper()
            if ans.startswith("YES"):
                return True
            if ans.startswith("NO"):
                return False
            return None
        except Exception:
            return None

    def close(self):
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass