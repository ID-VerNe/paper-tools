"""CSV export post-processor.

Replaces ``generate_plot_data.py`` from the original project.  Generates CSV
files from aggregated extraction results with configurable field mappings,
supporting:

* **Field selection** — choose which nested fields to include and rename them
  with aliases.
* **Metadata enrichment** — join data from other prompts (e.g. attach
  ``doi`` / ``year`` / ``title`` from a ``metadata`` prompt to a
  ``timeline_evolution`` export).

The output CSVs are designed to be consumed directly by plotting tools
(``matplotlib``, ``pandas``, etc.).

Example YAML config
    .. code-block:: yaml

        pipeline:
          - name: export_csv
            module: litrev_extract.postproc.export_csv
            enabled: true
            config:
              exports:
                - name: "timeline"
                  prompt: "timeline_evolution"
                  fields:
                    - source: "citation.year"
                      alias: "year"
                    - source: "milestone_category"
                      alias: "milestone_category"
                  enrich_from:
                    - "metadata"
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from ..models import ReviewConfig
from ..output import OutputManager
from .base import PostProcessor
from .registry import register_post_processor
from .stats import _find_keys


@register_post_processor("export_csv")
class CsvExportProcessor(PostProcessor):
    """Export fields from aggregated extraction results as CSV files.

    Each export configuration defines a *prompt* to read, a set of *fields*
    (each with an optional *alias* for the column name), and optional
    *enrich_from* prompts whose metadata (DOI, year, title) is joined onto
    every row.

    Attributes
    ----------
    name : str
        Processor identifier (``"export_csv"``).
    config : dict
        Expects ``config["exports"]`` — a list of export definitions.

    Example
    -------
    .. code-block:: python

        # Instantiated and run by the pipeline; standalone usage:
        proc = CsvExportProcessor()
        proc.config = {
            "exports": [{
                "name": "timeline",
                "prompt": "timeline_evolution",
                "fields": [
                    {"source": "year", "alias": "year"},
                ],
            }]
        }
        proc.run(config, "gpt4o")
    """

    name = "export_csv"

    def run(self, config: ReviewConfig, model_alias: str) -> Dict[str, int]:
        """Run all configured CSV exports.

        For each export definition in ``self.config["exports"]``:

        1. Load aggregated data for the specified prompt.
        2. Build one row per entry by extracting each requested field via
           :func:`litrev_extract.postproc.stats._find_keys`.
        3. Optionally enrich rows with metadata from other prompts.
        4. Write the resulting ``pandas.DataFrame`` to CSV via
           :meth:`OutputManager.get_plot_path`.

        Parameters
        ----------
        config : ReviewConfig
            The review configuration (used to resolve output directories).
        model_alias : str
            Model identifier (used to resolve the aggregate subdirectory and
            output paths).

        Returns
        -------
        dict of {str: int}
            Mapping from export name to number of rows written, e.g.::

                {"timeline": 25, "methods": 30}
        """
        exports = self.config.get("exports", [])
        aggregate_dir = Path(config.output.directory) / config.output.aggregate_subdir
        output_mgr = OutputManager(config.output, model_alias)
        results: Dict[str, int] = {}

        for export_cfg in exports:
            name = export_cfg.get("name", "unnamed")
            prompt_name = export_cfg.get("prompt", "")
            fields = export_cfg.get("fields", [])
            enrich_from = export_cfg.get("enrich_from", [])

            # Load the aggregated data for this prompt
            data = self._load_aggregated(aggregate_dir, prompt_name)
            if not data:
                print(f"  [WARN] No data for export '{name}' (prompt: {prompt_name})")
                continue

            # Build one dict per entry with the requested fields
            rows = []
            for entry in data:
                row = {}
                for f in fields:
                    source = f.get("source", "")
                    alias = f.get("alias", source)
                    vals = _find_keys(entry, [source])
                    # Use the first non-null value found (skip sentinels)
                    for v in vals:
                        if v not in (None, "not reported", "null", ""):
                            row[alias] = v
                            break
                # Attach the document identifier for traceability
                pid = entry.get("document_path", entry.get("paper_id", ""))
                row["paper_id"] = pid
                rows.append(row)

            if not rows:
                continue

            # Join metadata from other prompts (DOI, year, title, etc.)
            if enrich_from:
                self._enrich_rows(rows, aggregate_dir, enrich_from, data)

            # Write to CSV via pandas
            df = pd.DataFrame(rows)
            csv_path = output_mgr.get_plot_path(f"{name}.csv", model_alias)
            df.to_csv(csv_path, index=False)
            print(f"  Exported {len(rows)} rows -> {csv_path}")
            results[name] = len(rows)

        return results

    def _load_aggregated(self, aggregate_dir: Path, prompt_name: str) -> list:
        """Load aggregated JSON data for a prompt from disk.

        Parameters
        ----------
        aggregate_dir : Path
            Directory containing per-prompt aggregate JSON files.
        prompt_name : str
            Name of the prompt whose data to load.

        Returns
        -------
        list
            Parsed JSON array, or empty list if the file is missing or
            unreadable.
        """
        path = aggregate_dir / f"{prompt_name}.json"
        if not path.exists():
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def _enrich_rows(
        self,
        rows: List[Dict],
        aggregate_dir: Path,
        enrich_from: List[str],
        current_data: list,
    ) -> None:
        """Enrich rows with metadata from other prompts (DOI, year, etc.).

        Joins metadata from one or more "metadata-style" prompts onto each
        row, keyed by ``paper_id`` / ``document_path``.  For example, this
        allows attaching ``citation.year`` and ``citation.doi`` to each row
        of a timeline export.

        The method modifies *rows* **in place**.

        Parameters
        ----------
        rows : list of dict
            The rows to enrich (modified in-place by calling ``dict.update``).
        aggregate_dir : Path
            Directory containing per-prompt aggregate JSON files.
        enrich_from : list of str
            Prompt names whose data should be used as the metadata source.
        current_data : list
            The current prompt's data (used to build the paper-id mapping
            for the current rows).
        """
        # Build a paper_id → metadata dict by scanning the enrichment prompts
        meta_map: Dict[str, dict] = {}
        for prompt_name in enrich_from:
            data = self._load_aggregated(aggregate_dir, prompt_name)
            for entry in data:
                pid = entry.get("document_path", entry.get("paper_id", ""))
                if pid not in meta_map:
                    meta_map[pid] = {}
                # Collect known metadata keys
                for key in ("year", "doi", "title", "first_author", "author"):
                    # Check both top-level and nested citation.KEY
                    val = entry.get(key, entry.get("citation", {}).get(key))
                    if val and val not in ("not reported", None, ""):
                        meta_map[pid][key] = val

        # Apply the metadata to each row
        for row in rows:
            pid = row.get("paper_id", "")
            if pid in meta_map:
                row.update(meta_map[pid])