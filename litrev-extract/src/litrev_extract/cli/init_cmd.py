"""Scaffold subcommand for ``litrev init``.

Creates a new systematic-literature-review project directory with:

  - ``litrev.yaml``          — default project configuration
  - ``prompts/metadata.txt`` — sample LLM prompt template
  ``documents/sample_paper.md`` — example document for testing
  - ``.gitignore``           — sensible defaults for a litrev project
  - ``scripts/__init__.py``  — placeholder for custom scripts

All boilerplate files are generated from templates defined in this module.
"""

from __future__ import annotations

import os
from pathlib import Path

import click

# ---------------------------------------------------------------------------
# File templates
# ---------------------------------------------------------------------------

# Full YAML configuration produced when ``litrev init <name>`` is run.
# The ``{name}`` placeholder is filled from the CLI argument; double-braced
# placeholders (e.g. ``{{base}}``) survive the first format pass and are
# resolved later by the config loader / output naming logic.
INIT_TEMPLATE = """project:
  name: "{name}"
  description: "A systematic literature review"

input:
  directory: ./documents
  formats: [.md, .txt]
  recursive: true

output:
  directory: ./output
  file_naming:
    pattern: "{{base}}_{{prompt_name}}_{{model_alias}}.json"

models:
  - alias: "default"
    api_key_env: "LLM_API_KEY"
    base_url: "https://api.openai.com/v1"
    model_name: "gpt-4"
    max_concurrent: 3
    max_retries: 10
    retry_delay_base: 2
    rate_limit:
      max_requests: 50
      window_seconds: 60

prompts:
  - name: "metadata"
    id: "v1_metadata"
    file: "prompts/metadata.txt"
    system_prompt: "You are an expert scientific researcher focused on bibliographic data extraction."

postproc:
  pipeline:
    - name: aggregate
      module: "litrev_extract.postproc.aggregate"
      enabled: true
    - name: stats
      module: "litrev_extract.postproc.stats"
      enabled: true
      config:
        sections:
          - prompt: "metadata"
            field: "citation.year"
            type: value_counts
            top_k: 10
    - name: export_csv
      module: "litrev_extract.postproc.export_csv"
      enabled: true
    - name: report_md
      module: "litrev_extract.postproc.report_md"
      enabled: true
      config:
        output_file: "summary_report.md"
        sections:
          - title: "Year Distribution"
            source_prompt: "metadata"
            content: |
              ## Year Distribution
              {{value_counts "metadata" "citation.year"}}
"""

# Default system prompt for the ``metadata`` extraction prompt.
# Instructs the LLM to extract citation fields (title, authors, year, etc.)
# from academic-paper text, with a strict no-guessing rule.
METADATA_PROMPT = """You are an expert scientific researcher. Your task is to extract bibliographic metadata from academic papers accurately.

### Mandatory Extraction Rules:
1. **No Guessing**: If information is not explicitly mentioned, use `null` for numbers or `"not reported"` for strings. Never hallucinate data.

### Target JSON Format:
{
  "citation": {
    "title": "not reported",
    "authors": [],
    "year": null,
    "journal": "not reported",
    "doi": "not reported"
  }
}

### Paper Content:
{content}
"""

# Minimal sample document used to test the extraction pipeline end-to-end.
SAMPLE_PAPER = """# Sample Paper: Machine Learning in Raman Spectroscopy

## Abstract
Machine learning has emerged as a powerful tool for analyzing Raman spectroscopy data. In this review, we survey recent advances in applying deep learning methods to spectroscopic analysis, with a focus on classification and quantification tasks.

## Introduction
Raman spectroscopy provides rich molecular fingerprint information, but the complexity of spectral data often requires sophisticated computational methods for interpretation. Machine learning approaches, particularly deep learning, have shown remarkable success in automating spectral analysis.

## Methods
We reviewed 150 papers published between 2018 and 2024 that applied machine learning to SERS data. Convolutional neural networks (CNNs) were the most common architecture, used in 45% of studies, followed by support vector machines (25%) and random forests (15%).

## Results
The average classification accuracy across studies was 94.2%. Deep learning methods consistently outperformed classical machine learning approaches, with CNNs achieving 96.8% accuracy compared to 89.3% for SVM-based methods.

## Discussion
Machine learning has significantly advanced the field of Raman spectroscopy, enabling automated analysis of complex spectral datasets. Key challenges include the need for larger, more diverse training datasets and improved model interpretability.
"""

# Default ``.gitignore`` that prevents generated output and state files
# from being accidentally committed.
GITIGNORE_TEMPLATE = """# litrev-extract project
output/
.litrev_state.json
litrev.lock
__pycache__/
*.pyc
"""


@click.command()
@click.argument("name")
@click.option(
    "--dir", "-d", "target_dir",
    default=None,
    help="Target directory (default: ./<name>)",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    default=False,
    help="Overwrite existing files",
)
def init(name: str, target_dir: str | None, force: bool) -> None:
    """Scaffold a new litrev-extract review project called NAME.

    Creates a project directory with a default ``litrev.yaml`` configuration,
    sample prompt templates, and a sample document for testing the pipeline.

    Args:
        name: Project name. Used as the directory name when ``--dir`` is not
            provided, and written into the ``litrev.yaml`` ``project.name``
            field.
        target_dir: Explicit target directory. When ``None``, the project is
            created at ``./<name>`` relative to the current working directory.
        force: If ``True``, overwrite any existing files in the target
            directory. If ``False`` and the directory already exists, abort.
    """
    base = Path(target_dir) if target_dir else Path.cwd() / name
    if base.exists() and not force:
        click.echo(f"Error: directory '{base}' already exists. Use --force to overwrite.")
        raise click.Abort()

    # Create all required sub-directories in a single pass.
    dirs = [
        base,
        base / "prompts",
        base / "documents",
        base / "scripts",
        base / "output",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)

    # Write boilerplate files from templates.
    yaml_content = INIT_TEMPLATE.replace("{name}", name)
    _safe_write(base / "litrev.yaml", yaml_content, force)

    _safe_write(base / "prompts" / "metadata.txt", METADATA_PROMPT, force)

    _safe_write(base / "documents" / "sample_paper.md", SAMPLE_PAPER, force)

    _safe_write(base / ".gitignore", GITIGNORE_TEMPLATE, force)

    _safe_write(base / "scripts" / "__init__.py", "", force)

    # Print the "next steps" guide so the user knows what to do after init.
    click.echo(f"\n[OK] Created review project at: {base}")
    click.echo()
    click.echo("Next steps:")
    click.echo(f"  1. cd {base}")
    click.echo("  2. Set your API key:   export LLM_API_KEY=sk-...")
    click.echo("  3. Edit prompts:       prompts/metadata.txt")
    click.echo("  4. Add papers:         documents/")
    click.echo("  5. Run extraction:     litrev run --model default")
    click.echo("  6. Post-process:       litrev postproc --model default")
    click.echo()


def _safe_write(path: Path, content: str, force: bool) -> None:
    """Write a file, skipping if it exists and force is ``False``.

    This helper prevents accidental overwrites of user-edited files when
    re-running ``litrev init`` on an existing project without ``--force``.

    Args:
        path: Target file path to create or overwrite.
        content: UTF-8 text content to write to the file.
        force: When ``True``, overwrite the file even if it already exists.
            When ``False``, skip writing and print a "Skipping" message.
    """
    if path.exists() and not force:
        click.echo(f"  Skipping {path.name} (already exists)")
        return
    path.write_text(content, encoding="utf-8")
    click.echo(f"  Created {path.name}")