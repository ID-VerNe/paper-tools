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