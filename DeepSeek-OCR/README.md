# DeepSeek-OCR 本地封装工具

将 PDF 和 EPUB 文件通过 DeepSeek-OCR API 转换为 Markdown 格式，无需本地 GPU。

## 使用方式

### 安装依赖

```bash
uv sync
```

### CLI 模式

```bash
# 单文件
uv run python ocr_cli.py input.pdf

# 批量目录
uv run python ocr_cli.py ./papers/ --output ./output/

# 指定 API Key
uv run python ocr_cli.py input.pdf --api-key sk-xxxxx
```

### GUI 模式

```bash
uv run python ocr_gui.py
```

或直接双击 `start_ocr.bat`。

## 配置

首次使用前，在 GUI 中填入 API 配置，或直接编辑 `settings.json`：

```json
{
  "api_url": "https://api.siliconflow.cn/v1/chat/completions",
  "api_key": "sk-xxxxx",
  "model_name": "deepseek-ai/DeepSeek-OCR",
  "dpi": 144,
  "workers": 10,
  "resume": true
}
```

> **注意**：`settings.json` 包含 API Key，已加入 `.gitignore`，不会被提交到版本控制。

## 功能

- **PDF 转 Markdown**：逐页渲染为图片，调用 DeepSeek-OCR API 识别，保留图片引用
- **EPUB 转 Markdown**：直接解析 EPUB 内部 HTML，提取文本和图片
- **断点续传**：处理过程中保存进度，中断后可恢复
- **多线程并发**：Scanner → Renderer → Worker 三阶段流水线
- **自动重试**：指数退避重试（最多 10 次），自动跳过不可重试错误（如 Key 无效、余额不足）

## 项目文件

| 文件 | 说明 |
|------|------|
| `ocr_engine.py` | 核心引擎：PDF 渲染、API 调用、Markdown 后处理、EPUB 转换调度 |
| `ocr_cli.py` | 命令行界面 |
| `ocr_gui.py` | Tkinter 图形界面 |
| `epub_converter.py` | EPUB 转换器（ebooklib + BeautifulSoup + html2text） |
| `settings.json` | 持久化配置（API URL、Key、模型名、DPI、线程数等） |
| `start_ocr.bat` | 双击启动脚本（自动调用 `uv run python ocr_gui.py`） |