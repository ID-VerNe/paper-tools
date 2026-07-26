# Fix Task List — ✅ 全部完成

> 修复日期: 2026-07-02 | 涉及文件: 10 个 | 改动行数: +105/-27
> 测试验证: 全部通过

## 🔴 P0 — 必须立即修复

- [x] P0.1 — `aggregate.py:120` — prompt name 后缀匹配歧义 → 按 name 长度降序匹配
- [x] P0.2 — `models.py:119` — `api_base` 尾部斜杠 → 加 `.rstrip("/")`
- [x] P0.3 — `pipeline.py:376` — 绕过 `extract_json_block()` → 集成到 JSON 解析
- [x] P0.4 — `pipeline.py:370` — 非 retriable 错误被重试 → 引入 `NonRetriableError` + 区分确定性错误
- [x] P0.5 — `config.py:160` — inline prompt 崩溃 → 文件路径为空时提前返回 None

## 🟠 P1 — 尽快修复

- [x] P1.1 — `state.py:92` — state 损坏静默丢弃 → 加 warning + 备份 `.corrupt.timestamp` 文件
- [x] P1.2 — `registry.py:144` — sys.path 注入 → 改用 `importlib.util.spec_from_file_location`
- [x] P1.5 — `base.py:241` — `.pdf.txt` 被当 PlainText → Step 0 优先匹配复合扩展名
- [x] P1.4 — `pipeline.py:357` vs `config.py:237` — 模板路径 base_dir 改为 `.`（项目根目录）

## 🟡 P2 — 建议修复

- [x] P2.1 — `pipeline.py:456` — queue rotation off-by-one → `append` 改为 `appendleft`
- [x] P2.2 — `file_utils.py:134` — Windows fnmatch 不匹配反斜杠 → 匹配前 `\` 替换为 `/`
- [x] P2.6 — `file_utils.py:89` — input_dir 不存在时崩溃 → 加 `os.path.isdir` 前置检查 + warning
- [x] P2.7 — `templates.py:170` — `truncation: 0` 被解释为"不截断" → 改为 `is not None`

## 🔵 P3 — 低优先级

- [x] P3.1 — `models.py:99` — 文档从指数退火修正为线性退火文档
- [x] P3.2 — `pipeline.py:102` — `max_workers=0` 静默被替换 → 改为 `is not None` 判断
- [x] P3.8 — `run_cmd.py:222` — glob injection 在 --reset → 加 `re.escape(model_alias)`