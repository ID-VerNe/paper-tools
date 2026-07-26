# Digital SERS Systematic Review — litrev-extract Example

This example reproduces the original extraction pipeline for "ML-Driven Digital SERS Analysis" using the reusable framework.

## Structure

```
digital-sers/
├── litrev.yaml              # Configuration for 21 prompts, 3 models
├── prompts/                 # 20 prompt template files (from original project)
├── documents/               # Papers go here (symlink or copy from 01-文章库/汇总)
└── output/                  # Results
    ├── derived/             # Per-document, per-prompt, per-model JSON
    ├── aggregate/           # Per-prompt aggregated (ds/, glm/, gpt55/)
    ├── reports/             # Markdown reports
    └── plots/               # Plot CSVs
```

## Usage

```bash
# Set API keys
export ANTHROPIC_API_KEY=sk-...
export OPENAI_API_KEY=sk-...  # Used by both DeepSeek and GLM

# Run with Claude
litrev run --model opus --config litrev.yaml

# Run with DeepSeek
litrev run --model ds --config litrev.yaml

# Run with GLM
litrev run --model glm --config litrev.yaml

# Aggregate and generate reports
litrev postproc --model opus --config litrev.yaml
```

## Configuration Highlights

- **21 extraction prompts** covering timeline, threshold strategies, ML models, preprocessing, applications, future framework, etc.
- **3 models**: Claude Opus 4.8 (low concurrency), DeepSeek v4 Pro (high concurrency), GLM 5.1 (high concurrency)
- **Post-processing**: aggregate → stats → CSV export → markdown report
- **Resumable**: state tracked per-model, incremental re-runs skip completed tasks