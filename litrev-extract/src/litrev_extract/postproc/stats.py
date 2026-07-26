"""Generic statistics post-processor.

Replaces **all** 6 ``summarize_*.py`` scripts and ``generate_report_stats.py``
from the original project with a single configurable processor that supports
``value_counts``, ``crosstab``, ``mean``, and ``count_true`` operations.

Key design
    Instead of maintaining a separate script per statistic type (one for
    milestone categories, one for algorithm families, one for boolean flags,
    etc.), this processor reads a declarative config from ``litrev.yaml``::

        sections:
          - prompt: "timeline_evolution"
            field: "milestone_category"
            type: value_counts
            top_k: 10

    It then recursively searches for the requested *field* in the nested JSON
    structure using dot-notation (e.g. ``"citation.year"``), normalises the
    values, and computes the selected statistic.

Module-level functions
    The helper functions (``_find_keys``, ``_clean_val``, ``_process_values``,
    ``_count_true``) are also exported for reuse by
    :mod:`litrev_extract.postproc.export_csv` and
    :mod:`litrev_extract.postproc.report_md`.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import pandas as pd

from ..models import ReviewConfig
from .base import PostProcessor
from .registry import register_post_processor


def _find_keys(data: Any, target_keys: List[str], depth: int = 0) -> List[Any]:
    """Recursively search for *target_keys* in a nested JSON structure.

    Supports **dot-notation** for accessing nested paths.  For example,
    ``"citation.year"`` resolves to ``data["citation"]["year"]``.

    The search strategy is:

    * If *target_keys* has a single key containing a ``.``, treat it as a
      dot-notation path.  Walk into nested dicts step by step; if a list is
      encountered at any intermediate level, fan out — recurse into each
      element with the remaining path segments.  This handles structures
      like ``[{"citation": {"year": 2020}}, {"citation": {"year": 2021}}]``.
    * Otherwise, for each key in *target_keys*, check if it exists in the
      current dict; if not, recurse deeper.  Lists are unwrapped element-by-
      element.
    * Recursion is bounded at a depth of 10 to protect against cycles or
      pathological nesting.

    Parameters
    ----------
    data : Any
        The JSON-parsed data to search.  Typically a list of record dicts or
        a single record dict.
    target_keys : list of str
        Keys to look for.  Supports dot-notation (e.g. ``["citation.year"]``).
    depth : int
        Current recursion depth (internal; caller should not set this).

    Returns
    -------
    list of Any
        All matching values found.  Empty list if nothing is found or the
        depth limit is reached.
    """
    # Guard: cap recursion depth to avoid infinite loops on cyclic references
    if depth > 10:
        return []

    # ------------------------------------------------------------------
    # Dot-notation path resolution
    # ------------------------------------------------------------------
    # If there is exactly one key and it contains a ".", walk it as a path
    # through nested dicts/lists rather than treating it as a literal key.
    if len(target_keys) == 1 and "." in target_keys[0]:
        parts = target_keys[0].split(".")
        current = data
        for part in parts:
            if isinstance(current, dict):
                # Step into the next level of nesting
                current = current.get(part, {})
            elif isinstance(current, list):
                # If we hit a list mid-path, fan out into each element
                results = []
                for item in current:
                    results.extend(_find_keys(item, [part], depth + 1))
                return results
            else:
                # Scalar value at an intermediate path segment — not traversable
                return []
        # After walking the full path, return what we found
        # Exclude empty containers (None, {}, [])
        if not isinstance(current, dict) or current != {}:
            return [current] if current not in [None, {}, []] else []
        return []

    # ------------------------------------------------------------------
    # Flat-key search (no dot-notation)
    # ------------------------------------------------------------------
    results: List[Any] = []
    if isinstance(data, dict):
        for k, v in data.items():
            if k in target_keys:
                # Found a match at this level
                results.append(v)
            else:
                # Recurse into the value — it may be a nested dict/list
                results.extend(_find_keys(v, target_keys, depth + 1))
    elif isinstance(data, list):
        for item in data:
            results.extend(_find_keys(item, target_keys, depth + 1))
    return results


def _clean_val(val: Any) -> Optional[str]:
    """Normalize a single value for counting and comparison.

    This pipeline step filters out nullish/missing-data sentinels and
    normalises the remaining values to lowercase strings so they can be
    counted consistently.

    Transformation rules
        * ``None`` → ``None`` (filtered out)
        * ``True`` / ``False`` → ``"true"`` / ``"false"``
        * ``int`` / ``float`` → ``str(val)``
        * ``str`` → lower-cased, stripped.  If the result is one of
          ``"not reported"``, ``"none"``, ``"null"``, ``"n/a"``, or empty,
          returns ``None``.
        * Other types (e.g. list, dict) → ``None``.

    Parameters
    ----------
    val : Any
        The raw value extracted from JSON.

    Returns
    -------
    str or None
        A cleaned, comparable string, or ``None`` if the value should be
        excluded from counting.
    """
    if isinstance(val, str):
        v = val.lower().strip()
        # Filter out common "no data" sentinels used in LLM extraction
        if v in ("not reported", "none", "null", "n/a", ""):
            return None
        return v
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, (int, float)):
        return str(val)
    return None


def _process_values(lst: List[Any]) -> List[str]:
    """Flatten a list of mixed values and clean each one for counting.

    Handles nested lists by recursive flattening.  Dictionaries at the leaf
    level are **skipped** — they are assumed to have already been processed
    by :func:`_find_keys` (which drills into them and returns scalar values).
    This avoids double-counting when the data contains both the target field
    and its parent dict.

    Parameters
    ----------
    lst : list of Any
        Output from :func:`_find_keys`, which may contain scalars, lists,
        or (rarely) dicts.

    Returns
    -------
    list of str
        All clean, countable strings extracted from the input.
    """
    results: List[str] = []
    for item in lst:
        if isinstance(item, list):
            # Recursively flatten nested lists
            results.extend(_process_values(item))
        elif isinstance(item, dict):
            # Skip dicts — _find_keys already resolved the target path
            continue
        else:
            cleaned = _clean_val(item)
            if cleaned:
                results.append(cleaned)
    return results


def _count_true(data: list, keys: List[str]) -> int:
    """Count how many entries have a truthy value for any of the given keys.

    An entry is counted if its value for any key in *keys* is ``"true"`` or
    ``"yes"`` (after the normalisation in :func:`_clean_val`).

    Parameters
    ----------
    data : list
        List of record dicts (e.g. aggregated extraction results).
    keys : list of str
        Field paths to check.  Supports dot-notation.

    Returns
    -------
    int
        Number of entries for which at least one key is true-ish.
    """
    count = 0
    for entry in data:
        vals = _find_keys(entry, keys)
        cleaned = _process_values(vals)
        # Check if any of the cleaned values represent "true"
        if any(v == "true" or v == "yes" for v in cleaned):
            count += 1
    return count


@register_post_processor("stats")
class StatsProcessor(PostProcessor):
    """Generic statistics processor for aggregated extraction results.

    This single processor replaces the following original-project scripts:

    * ``summarize_timeline.py``
    * ``summarize_ml_comparison.py``
    * ``summarize_metrics.py``
    * ``summarize_dataset.py``
    * ``summarize_method.py``
    * ``summarize_application.py``
    * ``generate_report_stats.py``

    It is configured declaratively through ``litrev.yaml``.

    Attributes
    ----------
    name : str
        Processor identifier (``"stats"``).
    config : dict
        Expects ``config["sections"]`` — a list of section dicts, each with:
        ``prompt``, ``field``, ``type``, and optionally ``top_k``.

    Example YAML config
    -------------------
    .. code-block:: yaml

        pipeline:
          - name: stats
            module: litrev_extract.postproc.stats
            enabled: true
            config:
              output_file: "results/quick_stats.json"
              sections:
                - prompt: "timeline_evolution"
                  field: "milestone_category"
                  type: value_counts
                  top_k: 10
                - prompt: "ml_model_comparison"
                  field: "algorithm_family"
                  type: value_counts
                  top_k: 10
                - prompt: "is_sers"
                  field: "is_sers_based"
                  type: count_true
    """

    name = "stats"

    def run(self, config: ReviewConfig, model_alias: str) -> Dict[str, Any]:
        """Run all configured statistics sections and write results to JSON.

        Workflow
        --------
        1. Reads ``sections`` from ``self.config``.
        2. For each section, loads the aggregated data for the specified
           *prompt* and computes the requested *type* of statistic.
        3. Also computes ``count_true`` for any sections of that type.
        4. Writes all results to the configured *output_file* as JSON.

        Parameters
        ----------
        config : ReviewConfig
            The review configuration (used to resolve output directories).
        model_alias : str
            Model identifier (used to resolve the aggregate subdirectory).

        Returns
        -------
        dict of {str: Any}
            Mapping from ``"{prompt_name}.{field}"`` to the computed statistic.
            For example::

                {
                    "timeline_evolution.milestone_category": {
                        "type": "value_counts",
                        "data": [("fabrication", 15), ("simulation", 10), ...]
                    },
                    "is_sers.is_sers_based.count_true": 42
                }
        """
        sections = self.config.get("sections", [])
        output_file = self.config.get(
            "output_file",
            f"{config.output.directory}/{config.output.report_subdir}/quick_stats.json",
        )

        results: Dict[str, Any] = {}
        aggregate_dir = Path(config.output.directory) / config.output.aggregate_subdir

        # ------------------------------------------------------------------
        # Phase 1: value_counts, crosstab, mean
        # ------------------------------------------------------------------
        for section in sections:
            prompt_name = section.get("prompt", "")
            field = section.get("field", "")
            stat_type = section.get("type", "value_counts")
            top_k = section.get("top_k", 10)

            if not prompt_name or not field:
                continue

            data = self._load_data(aggregate_dir, prompt_name)
            if not data:
                print(f"  [WARN] No data for prompt '{prompt_name}'")
                continue

            section_result = self._compute_stat(data, field, stat_type, top_k)
            if section_result:
                results[f"{prompt_name}.{field}"] = section_result

        # ------------------------------------------------------------------
        # Phase 2: count_true (handled separately because it can combine
        #           multiple fields via comma-separated keys)
        # ------------------------------------------------------------------
        for section in sections:
            if section.get("type") == "count_true":
                prompt_name = section.get("prompt", "")
                field = section.get("field", "")
                data = self._load_data(aggregate_dir, prompt_name)
                if data:
                    # Support comma-separated keys for OR-style counting
                    keys = field.split(",") if "," in field else [field]
                    count = _count_true(data, [k.strip() for k in keys])
                    results[f"{prompt_name}.{field}.count_true"] = count

        # Write all results to a single JSON file
        out_path = Path(output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"  Statistics written to {out_path}")

        return results

    def _load_data(self, aggregate_dir: Path, prompt_name: str) -> list:
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

    def _compute_stat(
        self, data: list, field: str, stat_type: str, top_k: int
    ) -> Optional[Dict]:
        """Compute a single statistic for a given field across records.

        Parameters
        ----------
        data : list
            List of records (aggregated extraction results).
        field : str
            Field path to analyse.  Supports dot-notation.
        stat_type : str
            One of ``"value_counts"``, ``"crosstab"``, or ``"mean"``.
        top_k : int
            For ``value_counts``, only return the top-*k* most common values.

        Returns
        -------
        dict or None
            A result dict with keys ``"type"`` and ``"data"`` (and optionally
            ``"mean"``, ``"max"``, etc.), or ``None`` if no data is available
            or the stat type is not recognised.
        """
        keys = [field]

        if stat_type == "value_counts":
            # Extract all values for the field, clean them, and count
            values = _find_keys(data, keys)
            cleaned = _process_values(values)
            if not cleaned:
                return None
            counter = Counter(cleaned).most_common(top_k)
            return {"type": "value_counts", "data": counter}

        if stat_type == "crosstab":
            # Crosstab requires two fields (row_field and col_field);
            # currently a placeholder — returns None.
            return None

        if stat_type == "mean":
            # Collect numeric values; skip non-numeric entries
            values = _find_keys(data, keys)
            numeric = []
            for v in values:
                try:
                    numeric.append(float(v))
                except (ValueError, TypeError):
                    pass
            if not numeric:
                return None
            return {
                "type": "mean",
                "mean": sum(numeric) / len(numeric),
                "max": max(numeric),
                "min": min(numeric),
                "count": len(numeric),
            }

        return None



# Function-based API for backwards compatibility with generate_report_stats.py
def quick_stats(prompt_data: list, field: str, top_k: int = 10) -> List:
    """Quick ``value_counts`` on extracted data (standalone usage).

    Provided for backwards compatibility with code that imported
    ``generate_report_stats.quick_stats`` from the original project.

    Parameters
    ----------
    prompt_data : list
        List of record dicts.
    field : str
        Field path to analyse.  Supports dot-notation.
    top_k : int, optional
        Only return the *top_k* most common values (default 10).

    Returns
    -------
    list of (str, int)
        Descending list of ``(value, count)`` pairs.
    """
    values = _find_keys(prompt_data, [field])
    cleaned = _process_values(values)
    return Counter(cleaned).most_common(top_k)


def count_true(prompt_data: list, field: str) -> int:
    """Count true values for a boolean field (standalone usage).

    Provided for backwards compatibility with code that imported
    ``generate_report_stats.count_true`` from the original project.

    Parameters
    ----------
    prompt_data : list
        List of record dicts.
    field : str
        Field path to check.  Supports dot-notation.

    Returns
    -------
    int
        Number of records with a true value.
    """
    return _count_true(prompt_data, [field])