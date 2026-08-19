"""paper-prep 流水线包。

导入本包即完成两件事:
  1. 把 pdf-doi-toolkit 加入 sys.path(自动定位,不硬编码绝对路径)
  2. Windows OpenSSL 兼容(同旧版单文件行为)
"""

from __future__ import annotations

import ssl
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
#  路径解析:找到 pdf-doi-toolkit 并加入 import path
#  paper-prep 现位于 paper-tools/paper-prep/,与 pdf-doi-toolkit 同级,
#  向上走最多 3 层即可命中 paper-tools/pdf-doi-toolkit
# ---------------------------------------------------------------------------
def _resolve_toolkit_path() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "pdf-doi-toolkit"
        if candidate.is_dir():
            return candidate
    # 回退:用户标准安装位置
    return here.parents[3] / "pdf-doi-toolkit"

_TOOLKIT_PATH = _resolve_toolkit_path()
if str(_TOOLKIT_PATH) not in sys.path:
    sys.path.insert(0, str(_TOOLKIT_PATH))

# ---------------------------------------------------------------------------
#  Windows OpenSSL 兼容(必须在 urllib 被使用前执行)
# ---------------------------------------------------------------------------
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context
