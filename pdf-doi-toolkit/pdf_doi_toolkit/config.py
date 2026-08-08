"""
pdf-doi-toolkit 配置

所有可调参数集中在此，方便自定义。
"""

# CrossRef API 配置
DEFAULT_USER_AGENT = "PDF-DOI-Toolkit/1.0"
CROSSREF_BASE_URL = "https://api.crossref.org/works"
CROSSREF_MAX_RETRIES = 5
CROSSREF_CONCURRENCY = 6
CROSSREF_REQUEST_DELAY = 0.35  # 基本请求间隔（秒）
CROSSREF_TIMEOUT = 15          # HTTP 超时（秒）

# 扫描配置
SUMMARY_DIR_DEFAULT = None      # 由调用方指定
OUTPUT_DIR_DEFAULT = None

# 作者匹配阈值
AUTHOR_MATCH_CHAR_THRESHOLD = 0.7  # 姓氏字符相似度阈值

# 模糊文件名匹配
FUZZY_AUTO_RENAME_THRESHOLD = 70   # 评分 >= 70 → 自动重命名
FUZZY_REVIEW_THRESHOLD = 40        # 评分 >= 40 → 需人工确认; < 40 → 跳过
FUZZY_YEAR_MIN = 1900
FUZZY_YEAR_MAX = 2099
FUZZY_SCORE_AUTHOR = 50
FUZZY_SCORE_YEAR_EXACT = 30
FUZZY_SCORE_YEAR_NEAR = 20
FUZZY_SCORE_KEYWORD_MAX = 20
FUZZY_SEARCH_ROWS = 5

# OCR 文本提取
OCR_SUCCESS_THRESHOLD = 0.85          # 标题相似度 → auto-rename
OCR_MIN_TITLE_LENGTH = 15
OCR_DEFAULT_DIR = None                # 子类自动探测 DeepSeek-OCR 目录
OCR_TIMEOUT = 600                     # 单文件 OCR 超时（秒）

# 缓存
CACHE_DEFAULT_FILENAME = "pdf_doi_cache.json"

# 不太可能是作者姓氏的常见英文词
FUZZY_COMMON_WORDS = frozenset({
    "the", "and", "for", "with", "from", "study", "based", "analysis",
    "using", "method", "results", "research", "journal", "paper", "review",
    "new", "high", "effect", "role", "impact", "approach", "model", "system",
    "data", "application", "a", "an", "of", "in", "on", "by", "to", "is",
    "it", "as", "at", "was", "are", "be", "has", "have", "not", "but",
    "this", "that", "all", "each", "between", "during", "through", "under",
    "over", "before", "after", "above", "below", "out", "off", "up", "down",
    "about", "into", "than", "also", "very", "just", "more", "some", "any",
    "one", "two", "three", "first", "second", "last", "next", "other",
    "another", "such", "only", "own", "same", "so", "than", "too", "very",
    "well", "even", "still", "already", "however", "although", "because",
    "while", "since", "until", "once", "though", "if", "when", "where",
    "how", "what", "which", "who", "whom", "why",
    # 常见文件名但不是作者姓氏的词
    "manuscript", "thesis", "main", "draft", "final", "revised",
    "submitted", "accepted", "published", "preprint", "untitled",
    "document", "file", "figure", "supplement", "supporting",
})