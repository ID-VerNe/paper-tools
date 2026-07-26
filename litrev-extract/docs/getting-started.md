# Getting Started with litrev-extract

## Installation

```bash
pip install litrev-extract
```

Requires Python 3.10+.

## Creating a Review Project

```bash
litrev init my-literature-review
cd my-literature-review
```

This creates:

```
my-literature-review/
├── litrev.yaml          # Main configuration file
├── prompts/
│   └── metadata.txt     # Sample prompt template
├── documents/
│   └── sample_paper.md  # Sample document to test with
├── scripts/             # Custom post-processors go here
├── output/              # Results generated here
└── .gitignore
```

## Configuration

Edit `litrev.yaml` to configure:

1. **Input**: Where your papers are and what formats
2. **Models**: Which LLMs to use (any OpenAI-compatible API)
3. **Prompts**: What data to extract from each paper
4. **Post-processing**: How to aggregate and report results

See [Configuration Reference](configuration.md) for full details.

## Setting API Keys

litrev-extract reads API keys from environment variables — never put keys in your config file:

```bash
# For OpenAI-compatible APIs
export LLM_API_KEY=sk-your-key-here

# Or for Anthropic
export ANTHROPIC_API_KEY=sk-ant-your-key-here
```

The environment variable name is specified in `litrev.yaml` under `models[].api_key_env`.

## Running Extraction

```bash
# Run with the default model (first one in config)
litrev run

# Run with a specific model
litrev run --model opus

# Run with more workers (if rate limits allow)
litrev run --model opus --workers 10

# See what would be done without executing
litrev run --dry-run
```

The pipeline:
1. Scans `documents/` for supported files
2. Creates a task for each (document × prompt) combination
3. Sends each task to the LLM with the configured prompt template
4. Saves valid JSON responses to `output/derived/`
5. Tracks progress in `.litrev_state.json` for resumability

## Post-Processing

After extraction completes, run the post-processing pipeline:

```bash
litrev postproc --model opus
```

This runs the configured steps in `litrev.yaml` → `postproc.pipeline`, typically:

1. **Aggregate**: Consolidates per-document results into per-prompt JSON files
2. **Stats**: Computes summary statistics (e.g., value counts, means)
3. **CSV Export**: Generates CSV files for plotting
4. **Report**: Generates a markdown summary report

## Custom Post-Processors

Write custom Python classes in the `scripts/` directory:

```python
# scripts/my_analysis.py
from litrev_extract.postproc.base import PostProcessor
from litrev_extract.postproc.registry import register_post_processor

@register_post_processor("my_analysis")
class MyAnalysis(PostProcessor):
    name = "my_analysis"

    def run(self, config, model_alias):
        # Your analysis logic here
        return {"status": "done"}
```

Then add to your `litrev.yaml`:

```yaml
postproc:
  pipeline:
    - name: my_analysis
      module: "scripts.my_analysis"
```

## Project Structure Tips

- **Large document sets**: Use subdirectories under `documents/` for organization
- **Multiple models**: Run `litrev run --model ds` then `litrev run --model opus` to compare
- **Version control**: Commit `litrev.yaml`, `prompts/`, and `documents/`; add `output/` and `.litrev_state.json` to `.gitignore`