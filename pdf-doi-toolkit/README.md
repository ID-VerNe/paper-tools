# pdf-doi-toolkit — PDF DOI 重命名工具包

将散乱命名的学术 PDF 统一重命名为 `DOI.pdf` 格式。

## 安装

本项目使用 [uv](https://docs.astral.sh/uv/) 管理依赖。

```bash
uv sync
```

**依赖:** 零第三方依赖（仅标准库：`urllib`, `json`, `re`, `threading`, `concurrent.futures`）

## 快速开始

```python
from pdf_doi_toolkit import simple_rename_pipeline

matcher = simple_rename_pipeline("path/to/pdf/directory")
print(matcher.summary())
```

## 分步使用

```python
from pdf_doi_toolkit import DOIMatcher

matcher = DOIMatcher("path/to/pdf/directory")

# 第 1 步：扫描 PDF
matcher.scan_pdfs()
print(f"待处理: {len(matcher.entries)} 篇")

# 第 2 步：CrossRef 查询 + 作者验证
matcher.run_crossref_check()

# 第 3 步：重命名匹配的
matcher.rename_matched()

# 第 4 步：保存报告
matcher.save_report()
```

## 处理 ScienceDirect 格式

```python
matcher = DOIMatcher("path/to/pdfs")
matcher.scan_sciencedirect()
matcher.run_crossref_check()
matcher.rename_matched()
```

## x-mol 人工确认兜底

```python
from pdf_doi_toolkit import XMolFallback

# 生成确认清单
checklist = XMolFallback.generate_checklist(entries)

# 用户确认后执行
matcher.verify_manual_dois({"old.pdf": "10.xxxx/xxxxx"})
```

## 项目结构

```
pdf-doi-toolkit/
├── pdf_doi_toolkit/          # 主包
│   ├── __init__.py           # 导出 + 一键入口
│   ├── config.py             # 所有可调参数
│   ├── utils.py              # 工具函数（作者匹配、DOI 格式处理）
│   ├── crossref.py           # CrossRef API 客户端
│   ├── scanner.py            # PDF 扫描与元数据提取
│   ├── sciencedirect.py      # ScienceDirect PII 解析
│   ├── xmol.py               # x-mol 兜底查询
│   └── matcher.py            # DOIMatcher 主引擎
├── demo.py                   # 使用示例
├── tests.py                  # 单元测试
└── README.md                 # 本文件
```

## 关键特性

- **宽松作者匹配** — 容忍 Unicode 差异（`Kutálek` ↔ `Kutalek`）、拼写变体（`Kalahdaran` ↔ `Kaladharan`）、连字符变体
- **自动 .s001 兜底** — CrossRef 返回补充材料后缀时自动剥离重查
- **指数退避重试** — 5 次重试 + 随机抖动，应对 429 限流
- **ScienceDirect 支持** — PII 格式解析 + 按标题查询
- **x-mol 兜底** — 生成搜索链接和确认清单，人工确认后执行
- **零依赖** — 仅标准库