# litrev-extract 完整代码审计报告

> **审计日期**: 2026-07-02
> **审计范围**: 27 个 Python 源文件 (5255 行)
> **审计方法**: 9 路 Agent 分布式审计 + 手动代码审查
> **覆盖维度**: 并发安全、数据流、错误处理、JSON 解析、文件系统、安全、配置/CLI、后处理、类型/API

---

## 严重度分级说明

| 级别 | 含义 |
|------|------|
| 🔴 P0 | 立即修复 — 数据错位、API 全部失败、配置级崩溃 |
| 🟠 P1 | 尽快修复 — 数据丢失、安全风险、功能异常 |
| 🟡 P2 | 建议修复 — 平台兼容、逻辑缺陷 |
| 🔵 P3 | 低优先级 — 代码异味、文档不一致 |

---

## 🔴 P0 — 必须立即修复

### P0.1 Prompt Name 后缀匹配歧义 → 数据归入错误分类

**严重度**: 严重 — 静默产生错误数据

- **文件**: `src/litrev_extract/postproc/aggregate.py:120-124`
- **来源**: dataflow-audit (HIGH), 手动审计

**现象**:
prompt name 集合 (`prompts = {p.name for p in config.prompts}`) 是无序的 `set`。文件名解析使用 `stem.endswith(f"_{pname}_{model_alias}")` 做匹配。如果存在两个 prompt name 互为后缀（例如 `"evolution"` 和 `"timeline_evolution"`），短 name 会提前匹配并 `break`，导致：

1. 文件被归入错误的 prompt
2. `paper_id` 被截断：`stem[: -len("_evolution_gpt4")]` 得到 `"PMC123456_timeline"` 而非正确的 `"PMC123456"`
3. 后续 CSV enrich 因 paper_id 不匹配而全部失效

**修复方案**: 将 `prompts` 按 name 长度降序排序后再迭代，长 name 优先匹配：

```python
for pname in sorted(prompts, key=len, reverse=True):
    if stem.endswith(f"_{pname}_{model_alias}"):
        matched_prompt = pname
        break
```

---

### P0.2 `api_base` 尾部斜杠导致 API 路由 404

**严重度**: 严重 — 所有 API 请求全部失败

- **文件**: `src/litrev_extract/models.py:119-120`
- **来源**: config-cli-audit (MEDIUM-HIGH), filesystem-audit (H4)

**现象**:
```python
return self.base_url.removesuffix("/chat/completions")
```
`base_url` 如 `"https://api.example.com/v1/chat/completions/"`（尾部有 `/`），`removesuffix` 不会匹配（缺少尾部斜杠的处理）。OpenAI SDK 会追加 `/chat/completions`，结果是：
```
https://api.example.com/v1/chat/completions//chat/completions
```
→ HTTP 404

**修复方案**: 先 strip 尾部斜杠再移除 suffix：
```python
return self.base_url.rstrip("/").removesuffix("/chat/completions")
```

---

### P0.3 Pipeline 绕过 `extract_json_block()` 多策略 JSON 提取

**严重度**: 严重 — LLM 非纯 JSON 输出全部触发重试

- **文件**: `src/litrev_extract/pipeline.py:376-385`
- **来源**: json-parsing-audit (HIGH)

**现象**:
`_process_task()` 直接调用 `clean_json_string(response_str)` → `json.loads(cleaned)`，从未使用 `extract_json_block()`。后者的三大策略（清理 fence → 括号匹配深度 → 宽松回退）全部被闲置。

如果 LLM 返回 `Here is the result:\n```json\n{"key": "val"}\n```\nI hope this helps!`，`clean_json_string` 无法清理（第一行不是 ` ``` `），`json.loads` 直接异常 → 进入 10 次重试循环。

**修复方案**: 将 `extract_json_block` 的调用集成到 JSON 解析步骤中：

```python
try:
    cleaned = extract_json_block(response_str)
    data = json.loads(cleaned)
    if not validate_json_schema(data):
        ...
```

---

### P0.4 非 Retriable 错误被循环重试 10 次

**严重度**: 高 — 浪费 API 调用和时间

- **文件**: `src/litrev_extract/pipeline.py:370-374, 304-319`
- **来源**: dataflow-audit (MEDIUM), 手动审计

**现象**:
`_process_task()` 中的 LLM 调用捕获异常后调用 `is_retriable(e)`。对于非 retriable 错误（HTTP 401/403/400, context-length exceeded），返回 `False`。但 `_safe_process` 收到 `False` 后统一交给 `_handle_failure`，后者递增重试计数器、sleep、重新入队。

注释写着 `return False  # Permanent failure — do not retry`，但控制流仍然执行重试逻辑，直到 `retry_count >= max_retries` 才终止。

**修复方案**: 引入 `NonRetriableError` 异常类，`_safe_process` 捕获后直接标记 `mark_failed` 并返回 `True`（terminal），不进入重试路径。

---

### P0.5 Inline-only Prompt 配置导致崩溃

**严重度**: 高 — 配置级崩溃

- **文件**: `src/litrev_extract/config.py:160-181, 346-349`
- **来源**: config-cli-audit (Issue 1, MEDIUM)

**现象**:
当 prompt 只有 `user_template` 内联内容、没有 `file` 字段时，YAML 解析代码：
```python
prompt_file = p.get("file", "")       # → ""
template_content = _load_prompt_file(prompt_file, base_dir)
```
`_load_prompt_file("", base_dir)` 内 `Path("")` 解析为当前目录（因为 `""` 被视为相对路径），非绝对路径就拼接到 `base_dir` 上，得到 `base_dir/`（一个存在的目录）。`path.exists()` 为 True，然后 `path.read_text()` 对目录调用 → 抛出 `IsADirectoryError`。

**修复方案**: `_load_prompt_file` 入口处判空返回：

```python
def _load_prompt_file(file_path: str, base_dir: str) -> Optional[str]:
    if not file_path or not file_path.strip():
        return None
    ...
```

---

## 🟠 P1 — 尽快修复

### P1.1 State 文件损坏静默丢弃全部进度

- **文件**: `src/litrev_extract/state.py:92-94`
- **来源**: 手动审计

**现象**:
```python
except (json.JSONDecodeError, OSError):
    self._state = {}
```
如果 `.litrev_state.json` 因写入中断而损坏，系统静默清空状态。已完成的所有任务被当作"未完成"重新处理。无告警、无备份。

**修复**: 捕获异常时打印 warning，留备份文件 `.litrev_state.json.corrupt`。

---

### P1.2 `sys.path` 注入 → Python stdlib 劫持风险

- **文件**: `src/litrev_extract/postproc/registry.py:144-148`
- **来源**: security-audit (Finding 1, MEDIUM)

**现象**:
`import_user_module()` 的 strategy 2（文件路径）把模块父目录插入 `sys.path[0]`，然后用 basename 导入。如果该目录下有 `json.py`、`os.py`、`pathlib.py` 等与 stdlib 同名的文件 → 后续所有对应模块的 import 都加载恶意文件。

**修复**: 改用 `importlib.util.spec_from_file_location()` 直接根据文件路径加载模块，绕过 `sys.path`。

---

### P1.3 `validate_json_schema()` 无字段级校验 → 垃圾数据保存

- **文件**: `src/litrev_extract/utils/json_utils.py:50-74`
- **来源**: json-parsing-audit (HIGH)

**现象**:
```python
def validate_json_schema(data):
    if not isinstance(data, dict): return False
    if len(data) == 0: return False
    return True
```
任何非空 dict 通过校验，包括 `{"error": "I cannot process this"}` 或 `{"foo": "bar"}`。垃圾响应被保存为结果文件，下游统计、报表全部污染。

**修复**: 允许配置字段级 schema（可选，不做强制），至少记录 warning 级别的质量提示。

---

### P1.4 模板文件路径 `base_dir` 不一致

- **文件**: `src/litrev_extract/pipeline.py:357` vs `src/litrev_extract/config.py:237`
- **来源**: config-cli-audit (Issue 2/3, LOW)

**现象**:
- Config loader 解析 prompt file 路径时：`base_dir = str(yaml_path.parent)`（YAML 文件所在目录）
- Pipeline 中：`self.template_manager.load(task.prompt_def, base_dir=self.config.input_dir)`

两者不同时模板文件找不到。目前因为 `PromptDef.user_template` 已在 config 加载时预填充才没崩。

**修复**: pipeline 传递项目根目录（而非 input_dir）作为 base_dir。

---

### P1.5 `.pdf.txt` 文件被错误识别为 PlainText

- **文件**: `src/litrev_extract/readers/base.py:241`
- **来源**: filesystem-audit (M2), 手动审计

**现象**:
`os.path.splitext("foo.pdf.txt")` → `(".pdf", ".txt")` → ext = `".txt"` → step 1 直接匹配 `PlainTextReader`。`PdfTextReader` 注册了 `.pdf.txt` 但永远不会命中。

**修复**: 在 `get_reader()` 中先检查多段复合扩展名，再回退单段匹配。

---

## 🟡 P2 — 建议修复

### P2.1 Queue Rotation 重插入位置 off-by-one

- **文件**: `src/litrev_extract/pipeline.py:456-459`
- **来源**: dataflow-audit (MEDIUM)

**现象**:
`rotate(-2) + append(T) + rotate(2)` 预期把 T 插入 index 2，实际落在 index 1。

**修复**: 使用 `self.queue.appendleft(task)` 替代中间的 `append`：

```python
self.queue.rotate(-insert_pos)
self.queue.appendleft(task)
self.queue.rotate(insert_pos)
```

---

### P2.2 Windows `fnmatch` 不匹配反斜杠路径 → 排除规则失效

- **文件**: `src/litrev_extract/utils/file_utils.py:134`
- **来源**: filesystem-audit (H2)

**现象**:
`os.walk` 在 Windows 上返回 `root\dir\file`（反斜杠）。`fnmatch.fnmatch` 的 Unix 风格模式 `*/archived/*` 不匹配反斜杠路径 → 整个排除机制在 Windows 上失效。

**修复**: 调用 fnmatch 前将路径中的 `\` 替换为 `/`。

---

### P2.3 `os.replace` 在 Windows 上非原子

- **文件**: `src/litrev_extract/pipeline.py:399`, `state.py:114`
- **来源**: filesystem-audit (H3)

**现象**:
Windows 的 `rename` 在目标文件被其他进程打开时抛出 `PermissionError`。影响 pipeline 结果写入和 state flush。

**修复**: 捕获 `PermissionError` 回退到 `shutil.move` 或重试（带指数退避）。

---

### P2.4 跨进程 State 文件覆写

- **文件**: `src/litrev_extract/state.py:111-114`
- **来源**: filesystem-audit (H5)

**现象**:
两个进程使用同一 state 文件时，都写入 `.tmp` 再 `os.replace` → 后写入者覆盖前者更新，中间状态丢失。

**修复**: 引入进程锁文件 (lockfile) 或使用文件锁 (`msvc.locking` on Windows, `fcntl.flock` on Unix)。

---

### P2.5 LLM Response 空内容被静默重试

- **文件**: `src/litrev_extract/llm_handler.py:289`
- **来源**: 手动审计

**现象**:
`response.choices[0].message.content or ""` — 如果 LLM 因 `finish_reason="length"`（token 耗尽）返回 `content=None`，转换为空字符串。下游 `json.loads("")` 失败 → 重试循环，浪费 API 费用。

**修复**: 检查 `finish_reason`，对 `"length"` 返回特定错误以减少不必要的重试。

---

### P2.6 `input_dir` 不存在时崩溃

- **文件**: `src/litrev_extract/utils/file_utils.py:89, 105`
- **来源**: filesystem-audit (H1)

**现象**:
`os.walk(input_dir)` 或 `os.listdir(input_dir)` 无存在性检查。输入目录不存在时抛出 `FileNotFoundError`。

**修复**: 顶部加 `if not os.path.isdir(input_dir): return []` 和 warning 日志。

---

### P2.7 `truncation: 0` 被解释为"不截断"

- **文件**: `src/litrev_extract/templates.py:170`
- **来源**: 手动审计

**现象**:
```python
if truncation and len(text) > truncation:
    text = text[:truncation]
```
`truncation = 0` 是 falsy，条件为 False → 全文输入。

**修复**: 改为 `if truncation is not None and ...`。

---

## 🔵 P3 — 低优先级

### P3.1 文档声称指数退火，代码实现线性退火

- **文件**: `src/litrev_extract/models.py:99` (文档) vs `pipeline.py:432` (代码)
- **来源**: config-cli-audit (Issue 6)

**现象**:
- 文档: `delay = retry_delay_base * (2 ** attempt)`
- 代码: `delay = min(task.retry_count * self.model_config.retry_delay_base, 60)`

**修复**: 更新文档匹配代码，或增加 60s ceiling 的文档说明。

---

### P3.2 `max_workers=0` 静默被替换为默认值

- **文件**: `src/litrev_extract/pipeline.py:102`
- **来源**: dataflow-audit (LOW)

**现象**:
```python
self.max_workers = max_workers or self.model_config.max_concurrent
```
`0 or 3` → 3。用户期望 0 表示"不并行"时被悄悄替掉。

**修复**: 改为 `self.max_workers = max_workers if max_workers is not None else self.model_config.max_concurrent`。

---

### P3.3 Env var 只支持完整值匹配

- **文件**: `src/litrev_extract/config.py:110-113`
- **来源**: 手动审计

**现象**:
`_resolve_env_var("${VAR}")` 只在整个字符串就是 `${VAR}` 时生效。`"prefix_${VAR}"` 或 `${A}/${B}` 不会被解析。

---

### P3.4 Dry-run 统计不扣减已完成任务

- **文件**: `src/litrev_extract/cli/run_cmd.py:255`
- **来源**: config-cli-audit (Issue 4, LOW)

**现象**:
`_dry_run()` 打印 `len(documents) * len(config.prompts)`，但实际 pipeline 会 dedup + 跳过已完成任务。部分运行后 dry-run 数字偏大。

---

### P3.5 不必要的锁范围 (pbar desc 更新)

- **文件**: `src/litrev_extract/pipeline.py:265-268`
- **来源**: 手动审计

**现象**: 在保护 queue/stats 的 `self._lock` 内更新 tqdm UI 描述，不相关。

---

### P3.6 ReaderFactory 扩展名冲突无告警

- **文件**: `src/litrev_extract/readers/base.py:216-217`
- **来源**: json-parsing-audit (LOW)

**现象**: `self._readers[ext] = reader` 是简单 dict 赋值，两个 reader 声明同一扩展名时后一个静默覆盖前一个。

---

### P3.7 scripts 目录错误文件崩溃整个 postproc

- **文件**: `src/litrev_extract/postproc/registry.py:180-183`
- **来源**: filesystem-audit (M3)

**现象**: `import_user_module` 异常未被捕获 → `.py` 文件语法错误或缺少依赖时整个后处理管线崩溃。

---

### P3.8 Glob injection 在 `--reset` 文件删除

- **文件**: `src/litrev_extract/cli/run_cmd.py:220-224`
- **来源**: security-audit (Finding 4, LOW), filesystem-audit (M1)

**现象**: `f"*_{model_alias}.json"`，`model_alias='*'` 删除所有模型的所有结果文件。

---

## Agent 结果状态

| Agent | 方向 | 状态 | 说明 |
|-------|------|------|------|
| concurrency-audit | 并发/锁安全 | ❌ 无输出 | 持续 idle 不返回，手动代码已覆盖 |
| dataflow-audit | 数据流/状态 | ✅ 已返回 | 1 HIGH, 2 MEDIUM, 2 LOW |
| error-handling-audit | 错误处理 | ❌ 无输出 | 持续 idle 不返回，手动代码已覆盖 |
| json-parsing-audit | JSON 解析 | ✅ 已返回 | 2 HIGH, 2 MEDIUM, 4 LOW |
| filesystem-audit | 文件系统/OS | ✅ 已返回 | 5 HIGH, 4 MEDIUM, 2 LOW |
| security-audit | 安全/注入 | ✅ 已返回 | 1 MEDIUM, 1 LOW-MEDIUM, 3 LOW |
| config-cli-audit | 配置/CLI | ✅ 已返回 | 1 MEDIUM-HIGH, 1 MEDIUM, 5 LOW |
| postproc-audit | 后处理逻辑 | ❌ 无输出 | 持续 idle 不返回，手动代码已覆盖关键点 |
| stats-pbar-audit | 统计/pbar | ❌ 无输出 | 持续 idle 不返回，手动代码已覆盖 |
| 手动审计 | 全量代码阅读 | ✅ 已完成 | 覆盖全部 27 个源文件 |

---

## 建议修复顺序

```
P0.1  aggregate name 匹配 → 10行  ← 最危险：静默数据错误
P0.2  base_url 尾部斜杠   → 1行   ← 最影响面：全部 API 404
P0.3  bypass extract_json_block → 5行 ← LLM 非纯JSON全挂
P0.4  非 retriable 错误重试 → 15行 ← 浪费 API 调用
P0.5  inline prompt 崩溃  → 3行   ← 配置级崩溃
────
P1.1  state 损坏静默丢弃  → 20行  ← 大量数据重处理
P1.2  sys.path 注入       → 10行  ← 安全
P1.3  JSON 无字段校验     → 设计决策 ← 数据质量
P1.4  模板路径不一致      → 5行   ← 取决于使用场景
P1.5  .pdf.txt 识别错误   → 8行   ← 特定场景
```

---

*报告生成于 2026-07-02，基于 9 路 Agent 分布式审计 + 手动 27 文件代码审查*