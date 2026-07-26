"""``litrev run`` subcommand — execute the LLM extraction pipeline.

This module provides the ``run`` Click command, which:

  1. Loads the project configuration (``litrev.yaml`` by default).
  2. Selects an LLM model alias (first config model, or the ``--model`` value).
  3. Optionally resets state / deletes prior results for that model (``--reset``).
  4. Optionally enumerates tasks without executing them (``--dry-run``).
  5. Initialises and runs the extraction ``Pipeline``, which sends
     each document x prompt combination to the LLM and saves results
     incrementally.

Environment variables, CLI flags, and config-file values interact as follows:

  - ``--config`` overrides the default ``litrev.yaml`` path.
  - ``--model`` overrides the default (first configured) model.
  - ``--workers`` overrides the model's ``max_concurrent`` setting.
  - The ``--burn`` flag disables result persistence (for token-count testing).
"""

from __future__ import annotations

import logging
import os
import re

import click

from ..config import ConfigLoader
from ..pipeline import Pipeline


@click.command()
@click.option(
    "--model", "-m",
    default=None,
    help="Model alias to use (default: first model in config)",
)
@click.option(
    "--workers", "-w",
    type=int,
    default=None,
    help="Max concurrent workers (default: from model config)",
)
@click.option(
    "--config", "-c",
    "config_path",
    default="litrev.yaml",
    show_default=True,
    help="Path to litrev.yaml config file",
)
@click.option(
    "--burn",
    is_flag=True,
    default=False,
    help="Burn mode: process without saving any results (for token testing)",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Enumerate tasks without executing them",
)
@click.option(
    "--reset",
    is_flag=True,
    default=False,
    help="Reset state and delete previous results for the selected model",
)
@click.option(
    "--resume-failed",
    is_flag=True,
    default=False,
    help="Reset only failed tasks for the selected model, then re-run them",
)
@click.option(
    "--list-failed",
    is_flag=True,
    default=False,
    help="List all failed tasks without modifying state or running",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Enable debug logging",
)
def run(
    model: str | None,
    workers: int | None,
    config_path: str,
    burn: bool,
    dry_run: bool,
    reset: bool,
    resume_failed: bool,
    list_failed: bool,
    verbose: bool,
) -> None:
    """Run the extraction pipeline.

    Reads the ``litrev.yaml`` config, scans input documents, and sends
    each document x prompt combination to the configured LLM model.
    Results are saved incrementally so the pipeline can be resumed after
    interruptions.

    Args:
        model: Model alias override. When ``None``, the first model listed
            in the config is used.
        workers: Max concurrent LLM API calls. When ``None``, the model
            configuration's ``max_concurrent`` is used.
        config_path: Path to the ``litrev.yaml`` configuration file.
        burn: When ``True``, results are discarded after extraction (useful
            for estimating token usage without persisting output).
        dry_run: When ``True``, enumerates tasks (documents x prompts) and
            prints them without executing any LLM calls.
        reset: When ``True``, clears the state file and deletes all
            previously saved result files for the selected model before
            starting the pipeline.
        resume_failed: When ``True``, removes ONLY failed-task entries from
            the state file, then runs the pipeline so they get re-processed.
            Previously successful tasks are left untouched.
        list_failed: When ``True``, prints all failed tasks for the model
            and exits without modifying state or running the pipeline.
        verbose: When ``True``, sets HTTPX / OpenAI / HTTPCore loggers to
            ``DEBUG`` level.
    """
    # ------------------------------------------------------------------
    # Logging setup
    # ------------------------------------------------------------------
    # LLM client libraries are noisy at DEBUG; we only enable them when
    # the user explicitly passes --verbose.
    log_level = logging.DEBUG if verbose else logging.WARNING
    logging.getLogger("httpx").setLevel(log_level)
    logging.getLogger("openai").setLevel(log_level)
    logging.getLogger("httpcore").setLevel(log_level)

    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    if not os.path.exists(config_path):
        click.echo(f"Error: config file not found: {config_path}")
        click.echo("Run `litrev init my-review` to create a new project.")
        raise click.Abort()

    config = ConfigLoader.from_file(config_path)

    # ------------------------------------------------------------------
    # Model alias resolution
    # ------------------------------------------------------------------
    # If --model is not provided, fall back to the first model entry in
    # the config.  This keeps the common case (single-model projects) simple.
    model_alias = model
    if not model_alias:
        if config.models:
            model_alias = config.models[0].alias
            click.echo(f"Using first configured model: {model_alias}")
        else:
            click.echo("Error: no models defined in config and no --model specified.")
            raise click.Abort()

    # ------------------------------------------------------------------
    # State reset  (--reset)
    # ------------------------------------------------------------------
    # When the user passes --reset, we (a) clear the model's entry in the
    # state tracker so previously "completed" tasks are re-processed, and
    # (b) delete any saved result JSON files for that model.  This is a
    # destructive operation — the user must re-run the full pipeline.
    if reset:
        _reset_model(config, model_alias)

    # ------------------------------------------------------------------
    # Resume failed  (--resume-failed)
    # ------------------------------------------------------------------
    # Remove only the "failed" entries from state for the selected model,
    # leaving "success" entries untouched.  The subsequent pipeline run
    # will then re-process only the previously-failed tasks.
    if resume_failed:
        _resume_failed(config, model_alias)

    # ------------------------------------------------------------------
    # List failed  (--list-failed)
    # ------------------------------------------------------------------
    # Print all failed tasks for the model and exit.  Does NOT modify
    # state or run the pipeline.
    if list_failed:
        _list_failed(config, model_alias)
        return

    # ------------------------------------------------------------------
    # Dry-run enumeration  (--dry-run)
    # ------------------------------------------------------------------
    # When --dry-run is used we enumerate and display the task set but
    # never actually start the pipeline.
    if dry_run:
        _dry_run(config, model_alias)
        return

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------
    pipeline = Pipeline(config, model_alias, max_workers=workers, burn_mode=burn)

    click.echo(f"\n{'='*50}")
    click.echo(f"  litrev-extract Pipeline")
    click.echo(f"  Project: {config.project_name}")
    click.echo(f"  Model:   {model_alias}")
    click.echo(f"  Workers: {workers or pipeline.model_config.max_concurrent}")
    click.echo(f"  Mode:    {'BURN (no saving)' if burn else 'Normal'}")
    click.echo(f"{'='*50}\n")

    pipeline.initialize_queue()
    if dry_run:
        return

    stats = pipeline.run()

    # Print end-of-run summary.
    click.echo(f"\n{'='*50}")
    click.echo(f"  Pipeline complete")
    click.echo(f"  Total: {stats.total} | Completed: {stats.completed} "
               f"| Failed: {stats.failed} | Skipped: {stats.skipped} | Retries: {stats.retries}")
    click.echo(f"{'='*50}")


def _reset_model(config, model_alias: str) -> None:
    """Reset state and delete previous results for a model.

    This is used by the ``--reset`` flag to wipe all prior progress,
    allowing a clean re-run of the pipeline for the specified model.

    The cleanup covers two locations:

      1. The **state file** (``.litrev_state.json``), where per-model
         progress is tracked.  The model's entry is cleared so all tasks
         appear as "pending" on the next run.
      2. The **result output directory** (``output/<result_subdir>/``),
         where saved JSON result files matching ``*_<model_alias>.json``
         are deleted.

    Args:
        config: The loaded project configuration (a ``Config`` object).
        model_alias: The alias of the model whose state and results
            should be purged.
    """
    from ..state import StateManager
    from pathlib import Path

    click.echo(f"Resetting state for model: {model_alias}")

    # Reset in-memory state file entry for this model.
    state_mgr = StateManager(config.state_file)
    state_mgr.reset_model(model_alias)

    # Delete persisted result files matching this model's alias.
    # File-naming pattern:  <base>_<prompt_name>_<model_alias>.json
    # Escape glob metacharacters in model_alias to prevent injection.
    derived_dir = Path(config.output.directory) / config.output.result_subdir
    if derived_dir.exists():
        escaped_alias = re.escape(model_alias)
        pattern = f"*_{escaped_alias}.json"
        deleted = 0
        for f in derived_dir.rglob(pattern):
            f.unlink()
            deleted += 1
        click.echo(f"Deleted {deleted} result files for model '{model_alias}'")

    click.echo("Reset complete.\n")


def _resume_failed(config, model_alias: str) -> None:
    """Reset only failed-task entries in state for a model.

    Unlike ``--reset`` (which nukes ALL state and deletes result files),
    this helper uses ``StateManager.reset_failed()`` to remove only the
    entries whose status is ``"failed"``.  Successful tasks are preserved
    and will be skipped during the subsequent pipeline run.

    Args:
        config: The loaded project configuration.
        model_alias: The alias of the model whose failed tasks to reset.
    """
    from ..state import StateManager

    click.echo(f"Resetting failed tasks for model: {model_alias}")

    state_mgr = StateManager(config.state_file)
    failed_ids = state_mgr.get_failed_ids(model_alias)

    if not failed_ids:
        click.echo("No failed tasks to reset. Nothing to do.")
        return

    click.echo(f"Found {len(failed_ids)} failed task(s):")
    for tid in failed_ids:
        click.echo(f"  - {tid}")

    removed = state_mgr.reset_failed(model_alias)
    click.echo(f"Removed {removed} failed entries from state. "
               "Pipeline will re-process them on the next run.")


def _list_failed(config, model_alias: str) -> None:
    """Print all failed tasks for a model without modifying state.

    Args:
        config: The loaded project configuration.
        model_alias: The alias of the model whose failed tasks to list.
    """
    from ..state import StateManager

    state_mgr = StateManager(config.state_file)
    failed_ids = state_mgr.get_failed_ids(model_alias)

    if not failed_ids:
        click.echo("No failed tasks.")
        return

    click.echo(f"Failed tasks for model '{model_alias}':")
    for tid in failed_ids:
        # Show the full task_id (doc|prompt|model)
        click.echo(f"  {tid}")


def _dry_run(config, model_alias: str) -> None:
    """Enumerate and display all tasks without executing.

    Walks the input directory (using the configured extensions and
    recursion setting), pairs each document with each prompt, and prints
    the first five document-prompt mappings as a preview.

    Args:
        config: The loaded project configuration (a ``Config`` object).
        model_alias: The model alias whose configuration will be used
            (informational only in dry-run mode).
    """
    from ..utils.file_utils import relpath, scan_documents

    # Gather documents using the same logic the pipeline will use.
    extensions = [fmt.value for fmt in config.input_formats]
    documents = scan_documents(
        input_dir=config.input_dir,
        extensions=extensions,
        recursive=config.recursive,
    )

    click.echo(f"\nDry run for model '{model_alias}':")
    click.echo(f"  Documents: {len(documents)}")
    click.echo(f"  Prompts:   {len(config.prompts)}")
    click.echo(f"  Total tasks: {len(documents) * len(config.prompts)}")
    click.echo()

    # Show first 5 document-prompt pairs as a preview.
    for doc in documents[:5]:
        rel = relpath(doc, config.input_dir)
        click.echo(f"  [DOC] {rel}")
        for p in config.prompts:
            click.echo(f"     - {p.name} ({p.id})")
    if len(documents) > 5:
        click.echo(f"  ... and {len(documents) - 5} more documents")