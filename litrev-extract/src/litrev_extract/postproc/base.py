"""Post-processor abstract base class and plugin infrastructure.

This module defines the :class:`PostProcessor` abstract base class that every
post-processing step in the pipeline must subclass.  The base provides:

* A **contract** — subclasses implement :meth:`~PostProcessor.run`.
* A **name** attribute used by the registry for lookup.
* A **factory** hook (:meth:`~PostProcessor.from_config`) that subclasses can
  override to support custom initialization from pipeline configuration.

Built-in processors
    * :mod:`litrev_extract.postproc.aggregate`  — consolidate per-document results
    * :mod:`litrev_extract.postproc.stats`       — configurable statistics
    * :mod:`litrev_extract.postproc.export_csv`  — field-based CSV export
    * :mod:`litrev_extract.postproc.report_md`   — templated markdown reports

Typical usage
    All processors are discovered and run through the pipeline in
    :func:`litrev_extract.postproc.registry.run_pipeline`.  Direct instantiation
    is rarely needed outside tests.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from ..models import ReviewConfig


class PostProcessor(ABC):
    """Abstract base for all post-processing steps.

    Every post-processor is a **named, runnable plugin** that reads aggregated
    extraction results and produces some output (statistics, CSV, markdown, …).

    Subclasses **must** set :attr:`name` to a unique identifier (matched against
    ``step.name`` in the pipeline config) and implement :meth:`run`.

    Attributes
    ----------
    name : str
        Short, unique identifier for this processor.  Registered automatically
        by the :func:`~litrev_extract.postproc.registry.register_post_processor`
        decorator.
    config : dict
        Step-specific configuration passed through from the pipeline YAML.
        Populated by :meth:`from_config`; defaults to ``{}``.
    """

    name: str = "base"

    # Configuration dict, populated by from_config() — subclasses read from this.
    config: dict = {}

    @abstractmethod
    def run(self, config: ReviewConfig, model_alias: str) -> Any:
        """Execute this post-processing step.

        Parameters
        ----------
        config : ReviewConfig
            The full review configuration loaded from ``litrev.yaml``.  Provides
            access to ``config.output``, ``config.prompts``, etc.
        model_alias : str
            Which model's results to process (e.g. ``"gpt4o"``, ``"gpt55"``).
            Used to locate the correct subdirectory under ``output/``.

        Returns
        -------
        Any
            Arbitrary result data.  The pipeline collects these into a
            ``{step_name: result}`` dict.  Commonly ``None``, a dict of counts,
            or the generated report text.
        """
        ...

    @classmethod
    def from_config(cls, step_config: dict) -> PostProcessor:
        """Factory: create an instance from a step-level configuration dict.

        The default implementation simply calls the no-argument constructor and
        stores *step_config* in ``self.config``.  Subclasses **override** this
        when they need custom initialization logic or validation.

        Parameters
        ----------
        step_config : dict
            The ``config:`` block from one pipeline step in ``litrev.yaml``.
            Typically contains keys like ``output_file``, ``sections``, or
            ``exports`` depending on the processor.

        Returns
        -------
        PostProcessor
            A fully initialized processor instance.
        """
        instance = cls()
        instance.config = step_config
        return instance