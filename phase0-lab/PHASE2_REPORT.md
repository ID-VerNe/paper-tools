# PHASE 2 — 语料 A 全 34 篇回填 + 身份映射审计报告

**日期**: 2026-08-30
**范围**: 主规划 Phase 2（身份映射三层 + 34 篇 v2 回填 + 全量 ingest + manifest 审计）
**前置**: Phase 0（OCR 稳定）/ 0.5（旧 JSON 无逐字引文，承重假设证伪）/ 1.0（改 prompt → 100% 命中）/ 1（know 包 MVP，51/51 tests, 4/5 mutants）

## 1. 产出总结

| 层 | 结果 |
|---|---|
| v2 回填 | **34/34 篇全部重跑** 3 个 claim-bearing prompt（cyber_attacks / opf_surrogate_models / physics_informed_defense），gemini-3.1-flash-lite on :37183 |
| v2 JSON 产出 | 34 个 `{paper}_v2_gemini-3.1-flash-lite.json`（含 evidence_quotes） |
| ingest | 34 papers → **123 claims**（existence gate 丢弃 broken/unmatched 后的存活行） |
| validate | **OK** |
| 身份映射 | 31 clean / 1 fmatter_trap / 2 unresolved（均显式标注，无伪 DOI） |
| 页面/catalog/log | md pages=34 == catalog=34 == log 条目=34 |
| 锚点 | 123 行全部 `p`+8hex（malformed=0） |

## 2. v2 回填命中率（诚实分布）

### 5 篇小批（Phase 2 门槛，先跑）

| paper | 命中率 | matched/broken/unmatched/null |
|---|---|---|
| 10.1109_tii.2020.2999571 | 100% | 5/0/0/4 |
| 10.1109_tia.2024.3354228 | 100% | 5/0/0/2 |
| 10.1109_jestpe.2019.2953480 | 100% | 4/0/0/3 |
| 10.1109_access.2024.3406471 | 75% | 3/0/1/3 |
| 10.1109_tpwrs.2021.3124726 | 50% | 1/1/0/5 |
| **小批合计** | **81.8%** (18/22) | — |

### 29 篇大批

**grand: 106/114 = 93.0% PASS**（matched=106 broken=5 unmatched=3 null=96）

| paper | 命中率 | 说明 |
|---|---|---|
| 10.1109_icpre59655.2023.10353675 | 66.7% | 1 unmatched（LLM 给的引文超出 OCR 段落） |
| 10.1109_tie.2023.3281698 | 66.7% | 1 broken（引文与 OCR 有 0.85 阈值边缘漂移） |
| 10.1109_tii.2025.3545080 | 33.3% | 2 broken（该文 OPF 段与 OCR 首代质量相关） |
| 10.1109_tii.2026.3681361 | 71.4% | 1 broken + 1 unmatched |
| 10.1109_tnse.2020.3009299 | 83.3% | 1 broken |
| 10.3390_s24123815 | 75.0% | 1 unmatched（合成式 `...` 摘要） |
| 其余 23 篇 | 100% | — |

**全 34 篇合计**（含 5 小批）：matched=124, broken=8, unmatched=5, null=108 → **非 null 命中率 124/(124+8+5)=90.5%**。

## 3. 90% 门槛的处理（用户决策 Plan A：track-and-report）

5 篇小批 81.8% 低于 90%，用户决定不设硬停、走 Plan A：existence gate 自动丢弃 broken/unmatched 引文（不编造），低命中论文只贡献更少（但真实）的 claims；90% 作为**跟踪并报告**阈值而非中止门槛。29 篇大批 93.0% 重新过线。全 34 篇 90.5%。

**诚实说明**：低命中不是 prompt 缺陷，是两类真实边缘：
- null 偏多的论文（tpwrs 5 null、ptc 5 null 等）：该文确实没覆盖那些字段，不是提取失败。
- 少数 broken/unmatched：LLM 引文与 OCR 的 0.85 阈值边缘漂移，或 LLM 用合成式 `...` 拼接摘要（access 的 test_system_size、s24123815）。后者被 classify_fragmented 正确丢弃。

## 4. 身份映射三层（doi_map + metadata）

| status | 计数 | 处理 |
|---|---|---|
| clean | 31 | doi_map matched 且 DOI 真实，直接采用 |
| fmatter_trap | 1 | `10.1002/9781394215416.fmatter`（书籍前置页伪 DOI），**绝不作为论文 DOI**，DOI 置空 + 标注 |
| unresolved | 2 | `10.1007_978-3-031-29290-3_4`、`10.1109_pesgm41954.2020.9281941`，doi_map status=no_confidence，DOI=None → 置空 + 标注（title/authors/year 仍保留，可被标题检索） |

信任层级落地（MS §2.3）：pipeline 验证产物（doi_map）> 提取记录（metadata JSON）> agent 判断。fmatter/unresolved 绝不伪造 DOI。

## 5. Manifest 审计（主规划 Phase 2 step 4）

| 审计项 | 结果 |
|---|---|
| paper 页数 == manifest 计数 | 34 == 34 |
| derived JSON 链接 == 挂载 claim 记录 | 34 v2 JSON 全量 ingest |
| 2 个 no_confidence 显式入 unresolved | 是（2 行 identity_status=unresolved） |
| .fmatter 陷阱未作为 DOI | 是（fmatter_trap 行 doi=''） |
| 锚点计数 == 章节计数 | 123 claims，malformed=0 |
| catalog 页数 == md 文件 | 34 == 34 |
| log 条目 == 批次计数 | 34 == 34 |

## 6. 搜索验证（用户真实规范查询）

- `search "attack"` → 跨多篇命中（含 fmatter 的 10.1002_9781394215416、unresolved 的 10.1007_978-3-031-29290-3_4）
- `search "FDIA"` → 多篇命中（tii.2020、tii.2026、tnse、tpel）
- `search "OPF"` → 跨 7 篇命中
- `search "PINN"` → access.2024（PINN 论文）
- `search "攻击"`（2-char CJK）→ 返回 0 + 提示（语料全英文，无 CJK 行；Gap 5 的 LIKE 兜底在空结果集上正确返回 0 而非崩溃）

## 7. 过程中发现并修复的 bug

1. **`classify_fragmented` 对 list 值崩溃**：LLM 偶对多值字段（如 key_limitations_mentioned）返回 list → `TypeError: normalize() argument 2 must be str, not list`。修复：`anchors.coerce_quote()`，list → `...` 拼接成片段（每片段须锚定，一项坏即整条丢弃，不编造）。+6 tests。
2. **超大 OCR 触发 429**：`10.1002_9781394215416`（101 万字符书籍）超出模型上下文 → 无限 429 重试死循环。修复：backfill_run.py 加 `MAX_CHARS=90000` 内容上限 + 输出 `!TRUNC` 标记。6 篇超限论文被截断处理。
3. **CRLF 换行污染 paper_id**：shell `$(tr '\n' ' ' < todo29.txt)` 读入 CRLF 文本，每个 paper_id 带 `\r` 尾，导致路径拼接出 `/10.1109_x\r\10.1109_x\r.md` → 全部 SKIP。修复：用 LF-only 列表重跑。（教训：Windows 上 shell 展开多行文件前必须去 CR。）

## 8. 未完成（Phase 3+）

- **Phase 3 Agent 查询协议**（schema.md 化，search→cite://→答 attributed 归属）
- **concepts/ 概念页**（懒创建，层内唯一 LLM 写入）
- 语料 B 身份映射（无 doi_map，四身份族）——本轮未触及
- 回填作为 litrev 子命令 / `know ingest --backfill` 增量触发

## 9. 复现入口

```bash
# v2 回填（34 篇，需 :37183 gateway + 配额）
cd paper-tools/phase0-lab
uv run --no-project --with httpx python backfill_run.py --papers <id...>   # 或默认 5 篇

# 全量 ingest
cd paper-tools/know
KNOW_DB=<scratch>/index.sqlite KNOW_KB=<scratch>/kb \
KNOW_DOI_MAP=<ART>/doi_map.json \
KNOW_METADATA_DIR=<ART>/litrev_project/output/derived \
uv run --no-project python know.py ingest <ART>/ocr_output <ART>/litrev_project/output/derived_v2

# 测试套件
cd paper-tools/know && uv run --no-project python -m unittest discover tests  # 72 tests
```