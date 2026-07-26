"""Markdown report generator post-processor.

Replaces ``generate_detailed_md.py`` from the original project with a
templated approach.  Generates structured markdown reports from aggregated
extraction data using configurable sections with inline template commands.

Template commands
    The report content is plain markdown with ``{{command}}`` placeholders::

        {{value_counts "prompt_name" "field.path"}}  — top-10 value counts
        {{count_true "prompt_name" "field.path"}}    — count of truthy values
        {{mean "prompt_name" "field.path"}}          — mean/min/max of a numeric field
        {{count "prompt_name"}}                      — total entries for a prompt

    Commands that span unrecognised tokens are left unmodified in the output,
    making it safe to use ``{{}}`` in non-template contexts.

Architecture
    The :class:`TemplateRenderer` class parses and resolves template commands
    using ``re.sub`` with a callback.  Each render function
    (:func:`_render_value_counts`, :func:`_render_mean`, etc.) loads the
    required aggregated data and formats the result as markdown.
"""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Match, Optional

from ..models import ReviewConfig
from ..output import OutputManager
from .base import PostProcessor
from .registry import register_post_processor
from .stats import _find_keys, _process_values, _count_true


def _render_value_counts(data: list, field: str, top_k: int = 10) -> str:
    """Render the top-*k* value counts as a markdown unordered list.

    Each item shows the value (bold), raw count, and percentage of total.

    Parameters
    ----------
    data : list
        List of records to analyse.
    field : str
        Field path to count (supports dot-notation).
    top_k : int, optional
        Maximum number of items to include (default 10).

    Returns
    -------
    str
        Rendered markdown, e.g.::

            - **fabrication**: 15 (45.5%)
            - **simulation**: 10 (30.3%)
            - (no data)
    """
    values = _find_keys(data, [field])
    cleaned = _process_values(values)
    if not cleaned:
        return "  (no data)"
    counter = Counter(cleaned).most_common(top_k)
    lines = []
    for val, count in counter:
        pct = count / len(cleaned) * 100
        lines.append(f"  - **{val}**: {count} ({pct:.1f}%)")
    return "\n".join(lines)


def _render_mean(data: list, field: str) -> str:
    """Render mean, min, max, and count for a numeric field.

    Parameters
    ----------
    data : list
        List of records to analyse.
    field : str
        Field path (supports dot-notation).

    Returns
    -------
    str
        Rendered markdown, e.g.::

            Mean: 2021.45 (n=20, min=2015.00, max=2025.00)
    """
    values = _find_keys(data, [field])
    numeric = []
    for v in values:
        try:
            numeric.append(float(v))
        except (ValueError, TypeError):
            pass
    if not numeric:
        return "  (no numeric data)"
    avg = sum(numeric) / len(numeric)
    return f"  Mean: {avg:.2f} (n={len(numeric)}, min={min(numeric):.2f}, max={max(numeric):.2f})"


def _render_count_true(data: list, field: str) -> str:
    """Render the count (and percentage) of entries with a truthy field value.

    Parameters
    ----------
    data : list
        List of records to analyse.
    field : str
        Field path(s) — comma-separated keys are treated as an OR condition.

    Returns
    -------
    str
        Rendered markdown, e.g.::

            **35** / 50 (70.0%)
    """
    keys = [k.strip() for k in field.split(",")] if "," in field else [field]
    count = _count_true(data, [k.strip() for k in keys])
    total = len(data)
    pct = count / total * 100 if total > 0 else 0
    return f"  **{count}** / {total} ({pct:.1f}%)"


def _render_count(data: list) -> str:
    """Render the total number of entries in the data.

    Parameters
    ----------
    data : list
        List of records.

    Returns
    -------
    str
        Rendered markdown, e.g.::

            Total entries: **25**
    """
    return f"  Total entries: **{len(data)}**"


class TemplateRenderer:
    """Processes markdown template sections and resolves ``{{command}}`` blocks.

    The renderer caches loaded aggregated data so that multiple template
    commands referencing the same prompt only perform one disk read.

    Attributes
    ----------
    aggregate_dir : Path
        Directory containing per-prompt aggregate JSON files.
    _cache : dict of {str: list}
        Prompt-name → parsed JSON data, populated lazily.

    Example
    -------
    .. code-block:: python

        renderer = TemplateRenderer(Path("output/aggregate"))
        result = renderer.render_section(
            "## Year Distribution\\n{{value_counts \"metadata\" \"citation.year\"}}"
        )
    """

    def __init__(self, aggregate_dir: Path):
        """Initialise the renderer with a path to aggregate data.

        Parameters
        ----------
        aggregate_dir : Path
            Directory containing ``{prompt_name}.json`` files.
        """
        self.aggregate_dir = aggregate_dir
        # Cache: prompt_name → parsed JSON list (avoids re-reading the same file)
        self._cache: Dict[str, list] = {}

    def _load_data(self, prompt_name: str) -> list:
        """Load aggregated data for a prompt, using a cache to avoid re-reads.

        Parameters
        ----------
        prompt_name : str
            Name of the prompt whose data to load.

        Returns
        -------
        list
            Parsed JSON array, or empty list if the file is missing or
            unreadable.
        """
        if prompt_name not in self._cache:
            path = self.aggregate_dir / f"{prompt_name}.json"
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        self._cache[prompt_name] = json.load(f)
                except (json.JSONDecodeError, OSError):
                    self._cache[prompt_name] = []
            else:
                self._cache[prompt_name] = []
        return self._cache[prompt_name]

    def render_section(self, content: str) -> str:
        """Render a section template by resolving all ``{{command}}`` blocks.

        Parses ``{{command "prompt_name" "field.path"}}`` patterns using a
        regex and replaces each with its rendered markdown output.  Unknown
        commands are left in place.

        Parameters
        ----------
        content : str
            Raw section content, possibly containing template commands.

        Returns
        -------
        str
            Content with all recognised template commands replaced by their
            rendered markdown output.
        """

        def _resolve(m: Match) -> str:
            """Resolve a single ``{{...}}`` match to rendered markdown."""
            full = m.group(1).strip()
            parts = full.split()
            if not parts:
                return ""

            cmd = parts[0]

            # --- value_counts ---
            if cmd == "value_counts" and len(parts) >= 3:
                prompt_name = parts[1].strip('"').strip("'")
                field = parts[2].strip('"').strip("'")
                data = self._load_data(prompt_name)
                return _render_value_counts(data, field)

            # --- count_true ---
            if cmd == "count_true" and len(parts) >= 3:
                prompt_name = parts[1].strip('"').strip("'")
                field = parts[2].strip('"').strip("'")
                data = self._load_data(prompt_name)
                return _render_count_true(data, field)

            # --- mean ---
            if cmd == "mean" and len(parts) >= 3:
                prompt_name = parts[1].strip('"').strip("'")
                field = parts[2].strip('"').strip("'")
                data = self._load_data(prompt_name)
                return _render_mean(data, field)

            # --- count ---
            if cmd == "count" and len(parts) >= 2:
                prompt_name = parts[1].strip('"').strip("'")
                data = self._load_data(prompt_name)
                return _render_count(data)

            # Unrecognised command → pass through unchanged
            return m.group(0)

        # Match {{...}} — non-greedy inside to handle multiple commands per section
        return re.sub(r"\{\{(.+?)\}\}", _resolve, content)


@register_post_processor("report_md")
class MarkdownReportProcessor(PostProcessor):
    """Generate templated markdown reports from aggregated extraction data.

    The report is defined in ``litrev.yaml`` as a series of *sections*, each
    with a *title* and markdown *content* that may include
    :ref:`template-commands`.

    Attributes
    ----------
    name : str
        Processor identifier (``"report_md"``).
    config : dict
        Expects ``config["sections"]`` — a list of section dicts, each with:
        ``title``, ``content``, and optionally ``source_prompt``.

    Example YAML config
    -------------------
    .. code-block:: yaml

        pipeline:
          - name: report_md
            module: litrev_extract.postproc.report_md
            enabled: true
            config:
              output_file: "summary_report.md"
              sections:
                - title: "Year Distribution"
                  source_prompt: "metadata"
                  content: |
                    ## Year Distribution
                    {{value_counts "metadata" "citation.year"}}
                - title: "Timeline"
                  source_prompt: "timeline_evolution"
                  content: |
                    ## Timeline
                    {{value_counts "timeline_evolution" "milestone_category"}}
    """

    name = "report_md"

    def run(self, config: ReviewConfig, model_alias: str) -> str | None:
        """Generate the markdown report from configured sections.

        Workflow
        --------
        1. Reads ``sections`` from ``self.config``.
        2. Creates a :class:`TemplateRenderer` pointed at the aggregate dir.
        3. Builds the markdown output by concatenating the report header with
           each rendered section.
        4. Writes the result to the configured *output_file* via
           :class:`litrev_extract.output.OutputManager`.

        Parameters
        ----------
        config : ReviewConfig
            The review configuration (used to resolve output directories and
            the project name).
        model_alias : str
            Model identifier (used to resolve the aggregate subdirectory and
            output paths).

        Returns
        -------
        str or None
            The full rendered report text, or ``None`` if no output file is
            configured.
        """
        sections = self.config.get("sections", [])
        output_file = self.config.get("output_file", "summary_report.md")

        # Resolve the directory containing per-prompt aggregate JSON files
        aggregate_dir = Path(config.output.directory) / config.output.aggregate_subdir
        renderer = TemplateRenderer(aggregate_dir)

        # Build the report header
        lines = [
            f"# {config.project_name} -- Extraction Report",
            f"Model: `{model_alias}` | Generated automatically\n",
        ]

        # Render each configured section
        for section in sections:
            title = section.get("title", "Section")
            content = section.get("content", "")

            lines.append(f"## {title}")
            rendered = renderer.render_section(content)
            lines.append(rendered)
            lines.append("")

        report_text = "\n".join(lines)

        # Write the report to disk
        output_mgr = OutputManager(config.output, model_alias)
        out_path = output_mgr.get_report_path(output_file)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(report_text)
        print(f"  Report generated → {out_path}")

        return report_text