"""Top-level CLI entry point for the `litrev` command.

Exposes three subcommands under a Click group:
  - ``litrev init``      — scaffold a new review project
  - ``litrev run``       — execute the extraction pipeline
  - ``litrev postproc``  — run the post-processing pipeline

Usage from the command line::

    litrev init my-review
    litrev run --model default
    litrev postproc --model default
"""

from __future__ import annotations

import click

from .init_cmd import init
from .run_cmd import run
from .postproc_cmd import postproc


@click.group()
@click.version_option(version="0.1.0", prog_name="litrev")
def cli() -> None:
    """litrev-extract: Config-driven LLM-based systematic literature review extraction.

    ``litrev`` orchestrates a multi-step extraction workflow:

        1. **Scaffold** a project with ``litrev init`` (config + prompts + document dir).
        2. **Extract** structured data from documents via ``litrev run``.
        3. **Post-process** the raw results into reports and CSVs via ``litrev postproc``.

    Run ``litrev init my-review`` to scaffold a new review project,
    then ``litrev run --model opus`` to extract data from your papers.
    """
    pass


# Register subcommands so they appear under the ``litrev`` group.
cli.add_command(init)
cli.add_command(run)
cli.add_command(postproc)


if __name__ == "__main__":
    cli()