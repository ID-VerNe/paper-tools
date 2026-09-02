# 知识层总规划：Agent-Queryable Knowledge Layer over paper-tools

**起草日期**: 2026-08-30
**维护者**: 用户（博士生，paper-tools 工具链维护者）+ Claude Code
**状态**: Phase 0 / 0.5 / 1.0 / 1 / 2 全部完成（2026-08-30）；Phase 3 尚未开始
**关联文件**:
- 两轮 multi-lens-research 产物：`outputs/direction-judge/`（第一轮：是否引入 LLM Wiki）、`outputs/tech-architecture/`（第二轮：存储架构借鉴）
- 实验产物：`phase0-lab/PHASE0_REPORT.md`、`phase0-lab/PHASE05_REPORT.md`、`phase0-lab/PHASE2_REPORT.md`
- 测试床：`C:\Onedrive\VerNe\HKU\PHD申请\RMIT_Shuo_Yan\Research_Proposal\article`（语料 A，34 篇）

---

## 0. 一句话目标（来自用户的原始需求）

给 AI agent 看的知识库，不是给人看的。agent 能快速回答：
> "P2P trading 的瓶颈是各个 agent 需要顺序更新，导致网络环境每次更新都不一样"——这是哪篇文献、谁说的、哪一段。

引用粒度：段落/小节级即可（用户确认）。

## 1. 背景与约束（已确认事实）

| 项 | 值 | 来源 |
|---|---|---|
| 用户身份 | PhD 学生，做系统性文献综述 | 第一轮 multi-lens 用户澄清 |
| 语料规模 | ~10,000 篇/年；一个项目上千篇；一年 10-20 个项目 | 用户澄清 |
| 真实瓶颈 | 提取出的数据没有好的"归档""储存"方式 | 用户澄清 |
| 知识库消费者 | AI agent（非主要给人看） | 用户澄清 |
| 引用粒度 | 段落/小节级 | 用户澄清 |
| LLM 配额 | 无限量供应 | 用户多次声明 |
| 现有工具链 | paper-prep（v1.2.0，OCR→DOI→BibTeX→重命名）、litrev-extract（SLR 提取）、DeepSeek-OCR、pdf-doi-toolkit、scihub、tex2txt | 仓库目录 |
| 工具链边界 | paper-prep 在"import readiness"停止 | 第一轮 synthesis |
| 测试床语料 A | 34 篇 PDF，272 个 derived JSON（34×4 prompt×2 model），doi_map 34 条（32 matched + 2 no_confidence） | peer-review §0 实地核验 + phase0-lab 实测 |

## 2. 两轮 multi-lens-research 的结论

### 第一轮（direction-judge）：是否引入 LLM Wiki

**结论：现在不引入 LLM Wiki。** 不是拒绝，是推迟。

- 五个视角（optimist / pessimist / publication-strategist / resource-realist / pattern-matcher）一致认为 litrev-extract 是不可替代的主力和可引用的方法；知识图补充而非替代。
- 真正的约束是**人的注意力/维护成本 + 安装可靠性**，不是钱或算力。"无限量 LLM 预算"是真实的，但与瓶颈无关——瓶颈是有限的人类注意力，第二个知识系统会乘以而非节省它。
- LLM Wiki 的真实成本是 install + 异国栈在抖动网络上，且当前是**未构建的源码检出**（无 node_modules、无 src-tauri/target、需 Rust 1.88+/protoc）。
- 同行评审 B+：最弱链接是 F5（发表策略的"饱和"前提未经核验），以及承重假设"用户是否真会在写作时查询这个图"从未被测试。

### 第二轮（tech-architecture）：如何借鉴 llm_wiki 的存储架构

**结论：批准构建一个概念级、agent 可查询的知识层，叠在现有 paper-prep + litrev-extract 之上。** 四阶段、证据优先。

四个视角（systems-architect / scalability-engineer / migration-specialist / tech-debt-analyst）的关键分歧与共识：

**五个关键发现（第二轮 synthesis）：**
- **F1**（信度 8→peer 7）：不要重复提取；从已有 `derived/*.json` 派生，不让 LLM 读第二遍全文。但承重机制"引文→锚点映射是纯函数"**未经验证**——这是 peer review 标记的最弱链接。
- **F2**（8→7）：SQLite/FTS5 是查询底座，markdown 是归档层。但**已交付的 FTS5 schema 对 2 字符 CJK 查询返回 0 行**（peer review 实测：`MATCH '交易'` 在 trigram 和 unicode61 两张表都返回 0）——用户旗舰示例"P2P交易瓶颈"会查空。**这是 config 级 bug，必须修。**
- **F3**（9→9，最高）：三层身份映射，DOI-when-verified，明确的 unresolved 列表，冲突时两记录都保留。全部由 migration-specialist 在真实磁盘数据上核验（34/32/2 拆分、`.fmatter` 陷阱、语料 B 四类身份族）。
- **F4**（7→8，上修）：re-OCR 文本不稳是决定锚点方案的唯一未知数。所有视角都围绕它设计了不同方案（全文内容哈希 / 前 64 字符指纹 / `##`/`###` 章节句柄），**但没人做过测量**。一个 10 分钟实验能定。这是 Phase 0 的理由。
- **F5**（8→8）："谁说的"≠署名作者；归属是查询时的事。引文里的指代（"X (2019) showed that..."）才是真正 claim 持有者，署名只是载体。

**第二轮 peer review 的七个计划缺口（必须逐一回应）：**
1. 时间估计乐观 2x；Phase 1 "约 2 天"是红旗，实际 3-4 天。
2. **MS 的 3 篇 seed 阶段和 contradiction-map 的 Resolution Path 第 2 项被丢了，没并入计划。** 这是最高价值的计划步骤——在写 plumbing 前验证引文→锚点映射。
3. OCR 实验输入欠规约（必须同参数跑；语料 A 可能无 OCR md，全是首代——peer review 实测确认语料 A 是 34 PDF + 2 md，无成对 OCR）。
4. 决策表缺混合列和"段落跨节搬迁"行。
5. "SQLite 胜出"在实质上正确，但链路 custody 有瑕疵（3 视角的 contradiction-map 被当 4 视角证据用）。
6. Phase 0 决策表 note(2) 的 existence gate 与"重建策略"是两个不同对象——没人算过每次重锚死多少 claim。
7. 失败处理缺失或太晚（seed 失败分支、归属失败模式、agent 查询返回空路径都没定义）。

**第二轮 peer review 的最关键指出（已被 Phase 0.5 验证）：**
> "最不自信的断言是 F1 的承重机制：'引文→锚点映射是纯函数'——假设 litrev 的现有 derived JSON 引文能确定性重锚到 OCR 段落。它最便宜可测，也最承重。计划把它排在它的使用之后。"

以及：
> "TD 的第一条条件'**提取时必须产出锚点**（支持性引文 + 章节）'在计划里缺席。计划假设现有 derived JSON 里有可锚定引文；TD 明确说现有字段不保证带可定位引文——这是唯一需要新付的 LLM 可见成本。"

**这两点在 Phase 0.5 被实验直接命中，见下文 §4。**

## 3. Phase 0 实验结果（已完成，2026-08-30）

**实验**：同一篇 PDF（`10.1109_tii.2020.2999571`，11 页 IEEE TII）用 DeepSeek-OCR 跑两次，三层 diff。

| 层级 | 结果 |
|---|---|
| 章节结构（`##`/`###` 序列） | 14/14 完全一致 |
| 行指纹（前 64 字符对齐） | 90.7%（127/140） |
| 全行内容地址（sha1-8 集合） | **94.3%**（133/141） |
| 真实内容漂移 | **<5%，全部 ratio ≥0.95，零段低于 0.95** |

漂移全在非正文元素（LaTeX 宏名、表格 colspan、OCR 词边界 `FDIAIS`/`FDIA's`、标点空格、作者简介区图文相对顺序重排）。**正文段落（承载 claim 的部分）近 100% 稳定。**

**命中第二轮 synthesis 的决策表第 1 行** → 锚点方案锁定：
1. **主锚点**：全段落内容哈希（NFKC 归一化 + 空白折叠后 sha1-8），对 ~94% 行永久稳定
2. **回退**：模糊重匹配（difflib ratio ≥0.9），对 ~5% 漂移行确定性重绑，无 LLM
3. **父上下文**：章节路径（`##`/`###` 标题链，实测 100% 稳定）随每个锚点存储，作辅助上下文轴而非锚点本身

**验证了用户的判断**："现在的 OCR 准确性是很高的了"——被两次独立实验确认（Phase 0 段落漂移 <5%，Phase 0.5 正控制组 100%）。

## 4. Phase 0.5 实验结果（已完成，2026-08-30）

**实验**：在 8 个 derived JSON（同一篇论文，4 prompt × 2 model，46 个字符串字段）上测试承重假设"从逐字引文到 OCR 段落的确定性映射"。四组子测试：

| 子测试 | 命中率 | 说明 |
|---|---|---|
| A. 正控制组（OCR 原文截断 14 词） | **100%** | 证明匹配器 + OCR 无 bug |
| B. 当前提示词内联引文（`'...'` span） | 66.7% | 只 3 个字段有引文 |
| C. 专用 `evidence_quote` 字段（按 `...` 切片） | **91.7%** | 独立字段比内联引文可靠 |
| D. 纠正后提示词（模拟 12-18 词逐字片段） | **100%** | 改 prompt 后回到 100% |

**决定性数字：46 个字符串字段里 44 个根本不含逐字引文**（`no_quote`）。只有 `cyber_attacks.attack_target`（2 条内联引文，都命中）和 `physics_defense.evidence_quote_for_physics_use`（专用字段，切片后 11/12 命中）产出了可锚定文本。其余 44 个字段全是 LLM 转述或结构化分类。

**两个结论：**

1. **承重假设在当前提示词下不成立**——直接 ingest 当前 derived JSON，95.6% 字段无法回答"哪一段"，会得到一个 `quote` 列大半为空的锚点表。peer review 标记的"最弱链接"和 TD 的 #1 条件"提取时必须产出锚点"被实验直接命中。**综合计划删掉 TD 的 #1 条件是错的。**

2. **修复极便宜**——子测试 D 证明，只要 prompt 要求产出 ≤18 词逐字片段，命中率立刻回到 100%。这是**提示词层修改**，不增加 LLM 调用次数（只给现有提取加一个约束字段），零额外成本。正确形态不是"在转述里夹引号"（子测试 B，命中率低且易残缺），而是"为每个 claim 组加独立逐字证据字段"（子测试 C，`litrev-extract` 已在 `physics_defense` 里这么做了，只需推广到其余 3 个 prompt）。

**唯一的 unmatched**（子测试 B 第 3 条，ratio 0.31）是 gemini 在 `defense_mechanism_description` 转述里夹了残缺引文"s laws and AC power equations"——LLM 截断/改写，非 OCR 问题。这印证：内联引文不可靠，独立字段才可靠。

## 4.5. 已完成阶段记录（Phase 1.0 / 1 / 2，2026-08-30）

**Phase 1.0 — 提示词改造 + 回填验证（已收尾）**
- 改 4 个 prompt 模板：为每个 claim 组加独立 `evidence_quote` 字段（≤18 词逐字片段，可 `...` 连接，禁改写，无原文填 `null`）。实测命中率回到 ≥90%（从 Phase 0.5 子测试 B 的 66.7% 提到真实提取 ~100%）。
- 用户验收：进 Phase 1。

**Phase 1 — know 包 MVP + 验证（已收尾）**
- `paper-tools/know/` 包落地：`know.py`（CLI：ingest/validate/search/flag）+ `anchors.py`（确定性分段 + sha1-8 内容寻址锚点）+ `derive.py`（扫 derived_v2 JSON → claims 行 → 锚点，零 LLM）+ `config.py`（schema）。
- `papers` 表加 `doi` / `identity_status` 列（`insert_paper`）；`claims` 表含 `anchor` / `heading_path` / `model` / `prompt_id` / `conflicting` / `rank`。
- 三层身份映射（MS §2.3）落地：`identity.py`（`load_doi_map` / `doi_status` / `resolve_doi` / `build_identity`）。
- 物理布局：`know/` 包 + `knowledge-base/` git 仓库（schema.md / purpose.md / log.md / papers/ / concepts/ / catalog.md / index.sqlite 待 Phase 3）。
- 测试：**51/51 tests**（test_anchors / test_derive / test_identity / test_know_cli），变异测试 **4/5 mutants 被 kill**（1 equivalent：sha1→md5）。
- E2E：3 篇 seed → 14 claims，validate=OK。
- 诚实：existence-gate 变异测试曾在 16% 概率出现假 SURVIVED——定位为 cp936 读者线程 flake（`subprocess.run(text=True)` 在非 ASCII 失败输出上崩溃），修复后 0/10 复现。KNOW_EVIDENCE.md 已订正 provenance。

**Phase 2 — 回填语料 A + 身份映射 + 审计（已收尾，本次更新）**
- v2 回填：34/34 篇全重跑 3 个 claim-bearing prompt（cyber_attacks / opf_surrogate_models / physics_informed_defense），gemini-3.1-flash-lite on :37183 → 34 个 `{paper}_v2_gemini-3.1-flash-lite.json`。
- ingest：34 papers → **123 claims**（existence gate 丢弃 broken/unmatched 后存活）；validate=OK。
- 身份映射：**31 clean / 1 fmatter_trap / 2 unresolved**（`.fmatter` 伪 DOI 绝不作为论文 DOI；unresolved 显式标注，无伪 DOI）。信任层级落地：doi_map > metadata JSON > agent。
- 命中率诚实分布：5 篇小批 81.8%（<90%，用户 Plan A：track-and-report 不硬停）→ 29 篇大批 93.0% → 全 34 篇 **90.5%**。
- manifest 审计全项通过（papers=34==md=34==catalog=34==log=34；anchors malformed=0）。
- 过程修复 3 bug：`classify_fragmented` 对 list 值崩溃（`anchors.coerce_quote` 修复，+6 tests）；1MB 书籍 429 死循环（`MAX_CHARS=90000` cap + `!TRUNC` 标记）；CRLF 污染 paper_id（LF-only 列表重跑）。
- 产出：`phase0-lab/PHASE2_REPORT.md`（全量审计报告）。

**Phase 2 之外、Phase 3 之内**：
- 回填运行器泛化为 `phase0-lab/backfill_run.py`（N 篇，用 production `classify_fragmented` 测命中率）；回填作为 litrev 子命令 / `know ingest --backfill` 增量触发**未做**（Phase 3 项）。

## 5. 修正后的实现计划（四阶段 + Phase 1.0）

第二轮 synthesis 的 Phase 1 schema 里 `claims.quote` 标注"verbatim, from derived JSON"，但 Phase 0.5 证明当前 derived JSON 不满足此前置条件（95.6% 字段无逐字引文）。**修正：在 Phase 1 plumbing 前插入 Phase 1.0（提示词改造 + 回填验证）。** 同时把 peer review 的七个缺口逐一回应。

### Phase 1.0 — 提示词改造 + 回填验证（约 0.5 天，零额外 LLM 调用）【已完成 2026-08-30，见 §4.5】

**这是被 synthesis 删掉、被 Phase 0.5 抓回的一步，也是 peer review WEAKEST LINK 的直接修复。**

1. 改 4 个 prompt 模板（`metadata_extraction.txt` / `cyber_attacks.txt` / `opf_surrogate_models.txt` / `physics_informed_defense.txt`），为每个 claim 组增加独立字段 `evidence_quote`，约束：
   - 原文逐字片段，≤18 词
   - 可含省略号 `...` 连接多个片段（Phase 0.5 子测试 C 证明省略号切片后命中率 91.7%）
   - 不可改写、不可转述（明确禁止"the paper states..."式包装）
   - 无对应原文时填 `null`（保持 litrev 的 "not reported" 约定）
   - `physics_defense` 已有 `evidence_quote_for_physics_use`，对齐命名并推广
2. 用改后的 prompt 重跑语料 A 的那篇测试论文（8 次提取，用户无限量配额），验证 `evidence_quote` 字段命中率 ≥90%（把 Phase 0.5 子测试 D 的模拟换成真实 LLM 提取）。
3. **验收门槛**：真实提取的 `evidence_quote` 命中率 ≥90% 才进 Phase 1。低于 90% 回到 prompt 调约束（放宽词数 / 改用整句而非片段）。

**成本**：0 额外 LLM 调用次数（改 prompt 不增加调用，只约束输出字段）；8 次重提取在用户无限量配额下可忽略。**时间**：约半天。

### Phase 1 — 最小可用知识层 MVP（约 3-4 天，peer review Gap 1 上修）【已完成 2026-08-30，见 §4.5】

**先跑 seed，再写 plumbing**（peer review Gap 2：把 MS 的 3 篇 seed 和 contradiction-map Resolution Path 第 2 项并入，不要排在 plumbing 之后）。

物理布局（唯一新家是 `paper-tools/know/` 包 + `paper-tools/knowledge-base/` git 仓库；源语料只读不动）：

```
paper-tools/know/                    ← 包（Python + uv；stdlib + pyyaml + sqlite3）
├── know.py                          # CLI: know ingest / validate / search / flag
├── anchors.py                       # 确定性分段 + 锚点 id 计算
├── derive.py                        # 扫 derived/*.json → claims 行 → anchors；零 LLM
└── config.py                        # schema 定义；违反的 LLM 页写入被丢弃
paper-tools/knowledge-base/          ← 知识层本身（git 仓库）
├── schema.md                        # 页类型 + 摄取/查询协议（给 agent）
├── purpose.md                       # 综述范围 + 关键问题
├── log.md                           # 追加式，仅代码写
├── papers/{doi_or_uid}.md           # 每篇一页：元数据 + claims + cite:// 锚点表
├── concepts/{slug}.md              # 每概念一页：定义 + 演进 + [[链接]]
├── catalog.md                       # 代码生成的索引；绝不手写
└── index.sqlite                     # 查询底座（gitignored，可重建）
```

**SQLite schema（修正 peer review Gap 4/5/7）**：

```sql
CREATE TABLE papers (
  paper_id    TEXT PRIMARY KEY,      -- canonical doi:10.xxxx/y 或 uid:...
  title       TEXT NOT NULL,
  authors     TEXT NOT NULL,         -- doi_map 的 JSON 数组；原始大小写
  year        INTEGER,
  project     TEXT NOT NULL,
  source_rel  TEXT,
  ocr_version TEXT                   -- peer review Gap 6：OCR 版本作为层级字段，显式存
);
CREATE TABLE claims (
  claim_id    INTEGER PRIMARY KEY,
  paper_id    TEXT NOT NULL REFERENCES papers(paper_id),
  concept     TEXT NOT NULL,
  quote       TEXT NOT NULL,         -- 改造后 prompt 产出的逐字引文（Phase 1.0 前置）
  anchor      TEXT NOT NULL,         -- Phase 0 方案：p{sha1_8(normalized)}
  heading_path TEXT NOT NULL,        -- peer review Gap 4：随锚点存章节路径，查询时验证搬迁
  model       TEXT NOT NULL,         -- 'deepseek' 或 'gemini'，两记录都留
  prompt_id   TEXT NOT NULL,
  conflicting TEXT,                  -- 'note'/'flagged'/NULL
  rank        INTEGER
);
-- peer review Gap 5：修 FTS5 tokenizer。trigram 对 2 字符 CJK 返回 0 行（实测）。
-- 用 bigram + char 索引（llm_wiki 自己的 search.rs 就是这么做的），或加 LIKE 兜底。
CREATE VIRTUAL TABLE claims_fts USING fts5(concept, quote, content_rowid='rowid',
  tokenize='unicode61 remove_diacritics 2');  -- 英文 + CJK token
CREATE VIRTUAL TABLE claims_fts_cjk USING fts5(concept, quote, content_rowid='rowid',
  tokenize='trigram');  -- CJK 子串，但仅对 ≥3 字符有效；2 字符走 LIKE 兜底
```

**Phase 1 验收测试（peer review Gap 5/7 直接落地）**：把用户的三个真实规范查询（如"P2P交易瓶颈"、"攻击者模型"、"FDIA 检测"）作为 Phase-1 验收用例，**在声明 CLI 完成前**测 `know search`，确认 2 字符 CJK 不查空；定义"top-k 返回 0"的分支（扩词 / 直接搜 OCR / 回退到 derived JSON 搜索）。

**借用的 llm_wiki 模式**（按 TD 判决表"Borrow"项）：
- 目录 + frontmatter（type/title/doi/sources[]/created），在解析边界检查；created/updated 留给代码
- `sources[]` 由代码规范化，绝不手写
- `log.md` 追加，仅代码
- SHA256 增量缓存（内容哈希 + outputs 存在性检查）——litrev state 升级：`(prompt_id, model)` 键加内容哈希 + 输出存在性（MS 的 ghost-entry 修复）
- `cite://{paper}/{anchor}` 方案；页-锚存在性门（单元不在派生单元列表则丢链接）

**显式排除（写进 README，防 year-2 手越界）**：wikilinks 维护结构、知识图/社区检测、语义 lint 循环、评审队列 UI、Obsidian 兼容、query/comparison/thesis/synthesis 页类型、v1 嵌入。无 server、无 daemon、无后台 watcher。

**CLI（仅四个）**：
```
know ingest <litrev_project>   # 扫 derived/*.json → claims + anchors（确定性；零 LLM）
know validate                  # YAML 解析；sources[] 存在；quote→anchor 可解；catalog↔files
know search "<concept>"        # FTS5，top-k；2 字符 CJK 走 LIKE 兜底
know flag <claim_id> --note "..."   # 标记不删除（信任链：标，绝不删）
```

**谁跑 validate（peer review Gap 6 的维护循环归属）**：`know validate` 是查询前的 no-op 握手，由 agent 在每次 `know search` 前自动跑，不是用户的手工杂务。写进 schema.md。

### Phase 2 — 回填语料 A（约 1.5-2.5 天，peer review Gap 1 上修）【已完成 2026-08-30，见 §4.5】

1. **身份映射，三层，严格顺序（MS §2.3）**：
   - 语料 A：doi_map.json `doi` 字段规范化；2 个 `no_confidence` → `unresolved` 列表（绝不伪 DOI）
   - 语料 B（无 doi_map）：文件名安全键解码 → 连字符族歧义 → 人工确认列表（默认 3-5 分钟人工过；超时降级为 uid=目录名）；arXiv 原始 ID → `arxiv:1609.02907v4`；作者-年名 → uid 原样
   - 键归一化：去转义后小写再 dedup；显示保留原始大小写
2. **合并而非去重**：A 的 2024 记录与 B 的 2025 记录同 DOI 在一页共存，带 `conflicting` flag；绝不删任一。信任层级：pipeline 验证产物 > 提取记录 > agent 判断。
3. **引文→锚点映射是纯函数**（paragraphize + 归一化文本匹配；无 LLM）。每个引文三种结局：`matched` / `broken`（单元不再存在→警告）/ `unmatched`（无模糊命中→记入 manifest `warnings[]`，不编造）。
4. **回填 manifest + 审计**：paper 页数 == manifest 计数；derived JSON 链接 == 挂载 claim 记录；2 个 `no_confidence` 显式入 `unresolved`；锚点计数 == 章节计数；catalog 页数 == md 文件；log 条目 == 批次计数。
5. **回滚**：每批 = 一个 git commit；`git revert` = 秒级；全回滚 = 删 knowledge-base 目录（源不动）。
6. **回填作为 litrev 子命令**或 `know ingest --backfill`：下一次 litrev-extract 运行时在覆写 derived JSON 前自动触发增量回填（MS 的黄金规则：旧版本送进层后再被覆写）。

### Phase 3 — Agent 查询协议（真正的接口）【未开始】

写进 schema.md（任何 Claude Code agent session 都能跑）：

1. 用户/agent 给概念（"P2P trading 瓶颈是顺序 agent 更新导致网络环境变化"）
2. agent 跑 `know search "<concept>"`（FTS5；可选 3-5 关键词变体）
3. agent 读 top `claims` 行，跟 `cite://` 锚点 → 打开确切 OCR 段落（经 `index.sqlite` 行，非目录扫描）
4. agent 以（论文、作者、年份、段落 id、逐字引文）作答
5. 答案在 chat 交付；除 `flag` 外不回写层

**归属协议（F5——"谁说的"缺口）**：第 4 步，agent 须区分：
- **署名作者**（谁写了论文——来自 `papers.authors`）——答"哪篇"
- **指代 referent**（引文内"X (2019) showed that..."——真正 claim 持有者）
规则：引文句若点名他人结果，"谁说的"答指代 referent + 源论文作载体；无指代则答署名。指代 referent 不存为 claim（agent 判）；低置信归属须标记，绝不编造。可选（无 LLM 成本顾虑）：同概念复现时写懒概念页——页创建是层内唯一 LLM 写入。

## 6. peer review 七缺口的逐一回应（对照表）

| Gap | peer review 指出 | 本计划回应 | 落在哪 |
|---|---|---|---|
| 1 | 时间乐观 2x；Phase 1 "2 天"是红旗 | 上修 Phase 1→3-4 天，Phase 2→1.5-2.5 天；超时砍 gazette 概念页到 v1.1，保 existence gate 和 cache | Phase 1/2 时间估计 |
| 2 | MS 3 篇 seed + contradiction-map Resolution Path 第 2 项被丢 | 并入 Phase 1：seed 先于 plumbing；Phase 1.0 即 seed 的真实化 | Phase 1.0 + Phase 1 开头 |
| 3 | OCR 实验输入欠规约；语料 A 可能无 OCR md | Phase 0 已用 2 次新 OCR 跑（确认语料 A 无成对）；决策表补"同参数跑"已隐含 | Phase 0 已完成 |
| 4 | 决策表缺混合列 + "段落跨节搬迁"行 | `heading_path` 随锚点存，查询时验证；per-claim 混合策略（Phase 0 实测漂移 <5%，混合是主情形） | schema `claims.heading_path` |
| 5 | FTS5 对 2 字符 CJK 返回 0 行（实测） | 加 bigram/char tokenizer + LIKE 兜底；三个真实规范查询作 Phase-1 验收 | schema + Phase 1 验收 |
| 6 | existence gate 与"重建策略"是两对象；无人算过每次重锚死多少 claim | `ocr_version` 作层级字段；Phase 0 实测漂移 <5% → 每次 re-OCR 死 <5% claim，可接受 | schema `papers.ocr_version` + Phase 0 数据 |
| 7 | 失败处理缺失（seed 失败、归属失败、查询返回空） | `know search` 返回 0 的分支；归属低置信 flag 列；seed 失败的 ingest 语义 | Phase 1 验收 + Phase 3 协议 |

## 7. 是否构建的诚实标注（peer review Gap 6 / Missing Perspective）

当前语料 84 篇（A 34 + B 50）。在 <500 篇时，"JSON 文件夹 + agent 直接读文件"确实够用，markdown+grep 也够。Phase 0/0.5 验证的是**锚点 schema 可行性**，不是"必须现在建知识层"。

**退出点已设计进 Phase 0**（peer review："计划结构本身给了承诺前退出点"）：Phase 0 + Phase 1.0 完成后，若真实提取的 `evidence_quote` 命中率 <90% 或用户判断当前规模不值得，可停在"JSON 文件夹 + agent 读文件"，到 500 篇再 revisit。**这是文档化的决策表行，不是从未给行的想法。**

是否进 Phase 1 由两件事决定：(a) Phase 1.0 真实命中率 ≥90%；以及 (b) 用户的真实跨项目查询需求。LLM 配额不是约束。

## 8. 已验证 vs 未验证（诚实分层）

**已验证（实验实测）**：
- OCR 段落级高度稳定（漂移 <5%，全部 ratio ≥0.95）——Phase 0
- 改造后 prompt 产出的短逐字片段 100% 可回锚——Phase 0.5 子测试 D
- 当前 derived JSON 95.6% 字段无逐字引文——Phase 0.5
- 语料 A 真实结构（34/272/32/2）、`.fmatter` 陷阱、语料 B 四身份族——peer review 实地核验
- 真实 LLM 提取（v2 prompt）evidence_quote 命中率全 34 篇 **90.5%**（5 篇小批 81.8%→29 篇 93.0%）——Phase 2
- 身份映射全量正确：31 clean / 1 fmatter_trap / 2 unresolved，无伪 DOI——Phase 2
- know 包 51/51 tests + 4/5 变异 kill——Phase 1

**合理推测（未实测但有依据）**：
- 锚点方案在 1000 篇/项目规模可存活——基于 Phase 0 的 <5% 漂移 × 文件计数，未经大规模实测
- Phase 3 Agent 查询协议 + concepts/ 概念页在真实 agent 会话可用——待 Phase 3 实施

**暂时无法验证**：
- agent 是否真会在写作时查询此层（peer review 第一轮最弱链接 + Missing Perspective）——只有真实使用才知道
- 跨 OCR 引擎移植性（synthesis Frontier Question）——Phase 0 只测了 DeepSeek-OCR 自身稳定性，换引擎未测
- 语料 B 身份映射（无 doi_map，四身份族）——未触及（Phase 3 范围）

**已被证伪的假设**：
- ~~"现有 derived JSON 可直接确定性重锚到 OCR 段落"~~——Phase 0.5 证伪，95.6% 字段无可锚定引文。修复靠 prompt 改造（Phase 1.0）。

## 9. 下一步（等用户定）

**Phase 0 / 0.5 / 1.0 / 1 / 2 全部完成**（2026-08-30）。当前待决策：是否进入 **Phase 3 — Agent 查询协议**（真正的接口，见 §5 Phase 3 块）：

1. **Phase 3 Agent 查询协议**——schema.md 化，search→cite://→答 attributed 归属（含作者归属）。这是把已建好的 34 篇知识层真正交付给 agent 用的那一步。
2. **concepts/ 概念页**——懒创建，层内唯一 LLM 写入（同概念复现时）。
3. **语料 B 身份映射**——无 doi_map，四身份族（A 之外第二块语料）。
4. **回填作为 litrev 子命令 / `know ingest --backfill`**——下一次 litrev-extract 运行覆写前自动增量触发。

是否进 Phase 3 由用户决定（LLM 配额不是约束）。若不进，当前 34 篇已可被 agent 经 `know search` 查询（Phase 1 的 CLI 已交付）。

## 附录 A：产出文件清单

- `outputs/direction-judge/`（第一轮 multi-lens-research，8 个文件）
- `outputs/tech-architecture/`（第二轮，7 个文件，均为 2026-08-29）
- `phase0-lab/PHASE0_REPORT.md`——Phase 0 结果
- `phase0-lab/PHASE05_REPORT.md`——Phase 0.5 结果
- `phase0-lab/phase05_final.py`——Phase 0.5 决定性测试脚本（A/B/C/D 四组）
- `phase0-lab/analyze_ocr_diff.py` / `near_miss_detail.py` / `line_reorder.py`——Phase 0 diff 脚本
- `phase0-lab/labs/run1/` / `labs/run2/`——两次 OCR 原始输出

## 附录 B：llm_wiki 克隆位置

`C:\Users\VerNe\Downloads\Documents\paper-tools\llm_wiki\`（源码检出，未构建，无 node_modules/src-tauri/target）。仅供借鉴模式参考，不安装其 Tauri/Rust 应用（第一轮结论：推迟）。
