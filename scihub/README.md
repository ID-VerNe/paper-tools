# Sci-Hub Paper Downloader

Download academic papers from Sci-Hub by DOI, with automatic mirror fallback and retry logic.

## 依赖 & 安装

本项目使用 [uv](https://docs.astral.sh/uv/) 管理依赖。

```bash
# 安装依赖
uv sync

# 或手动添加依赖
uv add requests beautifulsoup4
```

## 使用方法

```bash
# 下载单篇论文
python scihub.py --doi 10.1039/D0TC00002G

# 使用自动镜像选择（推荐）
python scihub.py --doi 10.1039/D0TC00002G --mirror -1

# 批量下载（从文件读取 DOI）
python scihub.py --doi-file doi.txt

# 不自动重试
python scihub.py --doi 10.1039/D0TC00002G --no-retry

# 调试模式
python scihub.py --doi 10.1039/D0TC00002G -v
```

## 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--doi-file` | DOI 输入文件 | `doi.txt` |
| `--doi` | 直接指定 DOI（可多个） | - |
| `--mirror` | 镜像索引（-1 = 自动） | `0` |
| `--mode` | 输出文件名模式 (`doi` / `title`) | `doi` |
| `--no-retry` | 禁用重试 | - |
| `--max-retries` | 最大重试次数 | `5` |
| `--sleep` | 批量下载间隔秒数 | `30` |
| `-v` | 调试日志 | - |

## 更新镜像列表

```bash
# 从 lovescihub.wordpress.com 抓取已知镜像
python update_link.py

# 暴力扫描所有 sci-hub.{xx} TLD 组合（1352个URL，自动探测可用镜像）
python update_link.py --scan
```

另外，当 `--mirror -1` 自动模式尝试所有镜像失败后，会提示是否自动扫描镜像。选择 `y` 后会先尝试 WordPress 爬取，失败则自动暴力扫描所有 TLD 组合，扫描到的镜像会缓存到 `link.txt` 本地文件。`refresh_mirrors()` 方法也内置了同样的两级回退逻辑。
