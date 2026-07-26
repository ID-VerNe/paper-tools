"""``litrev postproc`` subcommand — run the post-processing pipeline.

After the LLM extraction phase (``litrev run``) has produced per-document
JSON result files, this command aggregates them, computes statistics,
generates reports, and exports CSV files — each step is a pluggable
module configured in ``litrev.yaml`` under ``postproc.pipeline``.

The pipeline steps are executed in declaration order.  Individual steps
can be skipped via the ``--step`` filter, and the full list can be viewed
with ``--list-steps`` without running anything.
"""

from __future__ import annotations

import logging
import os

import click

from ..config import ConfigLoader
from ..postproc.registry import run_pipeline


@click.command()
@click.option(
    "--model", "-m",
    default=None,
    help="Model alias (default: first model in config)",
)
@click.option(
    "--config", "-c",
    "config_path",
    default="litrev.yaml",
    show_default=True,
    help="Path to litrev.yaml config file",
)
@click.option(
    "--step", "-s",
    "steps",
    multiple=True,
    default=None,
    help="Run only specific post-processing steps (can repeat)",
)
@click.option(
    "--list-steps",
    is_flag=True,
    default=False,
    help="List configured post-processing steps and exit",
)
def postproc(
    model: str | None,
    config_path: str,
    steps: tuple[str, ...] | None,
    list_steps: bool,
) -> None:
    """Run the post-processing pipeline.

    Aggregates per-document extraction results, computes statistics,
    generates reports, and exports CSV files as configured in
    ``litrev.yaml``'s ``postproc.pipeline`` section.

    Args:
        model: Model alias whose results to post-process. When ``None``,
            the first model in the config is used.
        config_path: Path to the ``litrev.yaml`` configuration file.
        steps: One or more step names to restrict execution to. When
            ``None`` (or omitted), all enabled steps run in order.
        list_steps: When ``True``, print the configured pipeline steps
            (with their enabled/disabled status) and exit without running
            anything.
    """
    # ------------------------------------------------------------------
    # Config loading
    # ------------------------------------------------------------------
    if not os.path.exists(config_path):
        click.echo(f"Error: config file not found: {config_path}")
        raise click.Abort()

    config = ConfigLoader.from_file(config_path)

    # ------------------------------------------------------------------
    # Model alias resolution
    # ------------------------------------------------------------------
    model_alias = model
    if not model_alias:
        if config.models:
            model_alias = config.models[0].alias
        else:
            click.echo("Error: no models defined in config.")
            raise click.Abort()

    # Convert the tuple of repeated --step values to a list, or None if
    # no steps were specified (meaning "run all enabled steps").
    steps_filter = list(steps) if steps else None

    # ------------------------------------------------------------------
    # List-steps mode  (--list-steps)
    # ------------------------------------------------------------------
    if list_steps:
        click.echo(f"\nConfigured post-processing steps for '{config.project_name}':")
        if not config.postproc_pipeline:
            click.echo("  (no steps configured)")
        for s in config.postproc_pipeline:
            status = "[x]" if s.enabled else "[ ]"
            click.echo(f"  {status} {s.name}: {s.module}")
        return

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------
    click.echo(f"\n{'='*50}")
    click.echo(f"  Post-Processing Pipeline")
    click.echo(f"  Project: {config.project_name}")
    click.echo(f"  Model:   {model_alias}")
    click.echo(f"{'='*50}\n")

    run_pipeline(config, model_alias, steps_filter=steps_filter)

    click.echo(f"\n{'='*50}")
    click.echo(f"  Post-processing complete")
    click.echo(f"{'='*50}")