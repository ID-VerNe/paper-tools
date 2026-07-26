"""Output file path management.

Replaces hardcoded file path generation in the original project with
configurable naming patterns and directory structures.

The ``OutputManager`` class is the central entry point. It generates paths for
four output categories:
    - **Results**: per-document extraction output stored as JSON.
    - **Aggregates**: per-prompt aggregated results (all documents combined).
    - **Reports**: summary reports and analysis output.
    - **Plots**: per-model CSV data for visualisation.

Directory structure (default)::

    <output_dir>/
    ├── derived/          # Individual extraction results
    │   ├── doc1.json
    │   └── doc2.json
    ├── aggregate/        # Per-prompt aggregates
    │   └── metadata.json
    ├── reports/          # Reports
    │   └── summary.md
    └── plots/            # Plot data CSVs, organised by model
        └── opus/
            └── scores.csv
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from .models import ExtractionTask, OutputConfig


class OutputManager:
    """Manages output file paths and naming.

    The naming pattern for individual result files supports these template
    variables. They are replaced at path-generation time via string
    substitution (not a full template engine):

    ================   =====================================================
    Variable           Description
    ================   -----------------------------------------------------
    ``{base}``         Document filename without extension (e.g. ``paper1``)
    ``{prompt_name}``  Short name of the prompt (e.g. ``metadata``)
    ``{prompt_id}``    Unique prompt identifier (e.g. ``v1_metadata``)
    ``{model_alias}``  Alias for the model used (e.g. ``opus``)
    ``{model_name}``   Full model identifier (e.g. ``claude-opus-4-8``)
    ================   =====================================================

    Two directory structures are supported via ``config.structure``:

    - **flat** (default): All results go into a single ``derived/`` directory.
    - **mirror**: Results mirror the input directory hierarchy so that a
      document at ``input/subdir/doc.md`` produces output at
      ``output/derived/subdir/doc.json``.

    Attributes:
        config:       Output configuration (directory, subdirs, naming pattern).
        model_alias:  Short alias for the current model, used in path templates.
        model_name:   Full model name, used in path templates.

    Example:
        >>> config = OutputConfig(directory="output", file_naming=FileNaming(pattern="{base}_{prompt_name}"))
        >>> mgr = OutputManager(config, model_alias="opus", model_name="claude-opus-4-8")
        >>> task = ExtractionTask(document_path="docs/paper1.md", prompt_def=prompt_def, ...)
        >>> mgr.get_result_path(task)
        'output/derived/paper1_metadata.json'
    """

    def __init__(
        self, config: OutputConfig, model_alias: str, model_name: str = ""
    ):
        """Initialise the output manager.

        Args:
            config:       Output configuration model (directory, subdirs, naming).
            model_alias:  Short alias for the model, used as ``{model_alias}``
                          in naming templates (e.g. ``"opus"``).
            model_name:   Full model name, used as ``{model_name}`` in naming
                          templates (e.g. ``"claude-opus-4-8"``). Defaults to
                          empty string if not provided.
        """
        self.config = config
        self.model_alias = model_alias
        self.model_name = model_name

    def _render_pattern(
        self, pattern: str, task: ExtractionTask, extra: Optional[dict] = None
    ) -> str:
        """Render a naming pattern by substituting template variables.

        The base filename is derived from the task's document path (the file
        name without its extension). Additional key-value pairs in *extra*
        are also substituted, enabling callers to inject ad-hoc variables.

        Args:
            pattern:  Template string containing ``{variable}`` placeholders.
            task:     The extraction task providing document and prompt context.
            extra:    Optional dict of extra variables to substitute beyond the
                      standard set (``base``, ``prompt_name``, ``prompt_id``,
                      ``model_alias``, ``model_name``).

        Returns:
            The pattern string with all recognised variables replaced by their
            values. Unknown ``{variables}`` are left unchanged.
        """
        # Derive the base name from the document file (strip directory and extension)
        base = os.path.splitext(os.path.basename(task.document_path))[0]

        # Build the full variable dictionary: standard vars + extras
        vars = {
            "base": base,
            "prompt_name": task.prompt_def.name,
            "prompt_id": task.prompt_def.id,
            "model_alias": self.model_alias,
            "model_name": self.model_name,
            **(extra or {}),
        }

        # Perform sequential string replacement for each variable
        result = pattern
        for key, val in vars.items():
            result = result.replace("{" + key + "}", str(val))
        return result

    def get_result_path(self, task: ExtractionTask) -> str:
        """Compute the output file path for a single extraction result.

        The path is constructed as::

            <directory>/[<relative_path>/]<result_subdir>/<rendered_pattern>.json

        The ``<relative_path>`` segment is only present in *mirror* mode,
        where it preserves the input file's directory hierarchy, and the
        ``result_subdir`` is placed **inside** the document folder rather than
        at the output root::

            flat:  <directory>/<result_subdir>/<filename>.json
            mirror: <directory>/<relative_dir>/<result_subdir>/<filename>.json

        For multimodal tasks (one task per image), the output is placed in
        the **document** folder's derived/ directory (not in images/derived/).
        The ``{image_stem}`` variable is available in the naming pattern,
        allowing per-image output filenames.

        The parent directory is created on-the-fly if it does not exist.

        Args:
            task: The extraction task for which to generate a result path.

        Returns:
            Absolute or relative path to the output JSON file.
        """
        output_base = Path(self.config.directory)

        if self.config.structure == "mirror":
            # Use the document's relative directory (strip the image path for multimodal)
            if task.prompt_def.multimodal and task.image_path:
                # Strip the 'images/' suffix — use the paper folder as rel_dir
                rel_dir = os.path.dirname(os.path.dirname(task.relative_path))
            else:
                rel_dir = os.path.dirname(task.relative_path)
            output_base = output_base / rel_dir / self.config.result_subdir
        else:
            output_base = output_base / self.config.result_subdir

        # Extra variables for multimodal tasks
        extra = {}
        if task.prompt_def.multimodal and task.image_path:
            extra["image_stem"] = os.path.splitext(os.path.basename(task.image_path))[0]

        # Render the file naming pattern and ensure the directory exists
        filename = self._render_pattern(self.config.file_naming.pattern, task, extra=extra)
        output_base.mkdir(parents=True, exist_ok=True)

        return str(output_base / filename)

    def get_aggregate_path(self, prompt_name: str) -> str:
        """Compute the output path for an aggregated per-prompt results file.

        Aggregated files collect results for the same prompt across all
        documents. They are stored in the *aggregate* subdirectory with the
        prompt name as the file name.

        Args:
            prompt_name: Short name of the prompt (e.g. ``"metadata"``).

        Returns:
            Path to the aggregate JSON file, e.g.
            ``output/aggregate/metadata.json``.
        """
        path = Path(self.config.directory) / self.config.aggregate_subdir
        path.mkdir(parents=True, exist_ok=True)
        return str(path / f"{prompt_name}.json")

    def get_report_path(self, filename: str) -> str:
        """Compute the output path for a report file.

        Reports are free-form output files (summary, analysis, etc.) stored
        in the *reports* subdirectory.

        Args:
            filename: Desired file name including extension (e.g.
                      ``"quality_report.md"``).

        Returns:
            Path to the report file, e.g. ``output/reports/quality_report.md``.
        """
        path = Path(self.config.directory) / self.config.report_subdir
        path.mkdir(parents=True, exist_ok=True)
        return str(path / filename)

    def get_plot_path(
        self, filename: str, model_alias: Optional[str] = None
    ) -> str:
        """Compute the output path for a plot CSV file.

        Plot data is organised by model alias under the *plots* subdirectory,
        which keeps per-model visualisation data isolated::

            plots/<model_alias>/<filename>

        Args:
            filename:     Desired file name, e.g. ``"scores.csv"``.
            model_alias:  Optional model alias override. If ``None``, defaults
                          to ``self.model_alias`` from the constructor.

        Returns:
            Path to the plot CSV file, e.g.
            ``output/plots/opus/scores.csv``.
        """
        ma = model_alias or self.model_alias
        path = Path(self.config.directory) / self.config.plot_subdir / ma
        path.mkdir(parents=True, exist_ok=True)
        return str(path / filename)