# Writing Prompt Templates

Prompt templates define what information the LLM extracts from each paper. They are `.txt` files referenced in your `litrev.yaml` configuration.

## Template Structure

Each template has two components:

1. **System prompt** (defined in `litrev.yaml`): Sets the LLM's role and behavior
2. **User prompt** (in the `.txt` file): The extraction instructions + JSON schema + `{content}` placeholder

## Placeholder Variables

| Variable | Description |
|----------|-------------|
| `{content}` | The full text of the paper (required) |

The `{content}` placeholder is automatically replaced with the paper text when the pipeline runs. You can optionally truncate content via `content_truncation` in the prompt config.

## Best Practices

### 1. Define the JSON schema explicitly

Always include a `### Target JSON Format` section with a complete example:

```markdown
### Target JSON Format:
{
  "property_name": "not reported",
  "numeric_field": null,
  "list_field": [],
  "bool_field": false
}
```

### 2. Set explicit defaults

For every field, include the "null" value in the schema:
- Strings: `"not reported"`
- Numbers: `null`
- Lists: `[]`
- Booleans: `false`

### 3. Use controlled vocabularies

For categorical fields, provide a controlled list:

```markdown
### Controlled Vocabularies:
- **category**: Must be one of `["option_a", "option_b", "option_c"]`.
```

### 4. Include extraction rules

```markdown
### Mandatory Extraction Rules:
1. **No Guessing**: If information is not explicitly mentioned, use `null` or `"not reported"`.
2. **Evidence Quotes**: For key claims, include the exact sentence as an evidence_quote.
```

### 5. One extraction focus per template

Each template should extract one coherent set of related fields. The original project uses 20 templates for SERS review; a simpler review might use 3-5.

## Example: Bibliographic Metadata

```markdown
You are an expert scientific researcher. Your task is to extract bibliographic metadata from academic papers accurately.

### Mandatory Extraction Rules:
1. **No Guessing**: If information is not explicitly mentioned, use `null` for numbers or `"not reported"` for strings. Never hallucinate data.

### Target JSON Format:
{
  "citation": {
    "title": "not reported",
    "authors": [],
    "year": null,
    "journal": "not reported",
    "doi": "not reported",
    "url": "not reported"
  }
}

### Paper Content:
{content}
```

## Example: Study Analysis

```markdown
You are an expert scientific reviewer. Extract the study design information.

### Controlled Vocabularies:
- **domain**: Must be one of `["biomedical", "environmental", "food_safety", "materials"]`.

### Target JSON Format:
{
  "domain": "not reported",
  "target_analyte": "not reported",
  "sample_matrix": "not reported",
  "sample_size": null,
  "task_type": "not reported"
}

### Paper Content:
{content}
```

## Tips for Reliable JSON Output

1. **Single JSON object**: Request a single top-level JSON object, not a list or nested structure with multiple objects
2. **Avoid empty values**: Give the LLM explicit defaults for every field
3. **Use the right data types**: Booleans (`true`/`false`) are more reliable than string labels for yes/no classifications
4. **Include an example**: LLMs follow the format in the `Target JSON Format` section more reliably when it's near the end
5. **Keep templates focused**: A template that asks for 20+ fields tends to produce more hallucinated values than one asking for 5-8 related fields