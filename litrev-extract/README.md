# litrev-extract

**Config-driven LLM-based systematic literature review extraction framework.**

`litrev-extract` is a pip-installable Python package that helps researchers automatically extract structured data from scientific papers using Large Language Models (LLMs). Instead of writing ad-hoc scripts for each review project, you define your extraction tasks in a YAML config file, write prompt templates, and let the framework handle the pipeline: document scanning, prompt rendering, LLM API calls with rate limiting and retry, result storage, aggregation, statistics, and report generation.

## Quick Start

### 本地开发（使用 uv）

```bash
# 安装依赖
uv sync

# 查看可用命令
uv run litrev --help
```

### 作为 PyPI 包安装

```bash
pip install litrev-extract

# Scaffold a new review project
litrev init my-review
cd my-review

# Set your API key
export LLM_API_KEY=sk-...

# Add papers to documents/
# Edit prompts in prompts/

# Run extraction
litrev run --model default

# Post-process results
litrev postproc --model default
```

## Features

- **Config-driven**: Everything from models to prompts to post-processing is defined in a single `litrev.yaml` file — no code changes needed
- **Model-agnostic**: Works with any OpenAI-compatible API (Claude, GPT, DeepSeek, GLM, etc.)
- **Resumable**: Tracks task state so interrupted runs can continue where they left off
- **Concurrent**: Parallel extraction with configurable worker count and rate limiting
- **Extensible**: Plugin-based post-processing system — built-in aggregation, statistics, CSV export, and markdown report generation; write custom processors for domain-specific analysis
- **Secure**: API keys read from environment variables, never stored in config files
- **Configurable output**: Customizable file naming patterns and directory layouts

## Commands

| Command | Description |
|---------|-------------|
| `litrev init <name>` | Scaffold a new review project |
| `litrev run --model <alias>` | Run the extraction pipeline |
| `litrev postproc --model <alias>` | Run the post-processing pipeline |
| `litrev run --dry-run` | Enumerate tasks without executing |

## Documentation

- [Getting Started](docs/getting-started.md)
- [Configuration Reference](docs/configuration.md)
- [Writing Prompt Templates](docs/prompt-templates.md)

## Example

```yaml
# litrev.yaml
project:
  name: "my-review"
  description: "A systematic literature review"

input:
  directory: ./documents
  formats: [.md, .txt]

models:
  - alias: "opus"
    api_key_env: "ANTHROPIC_API_KEY"
    base_url: "https://api.anthropic.com/v1"
    model_name: "claude-opus-4-8"
    max_concurrent: 3

prompts:
  - name: "metadata"
    id: "v1_metadata"
    file: "prompts/metadata.txt"
    system_prompt: "You are an expert scientific researcher."
```

## License

MIT