---
name: paper-prep
version: 1.2.0
description: "学术文献预处理流水线 — OCR → 标题提取 → CrossRef DOI 匹配 → 内容校验 → BibTeX 拉取 → 重命名。内置 DOI 反查、LLM+余弦双通道验证、bib/litrev 三方比对与 rename 前验证门禁,防止『内容与 DOI 错配』与『Crossref 未注册论文被误配 DOI』。当你需要批量处理 PDF 文献、获取 DOI 和 BibTeX 引用时使用。通过调用 doi_pipeline.py 脚本执行,不自己编排流程。位于 paper-tools/paper-prep,依赖 pdf-doi-toolkit、DeepSeek-OCR,可选 LLM(双通道验证)。"
metadata:
  requires: [python, uv]
---

# paper-prep — 学术文献预处理流水线

> **核心理念**: 你（AI）是调度器，不是执行者。所有批量逻辑都在 `scripts/doi_pipeline.py` 与 `scripts/paper_prep/` 包里，你只负责：把用户需求翻译成 CLI 命令 → 跑命令 → 读报告 → 解读结果。

> **位置**: 本 skill 位于 `paper-tools/paper-prep/`（与 `pdf-doi-toolkit`、`litrev-extract`、`DeepSeek-OCR` 同级），不属 skill-forge。所有论文工具集中在此。

## 前置条件检查（每次使用前 MUST）

1. **pdf-doi-toolkit 可 import**（自动定位：`scripts/paper_prep/__init__.py` 向上找 `pdf-doi-toolkit`）：
   ```bash
   uv run python -c "import pdf_doi_toolkit; print(pdf_doi_toolkit.__version__)"
   ```

2. **DeepSeek-OCR 可用**（仅 --step ocr 时需要）：
   ```bash
   ls <paper-tools>/DeepSeek-OCR/ocr_cli.py
   ```

3. **目标目录有 PDF**：`ls <article_dir>/*.pdf`

4. **junction 已建立**：本 skill 通过 junction 链接到 `~/.claude/skills/paper-prep`，指向 `paper-tools/paper-prep`。若 scripts/ 下文件不存在说明链接断了，重跑安装命令。

---

## 目录约定

```
<article_dir>/
├── *.pdf              # 输入 PDF
├── ocr_output/        # OCR 输出（脚本自动创建）
│   ├── {pdf_stem}/
│   │   └── {pdf_stem}.md
├── bib/               # BibTeX 输出
│   ├── {doi_safe}.bib
├── doi_map.json       # 匹配结果（机器可读,含 verified/canonical_title/title_sim 字段）
├── doi_match_report.md # 可读报告
├── verify_report.md    # 内容校验报告（verify 阶段产出）
└── pipeline_state.json # 阶段状态（断点续跑）
```

**skill 自身结构**（拆分后）：
```
paper-tools/paper-prep/
├── SKILL.md
└── scripts/
    ├── doi_pipeline.py       # 薄入口:argparse + 编排
    └── paper_prep/            # 包
        ├── __init__.py        # 路径解析 + Windows SSL
        ├── config.py          # 常量 + resolve_llm_config()
        ├── llm.ini            # LLM 默认配置(base/model/timeout,无密钥)
        ├── llm_verifier.py    # LLMVerifier(双通道)
        ├── title_extractor.py / verify.py / bibtex_fetcher.py / state.py
        └── steps/             # 每 step 一个模块
            ├── step_ocr.py / step_match.py / step_report.py
            ├── step_verify.py / step_bibtex.py / step_rename.py
```

---

## LLM 配置（双通道验证用）

双通道验证（`--llm-verify`）需要一个能用的 LLM 端点。配置按优先级链解析（高 → 低），密钥不落版本库：

| 优先级 | 来源 | 用途 |
|---|---|---|
| 1 | `--llm-model` CLI | 只覆盖 model |
| 2 | `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL_NAME` 环境变量 | 专名,与 litrev-extract 同名 |
| 3 | `scripts/paper_prep/llm.ini` | 默认 base/model/timeout(随版本库) |
| 4 | `OPENAI_API_KEY` / `OPENAI_BASE_URL` | 兼容兜底(仅非 Claude 会话) |
| 5 | 全无 | WARN 并禁用 LLM 通道,降级为仅余弦 |

**为何不用 `OPENAI_*`**: `.claude/settings.json` 全局注入 `OPENAI_API_KEY=PROXY_MANAGED` + 一个对 deepseek/glm 返回 403 的 proxy。在 Claude 会话里,子进程继承这两个值,任何依赖 `OPENAI_*` 的配置默认就连错端点,且静默降级不报错。`LLM_*` 名字干净、与 litrev 的 `LLM_API_KEY` 同名,正好对齐"同一接口"。

**最小可用配置**（只设一个变量即可，base/model 从 ini 取）：
```jsonc
// .claude/settings.json 的 env 块
"LLM_API_KEY": "sk-..."
```

ini 默认指向 litrev 用的本地代理 `http://localhost:37183/v1` + `deepseek-v4-flash`。要换端点/模型，改 `llm.ini` 或设 `LLM_BASE_URL`/`LLM_MODEL_NAME` 环境变量覆盖。

---

## 工作流（CLI 驱动）

### 单一命令

用户说"帮我处理这些 PDF"时：
```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step all
```

`all` 的阶段顺序为 `ocr → match → verify → report → bibtex → rename`。

### 分段执行

用户说"只跑 OCR" / "只校验" 等时：
```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step ocr|match|verify|report|bibtex|rename
```

### 带双通道验证的 match（推荐）

启用 LLM 对 OCR 标题与 Crossref 标题做语义比对（余弦相似度 + LLM 双通道，两者都否定才判 `no_confidence`）：

```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step match --llm-verify
```

约 100 tokens/次（输入两标题 + 1 token 输出 YES/NO），极轻量。配置见上面「LLM 配置」章节，最小只需在 settings.json 设 `LLM_API_KEY`。缺配置时自动降级为仅余弦验证,不报错。

### 带 bib 与 litrev 的三方校验（强烈推荐）

若用户已有 `references.bib`,跑 verify 时传 `--bib` 让 bib 标题参与比对；若已跑过 `litrev-extract`,把其 `derived/` 目录传给 `--verify-with-litrev` 复用已有 metadata JSON,无需额外 LLM 调用:
```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step verify \
  --bib <dir>/references.bib \
  --verify-with-litrev <dir>/litrev_project/output/derived
```

### 断点续跑

`pipeline_state.json` 记录了已完成阶段。脚本启动时会跳过已完成的 stage,只跑未完成的。如果用户要求重跑已完成的 stage,需要手动删除 `pipeline_state.json` 中对应条目。

### dry-run

重命名前预览:
```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step rename --dry-run
```

### --force 放行未验证条目

verify 阶段判定为 `mismatch`(内容与 DOI 不符)的条目在 rename 时会被门禁跳过。人工核对后确认无误,可用 `--force` 放行:
```bash
uv run python <skill>/scripts/doi_pipeline.py --article-dir <dir> --step rename --force
```

---

## 每阶段结果解读

命令跑完后,你(AI)负责做以下解读,不要只把输出丢给用户:

| 阶段 | 你要做的事 |
|------|-----------|
| match | 读 `doi_match_report.md`,汇报:匹配 X 篇,未匹配 Y 篇,疑似未注册论文 Z 篇。对未匹配的列出标题,问用户是否需要人工处理。**对 `no_confidence` 条目**（疑似博士论文/预印本,在 Crossref 上无独立 DOI）说明:该论文可能不在 Crossref 上,建议归档到独立目录或重下正确论文,不要用 `--force` 强行匹配。同时留意 `verified:false` 的条目(标题与 CrossRef canonical 不符),一并向用户说明 |
| verify | 读 `verify_report.md`,汇报:一致 X 篇,内容不符 Y 篇。**对内容不符项必须逐条列出**（OCR 标题 vs Crossref 期望标题 vs 相似度),告诉用户这通常是 PDF 下错/OCR 装错目录,需要重新下载正确论文或修正 OCR |
| bibtex | 汇报拉取成功/失败数。失败的 DOI 可让用户重新跑一次 bibtex stage |
| rename | **必须 dry-run 先预览**,列出将改名的文件让用户确认,确认后才执行。被门禁跳过的条目要向用户说明,建议先跑 verify 解决 |
| ocr | 汇报成功/失败数。失败的 PDF 可能是扫描件或加密,告诉用户 |

---

## 已知问题

| 问题 | 处理 |
|------|------|
| Windows OpenSSL | BibTeX 拉取已用局部 `_create_unverified_context()`,无需用户干预 |
| 已通过 pipeline_state.json 标记完成的 stage 重跑 | 手动删 state 文件或编辑 json |
| CrossRef 命中率 | 非英文论文/预印本可能匹配失败,属预期;报告会列出 |
| 重命名冲突 | 脚本跳过冲突项并记录,用户可手动处理 |
| **内容与 DOI 错配** | 文件夹名是正确 DOI,但 OCR md 装的是另一篇论文(如下错博士论文)。verify 阶段会标 `mismatch` 并在 rename 时门禁跳过。处理路径:人工核对 → 重下正确 PDF/重跑 OCR → 删 pipeline_state.json 的 verify 条目重跑 |
| **Crossref 未注册论文被误配 DOI** | 博士论文/预印本/技术报告在 Crossref 上无独立 DOI,`search_by_title` 会返回最相近的已注册论文(如 Mohammadian 博士论文→Rahman, Vasios 博士论文→Qi)。match 阶段双通道验证会判为 `no_confidence`(doi=None),不拉 bib 不 rename。处理:归档为 `@phdthesis` 或重下正确论文 |
| LLM 通道不可用 | `--llm-verify` 缺 `LLM_API_KEY` 时自动降级为仅余弦验证,不报错;启动时会打印 WARN |
| verify 误报 | OCR 标题启发式本身有噪声,相似度阈值默认 0.5 偏低以减少漏报。误报的条目人工确认后用 `--force` 放行 |
| bib month 字段小写警告 | IEEEtran.bst 不认 `sept`/`june`,改 `Sept`/`June` 消 warning(不影响 PDF) |

---

## 范围外(本 skill 不做)

- DeepSeek-OCR 并行化或并发调用(付费 API,settings.json 限制)
- venv 合并或依赖管理
- CrossRef 以外数据源(Semantic Scholar / OpenAlex)
- 一次性脚本版 `ocr_doi_matcher.py` 的后续维护
- LLM 并行化或并发调用(双通道验证为逐篇串行,单次极轻量)

---

## 校验机制说明(为何加这些)

**信任链方向(重要)**:本 skill 的输入是乱码命名的 PDF。OCR 内容是最强的可用真值,Crossref 是外部权威登记,`references.bib` 是**流水线的输出**,不是输入。校验的目的是验证「这个 DOI 是否真的是这篇 PDF」,而不是假设 DOI 对了反过来怪内容。

本 skill 的核心风险是**单点信任**:流水线只信 OCR 文本,一旦 PDF 下错或 OCR 装错目录,错误会一路传到 rename 被固化。1.2.0 的双通道验证 + 1.1.0 的校验体系,按成本从低到高依次:

1. **[match] DOI 反查 + 双通道验证**:`search_by_title` 得到 DOI 后,回查 CrossRef canonical 标题,同时跑余弦相似度与 LLM 语义比对两条独立通道。两通道都过 → verified;只有余弦过 → 需人工;双通道都否 → `no_confidence`(该论文可能不在 Crossref 上,博士论文/预印本)。命中:搜错 DOI(M4)、内容错导致标题不一致、Crossref 未注册论文被误配。
2. **[verify] 文件夹内容校验**:按文件夹名推断 DOI,反查 CrossRef 期望标题与 OCR 标题比对。命中"PDF/OCR 装错目录"。
3. **`--bib` 第三方真值**:references.bib 标题参与三方比对。命中"bib 登记错"也能被比出来。
4. **`--verify-with-litrev` 深度内容**:复用 litrev-extract 已产出的 metadata JSON(从正文提取的 citation.title),抓"标题相似但内容不同"的刁钻案例。

为什么用 LLM 双通道而不是只调阈值:余弦相似度只看词重叠,读不出语义。它有两个盲区——不同论文共享关键词(假阳性)、OCR 噪声导致低分(假阴性)。LLM 直接读两个标题做语义判断,恰好补上这两个盲区。两条通道同时否定才判 `no_confidence`,避免单通道误杀。

所有校验只标记不删除,`no_confidence` 条目不进 rename 不拉 bib,错误内容仍以原名存在,可人工处理,零破坏。

---

## 安装方式(管理端)

本 skill 位于 `paper-tools/paper-prep/`。junction 链接：

```bash
# 管理员终端
mklink /J %USERPROFILE%\.claude\skills\paper-prep C:\Users\VerNe\Downloads\Documents\paper-tools\paper-prep
```