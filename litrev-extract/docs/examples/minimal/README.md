# Minimal litrev-extract Example

Quick-start example with 2 prompts, 3 sample papers, and 1 model.

```bash
litrev init --dir minimal-example
cd minimal-example
export LLM_API_KEY=sk-...
litrev run
litrev postproc
```

## litrev.yaml

```yaml
project:
  name: "minimal-review"
  description: "Quick test of litrev-extract with 3 papers"

input:
  directory: ./documents
  formats: [.md, .txt]

output:
  directory: ./output
  file_naming:
    pattern: "{base}_{prompt_name}_{model_alias}.json"

models:
  - alias: "default"
    api_key_env: "LLM_API_KEY"
    base_url: "https://api.openai.com/v1"
    model_name: "gpt-4"
    max_concurrent: 3

prompts:
  - name: "metadata"
    id: "v1_metadata"
    file: "prompts/metadata.txt"
    system_prompt: "You are an expert scientific researcher focused on bibliographic data extraction."

  - name: "study_analysis"
    id: "v1_study"
    file: "prompts/study_analysis.txt"
    system_prompt: "You are an expert scientific reviewer. Extract study design information."

postproc:
  pipeline:
    - name: aggregate
      module: "litrev_extract.postproc.aggregate"
    - name: stats
      module: "litrev_extract.postproc.stats"
      config:
        sections:
          - prompt: "metadata"
            field: "citation.year"
            type: value_counts
    - name: report_md
      module: "litrev_extract.postproc.report_md"
      config:
        output_file: "summary.md"
        sections:
          - title: "Year Distribution"
            content: |
              {{value_counts "metadata" "citation.year"}}
```

## Expected Output

After running `litrev run` and `litrev postproc`:

```
output/
├── derived/
│   ├── paper1_metadata_default.json
│   ├── paper1_study_analysis_default.json
│   ├── paper2_metadata_default.json
│   ├── paper2_study_analysis_default.json
│   └── paper3_metadata_default.json
├── aggregate/
│   ├── metadata.json
│   └── study_analysis.json
├── reports/
│   ├── quick_stats.json
│   └── summary.md
└── .litrev_state.json
```