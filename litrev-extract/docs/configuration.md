# Configuration Reference

The entire review project is configured through a single `litrev.yaml` file.

## Full Schema

```yaml
# === Project Metadata ===
project:
  name: "my-review"                     # Required: project name
  description: "..."                    # Optional: description

# === Input Documents ===
input:
  directory: ./documents                # Required: path to paper files
  formats: [.md, .txt]                  # Required: supported file extensions
  recursive: true                       # Optional: scan subdirectories (default: true)
  exclude_patterns: []                  # Optional: glob patterns to exclude

# === Output Layout ===
output:
  directory: ./output                   # Optional: output root (default: ./output)
  structure: "flat"                     # Optional: "flat" or "mirror" (default: flat)
  result_subdir: "derived"              # Optional: per-document results (default: derived)
  aggregate_subdir: "aggregate"         # Optional: aggregated results (default: aggregate)
  report_subdir: "reports"              # Optional: reports (default: reports)
  plot_subdir: "plots"                  # Optional: plot data (default: plots)
  file_naming:
    pattern: "{base}_{prompt_name}_{model_alias}.json"  # Output file naming pattern

# === LLM Models (at least one required) ===
models:
  - alias: "opus"                       # Required: short name for state/file naming
    api_key_env: "ANTHROPIC_API_KEY"    # Required: env var containing the API key
    base_url: "https://api.anthropic.com/v1"  # Required: API endpoint
    model_name: "claude-opus-4-8"       # Required: model identifier sent to API
    max_concurrent: 3                   # Optional: max parallel requests (default: 3)
    max_retries: 10                     # Optional: max retries per task (default: 10)
    retry_delay_base: 2                 # Optional: exponential backoff base in seconds (default: 2)
    rate_limit:
      max_requests: 50                  # Optional: max requests per window (0 = no limit)
      window_seconds: 60                # Optional: sliding window in seconds (default: 60)

# === Prompt Definitions (at least one required) ===
prompts:
  - name: "metadata"                    # Required: short name for file naming
    id: "v1_metadata"                   # Required: unique ID for state tracking
    file: "prompts/metadata.txt"        # Required: path to prompt template file
    system_prompt: "You are an expert..."  # Required: system prompt for the LLM
    content_truncation: 20000           # Optional: max chars of paper content (default: full)

# === State File ===
state_file: ".litrev_state.json"        # Optional: path for pipeline state

# === Post-Processing Pipeline ===
postproc:
  pipeline:
    - name: aggregate                   # Step name (matches registered processor)
      module: "litrev_extract.postproc.aggregate"  # Python module path
      enabled: true                     # Optional: enable/disable step
      config:                           # Optional: step-specific configuration
        output_dir: "./output/aggregate"

    - name: stats
      module: "litrev_extract.postproc.stats"
      config:
        output_file: "./output/reports/quick_stats.json"
        sections:
          - prompt: "metadata"
            field: "citation.year"
            type: value_counts          # value_counts, mean, or count_true
            top_k: 10

    - name: export_csv
      module: "litrev_extract.postproc.export_csv"
      config:
        exports:
          - name: "timeline"
            prompt: "timeline_evolution"
            fields:
              - source: "year"
                alias: "year"
              - source: "milestone_category"
                alias: "category"

    - name: report_md
      module: "litrev_extract.postproc.report_md"
      config:
        output_file: "summary_report.md"
        sections:
          - title: "Year Distribution"
            source_prompt: "metadata"
            content: |
              ## Year Distribution
              {{value_counts "metadata" "citation.year"}}

# === Processing Overrides ===
# These can also be set per-model
processing:
  default_max_concurrent: 5
  default_max_retries: 10
  skip_completed: true
```

## File Naming Pattern

The `output.file_naming.pattern` supports these template variables:

| Variable | Example Value |
|----------|---------------|
| `{base}` | `paper_001` |
| `{prompt_name}` | `metadata` |
| `{prompt_id}` | `v1_metadata` |
| `{model_alias}` | `opus` |
| `{model_name}` | `claude-opus-4-8` |

## Output Structure

### Flat mode (default):
```
output/
├── derived/
│   ├── paper_001_metadata_opus.json
│   ├── paper_001_timeline_opus.json
│   └── paper_002_metadata_opus.json
├── aggregate/
│   ├── metadata.json
│   └── timeline_evolution.json
├── reports/
│   └── summary_report.md
└── plots/
    └── opus/
        └── timeline.csv
```

### Mirror mode:
```
output/
├── derived/
│   └── subfolder/
│       ├── paper_001_metadata_opus.json
│       └── ...
├── aggregate/
└── ...
```

## Notes

- All paths in the config are relative to the directory containing `litrev.yaml`
- API keys are NEVER stored in the config file — use environment variables
- Prompt template files use `{content}` as a placeholder where the paper text is inserted
- The `formats` field supports: `.md`, `.markdown`, `.txt`, `.pdf_text`