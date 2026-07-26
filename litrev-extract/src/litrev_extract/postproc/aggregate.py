"""Aggregate post-processor.

Replaces ``aggregate_results.py`` and ``aggregate_gpt55.py`` from the original
project.  Scans per-document ``derived/`` result files and consolidates them
into one JSON file per prompt.

How it works
    For each prompt, the processor walks all ``.json`` files under the
    ``derived/`` directory, parses the filename to extract the prompt name
    (e.g. ``PMC123456_timeline_evolution_gpt4o.json`` → prompt
    ``timeline_evolution``), loads each result, enriches it with document
    metadata (``document_path``, ``paper_id``), and writes the aggregated
    list to ``aggregate/{prompt_name}.json``.

Example output
    ``aggregate/timeline_evolution.json`` contains::

        [
            {
                "milestone_category": "fabrication",
                "year": "2020",
                "document_path": "PMC123456_timeline_evolution_gpt4o.json",
                "paper_id": "PMC123456"
            },
            ...
        ]
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List

from ..models import ReviewConfig
from ..output import OutputManager
from ..utils.file_utils import relpath, scan_documents
from .base import PostProcessor
from .registry import register_post_processor


@register_post_processor("aggregate")
class AggregateProcessor(PostProcessor):
    """Aggregate per-document extraction results into per-prompt collections.

    This is the first step in the post-processing pipeline.  It consolidates
    individual JSON result files (one per document per prompt) into a single
    JSON array per prompt, enriched with document metadata.

    Attributes
    ----------
    name : str
        Processor identifier (``"aggregate"``), used by the registry.

    Example
    -------
    .. code-block:: yaml

        pipeline:
          - name: aggregate
            module: litrev_extract.postproc.aggregate
            enabled: true
    """

    name = "aggregate"

    def run(self, config: ReviewConfig, model_alias: str) -> Dict[str, int]:
        """Run aggregation for all prompts configured in the review.

        Workflow
        --------
        1. Resolves the ``derived/`` result directory.
        2. Iterates all ``.json`` files under it.
        3. Parses each filename to extract ``prompt_name``.
        4. Skips files whose prompt is not in ``config.prompts``.
        5. Loads JSON content and enriches with ``document_path`` and
           ``paper_id``.
        6. Appends to the per-prompt aggregated list.
        7. Writes each prompt's list to ``aggregate/{prompt_name}.json``.

        Parameters
        ----------
        config : ReviewConfig
            The review configuration, which defines ``output.directory``,
            ``output.result_subdir`` (the ``derived/`` dir name), and the
            list of prompts.
        model_alias : str
            Model identifier used to filter result files (files must end with
            ``_{model_alias}.json``).

        Returns
        -------
        dict of {str: int}
            Mapping from prompt name to record count, e.g.::

                {"timeline_evolution": 25, "ml_model_comparison": 30}
        """
        # Resolve the derived/ output directory
        derived_dir = Path(config.output.directory) / config.output.result_subdir
        if not derived_dir.exists():
            print(f"  [WARN] No results directory found: {derived_dir}")
            return {}

        # Build the set of valid prompt names from the configuration
        prompts = {p.name for p in config.prompts}
        # Sort by name length descending so longer names match first.
        # This prevents a short name like "evolution" from falsely matching
        # a file actually belonging to "timeline_evolution".
        sorted_prompts = sorted(prompts, key=len, reverse=True)
        # Pre-populate dict so every prompt has at least an empty list
        aggregated: Dict[str, List[Dict]] = {p: [] for p in prompts}
        counts: Dict[str, int] = {}

        # Walk all result files recursively
        for result_file in sorted(derived_dir.rglob("*.json")):
            filename = result_file.name

            # Filename format: {paper_id}_{prompt_name}_{model_alias}.json
            # Strategy: iterate known prompt names and look for a match via
            # stem.endswith(f"_{prompt_name}_{model_alias}") which correctly
            # handles multi-word prompt names like "timeline_evolution".
            stem = result_file.stem
            matched_prompt = None
            for pname in sorted_prompts:
                suffix = f"_{pname}_{model_alias}"
                if stem.endswith(suffix):
                    matched_prompt = pname
                    break
                suffix = f"_{pname}_{model_alias}"
                if stem.endswith(suffix):
                    matched_prompt = pname
                    break

            if matched_prompt is None:
                continue

            try:
                with open(result_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                if isinstance(data, dict):
                    # Enrich with document info for traceability
                    data["document_path"] = relpath(
                        str(result_file), str(derived_dir)
                    )
                    # Extract paper_id by removing the known prompt_name + model_alias suffix
                    suffix = f"_{matched_prompt}_{model_alias}"
                    data["paper_id"] = stem[: -len(suffix)]
                    aggregated[matched_prompt].append(data)
                    counts[matched_prompt] = counts.get(matched_prompt, 0) + 1
            except (json.JSONDecodeError, OSError) as e:
                print(f"  [WARN] Skipping {result_file}: {e}")

        # Write one aggregated JSON file per prompt
        output_mgr = OutputManager(config.output, model_alias)
        total = 0
        for prompt_name, records in aggregated.items():
            if not records:
                continue
            out_path = output_mgr.get_aggregate_path(prompt_name)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
            print(f"  Aggregated {len(records)} records -> {out_path}")
            total += len(records)

        print(f"  Total: {total} records across {len(counts)} prompts")
        return counts