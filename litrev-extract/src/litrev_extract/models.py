"""Core data models for litrev-extract.

This module defines the foundational dataclasses and enums used throughout the
literature review extraction pipeline. These models serve as the shared type
vocabulary across configuration loading, task scheduling, LLM interaction, and
post-processing stages.

Key types:
    - InputFormat: Enum of accepted document formats.
    - ModelConfig / RateLimitConfig: LLM endpoint configuration.
    - PromptDef: One extraction prompt template (system + user).
    - ExtractionTask: A single unit of work (document x prompt x model).
    - PostprocStepConfig: One step in the post-processing pipeline.
    - ReviewConfig: Top-level configuration aggregating all sub-configs.
    - PipelineStats: Thread-safe runtime counters for progress tracking.

Typical usage::

    from litrev_extract.models import ReviewConfig, ModelConfig, PromptDef

    config = ReviewConfig(
        project_name="my-review",
        input_dir="./papers",
        models=[ModelConfig(alias="gpt4", model_name="gpt-4", ...)],
        prompts=[PromptDef(name="extract", id="v1_extract", ...)],
    )
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class InputFormat(str, Enum):
    """Supported input document formats.

    Each member maps a human-friendly label to its corresponding file extension.
    The enum inherits from ``str`` so that members can be compared directly
    against extension strings (e.g. ``".md" in InputFormat``).

    Attributes:
        MARKDOWN: Markdown files (``.md``).
        PLAINTEXT: Plain text files (``.txt``).
        PDF_TEXT: Pre-extracted PDF text dumps (``.pdf_text``).
    """

    MARKDOWN = ".md"
    PLAINTEXT = ".txt"
    PDF_TEXT = ".pdf_text"


@dataclass
class RateLimitConfig:
    """Rate limiting configuration for an LLM model endpoint.

    Controls how aggressively requests are throttled to stay within an API
    provider's rate limits.  A ``max_requests`` of 0 (the default) means
    *unlimited* -- no throttling is applied.

    Attributes:
        max_requests: Maximum number of requests allowed within the window.
            ``0`` disables rate limiting entirely.
        window_seconds: Duration (in seconds) of the rate-limit sliding window.
    """

    max_requests: int = 0  # 0 = unlimited
    window_seconds: int = 60

    @property
    def enabled(self) -> bool:
        """Whether rate limiting is active (``max_requests > 0``)."""
        return self.max_requests > 0


@dataclass
class ModelConfig:
    """Configuration for a single LLM model endpoint.

    Each ``ModelConfig`` instance defines one "model slot" the pipeline can
    send requests to.  Multiple configs can refer to the same model name (e.g.
    for different API keys or base URLs) by using distinct aliases.

    Attributes:
        alias: Short human-readable label used in output file names and
            state tracking (e.g. ``"gpt4"``, ``"claude-haiku"``).
        api_key_env: Name of the environment variable holding the API key.
        base_url: Full API endpoint URL (may include ``/chat/completions``
            suffix, which is stripped automatically via :attr:`api_base`).
        model_name: The model identifier sent in the request body
            (e.g. ``"gpt-4"``, ``"claude-sonnet-4-20250514"``).
        max_concurrent: Maximum number of concurrent requests allowed against
            this endpoint.  Used by the pipeline scheduler to cap parallelism.
        max_retries: Maximum number of retry attempts after a transient
            failure (rate-limit, timeout, server error).
        retry_delay_base: Base delay (seconds).  Actual delay =
            ``min(retry_count * retry_delay_base, 60)`` (linear backoff).
        rate_limit: Rate limit policy for this endpoint.
    """

    alias: str
    api_key_env: str
    base_url: str
    model_name: str
    max_concurrent: int = 3
    max_retries: int = 10
    retry_delay_base: int = 2
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)

    @property
    def api_base(self) -> str:
        """Base URL with the ``/chat/completions`` suffix stripped.

        Some providers append ``/chat/completions`` to the base URL while
        others do not.  This property normalises the URL so that downstream
        code always receives the clean endpoint root.
        """
        return self.base_url.rstrip("/").removesuffix("/chat/completions")


@dataclass
class PromptDef:
    """Definition of a single extraction prompt template.

    A ``PromptDef`` pairs an optional **system prompt** with a **user
    template** (loaded from an external file or provided inline) that is
    rendered for each document.

    Attributes:
        name: Short label used in output files and state tracking
            (e.g. ``"extract-methods"``).
        id: Unique identifier for state persistence.  Convention is
            ``v1_{name}``.
        file: Path to the user template file (resolved relative to the
            YAML config file at load time).  Empty string when the template is
            supplied inline.
        system_prompt: System-level instructions sent ahead of the user
            message.  May be empty when the model uses a single-turn prompt.
        content_truncation: If set, document content is truncated to this
            many characters before being inserted into the user template.
            ``None`` means no truncation.
        multimodal: When ``True``, the pipeline reads image files from a
            ``images/`` sub-directory alongside the source document and
            sends them to the LLM via the vision API (``content`` array with
            ``image_url`` parts). The ``{content}`` variable receives either
            the image caption (when available) or the document context text.
    """

    name: str
    id: str
    file: str
    system_prompt: str
    user_template: str = ""
    content_truncation: Optional[int] = None
    multimodal: bool = False


@dataclass
class ExtractionTask:
    """One unit of extraction work: one document x one prompt x one model.

    The pipeline scheduler creates one ``ExtractionTask`` for every
    combination of (matched document, configured prompt, configured model).
    Each task is independent and can be dispatched concurrently.

    Attributes:
        document_path: Absolute path to the source document on disk.
        relative_path: Document path relative to the input directory; used
            for state tracking and mirror-style output layout.
        prompt_def: The prompt template to execute against this document.
        model_alias: Which model endpoint (by :attr:`ModelConfig.alias`) to
            send the request to.
        retry_count: Number of times this task has been retried after a
            transient failure.  Reset to 0 on initial creation.
        image_path: When the prompt is multimodal, the path to the image
            file to send alongside the text prompt.
        image_caption: The figure caption text associated with this image,
            substituted as ``{content}`` in the user template.
        image_doi_slug: DOI slug for output file naming in multimodal mode.
    """

    document_path: str
    relative_path: str
    prompt_def: PromptDef
    model_alias: str
    image_path: str = ""
    image_caption: str = ""
    image_doi_slug: str = ""

    @property
    def task_id(self) -> str:
        """Unique composite identifier for state tracking.

        Format: ``{relative_path}|{prompt_def.id}|{model_alias}``.

        For multimodal tasks (per-image), the relative_path already includes
        the image filename so each image gets its own state entry.

        This key is used to checkpoint progress in the state file so that
        interrupted runs can resume without re-processing completed tasks.
        """
        return f"{self.relative_path}|{self.prompt_def.id}|{self.model_alias}"

    retry_count: int = 0


@dataclass
class NamingConfig:
    """File naming pattern configuration.

    Controls how per-result files are named.  The pattern string may contain
    placeholders that are expanded at runtime:

    - ``{base}`` -- document filename without extension.
    - ``{prompt_name}`` -- :attr:`PromptDef.name`.
    - ``{model_alias}`` -- :attr:`ModelConfig.alias`.

    Attributes:
        pattern: Naming pattern with ``{placeholders}`` (default:
            ``"{base}_{prompt_name}_{model_alias}.json"``).
    """

    pattern: str = "{base}_{prompt_name}_{model_alias}.json"


@dataclass
class OutputConfig:
    """Output directory layout configuration.

    Defines where extracted results, aggregations, reports, and plots are
    written.  The ``structure`` field controls whether the output tree mirrors
    the input directory hierarchy (``"mirror"``) or flattens all results into
    a single directory (``"flat"``).

    Attributes:
        directory: Root output directory.
        structure: Layout strategy -- ``"flat"`` or ``"mirror"``.
        result_subdir: Sub-directory for per-document extraction results
            (e.g. ``"derived"``).
        aggregate_subdir: Sub-directory for aggregated output across
            documents (e.g. ``"aggregate"``).
        report_subdir: Sub-directory for human-readable reports
            (e.g. ``"reports"``).
        plot_subdir: Sub-directory for visualisation assets
            (e.g. ``"plots"``).
        file_naming: Pattern for individual result filenames.
    """

    directory: str = "./output"
    structure: str = "flat"  # "flat" or "mirror"
    result_subdir: str = "derived"
    aggregate_subdir: str = "aggregate"
    report_subdir: str = "reports"
    plot_subdir: str = "plots"
    file_naming: NamingConfig = field(default_factory=NamingConfig)


@dataclass
class PostprocStepConfig:
    """Configuration for one post-processing pipeline step.

    Each step is a named transformation that operates on the raw LLM outputs
    after all extractions complete.  Steps are executed in the order they
    appear in the pipeline list.

    Attributes:
        name: Human-readable label for the step (used in logging/error
            messages).
        module: Python import path pointing to the step implementation
            (e.g. ``"litrev_extract.postproc.deduplicate"``).
        enabled: Whether this step should be executed.  Disabled steps are
            skipped during pipeline execution.
        config: Arbitrary keyword arguments forwarded to the step
            implementation's ``run()`` function.
    """

    name: str
    module: str
    enabled: bool = True
    config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> PostprocStepConfig:
        """Construct a ``PostprocStepConfig`` from a plain dictionary.

        This factory is used during YAML deserialisation so that downstream
        code never has to unpack dict fields manually.

        Args:
            d: Dictionary with keys ``name``, ``module``, ``enabled``,
                and ``config``.  Missing keys fall back to their defaults.

        Returns:
            A new ``PostprocStepConfig`` instance populated from *d*.
        """
        return cls(
            name=d.get("name", "unnamed"),
            module=d.get("module", ""),
            enabled=d.get("enabled", True),
            config=d.get("config", {}),
        )


@dataclass
class ReviewConfig:
    """Complete review project configuration.

    This is the top-level dataclass that the :class:`~litrev_extract.config.ConfigLoader`
    produces from a ``litrev.yaml`` file.  It aggregates every sub-configuration
    needed to run a full extraction pipeline.

    Attributes:
        project_name: User-facing name for the review (e.g. ``"my-review"``).
        input_dir: Directory containing source documents.
        input_formats: File extensions to scan for in the input directory.
        output: Layout and naming rules for generated files.
        models: One entry per LLM endpoint the pipeline may call.
        prompts: One entry per extraction prompt template to run.
        postproc_pipeline: Ordered list of post-processing steps.
        state_file: Path (relative to project root) for the JSON state
            checkpoint that enables resumption after interruption.
        recursive: Whether to scan ``input_dir`` recursively for documents.
        exclude_patterns: Glob patterns for files/folders to ignore.
        dry_run: When ``True``, the pipeline enumerates and logs tasks
            without executing any LLM calls.
        description: Free-text description of the review project.
        schema_version: Config schema version for forward-compatibility
            checks.
    """

    project_name: str
    input_dir: str
    input_formats: List[InputFormat]
    output: OutputConfig = field(default_factory=OutputConfig)
    models: List[ModelConfig] = field(default_factory=list)
    prompts: List[PromptDef] = field(default_factory=list)
    postproc_pipeline: List[PostprocStepConfig] = field(default_factory=list)
    state_file: str = ".litrev_state.json"
    recursive: bool = True
    exclude_patterns: List[str] = field(default_factory=list)
    dry_run: bool = False
    description: str = ""
    schema_version: int = 1


class PipelineStats:
    """Runtime statistics tracking for a pipeline run.

    Collects thread-safe counters for total, completed, failed, skipped, and
    retried tasks.  Uses a ``threading.Lock`` internally so that concurrent
    workers can update counters without races.

    The :meth:`snapshot` method provides an atomic view of all counters that
    callers can use for logging or progress-bar updates.

    Attributes:
        total: Total number of tasks created for the run.
        skipped: Number of tasks skipped (e.g. already completed in a
            previous run or excluded by filters).
        completed: Number of tasks that finished successfully.
        failed: Number of tasks that exhausted all retries.
        retries: Cumulative count of retry attempts across all tasks.
    """

    def __init__(self) -> None:
        self.total: int = 0
        self.skipped: int = 0
        self.completed: int = 0
        self.failed: int = 0
        self.retries: int = 0
        # Lock guarding all counters so snapshot() remains consistent.
        self._lock: threading.Lock = threading.Lock()

    @property
    def remaining(self) -> int:
        """Number of tasks not yet accounted for in any terminal bucket.

        Computed as ``total - skipped - completed - failed``.  Thread-safe
        via ``self._lock``.
        """
        with self._lock:
            return self.total - self.skipped - self.completed - self.failed

    def increment_retries(self, n: int = 1) -> None:
        """Atomically increase the retry counter.

        Args:
            n: Number of retries to add (default 1).
        """
        with self._lock:
            self.retries += n

    def increment_completed(self, n: int = 1) -> None:
        """Atomically increase the completed counter.

        Args:
            n: Number of completions to add (default 1).
        """
        with self._lock:
            self.completed += n

    def increment_failed(self, n: int = 1) -> None:
        """Atomically increase the failed counter.

        Args:
            n: Number of failures to add (default 1).
        """
        with self._lock:
            self.failed += n

    def snapshot(self) -> dict:
        """Return an atomic copy of all counters as a plain dictionary.

        The lock is held for the entire read so the returned values are
        internally consistent (e.g. ``remaining`` will always equal
        ``total - completed - failed - skipped`` for that instant).

        Returns:
            Dictionary with keys ``total``, ``skipped``, ``completed``,
            ``failed``, ``retries``, and ``remaining``.
        """
        with self._lock:
            return {
                "total": self.total,
                "skipped": self.skipped,
                "completed": self.completed,
                "failed": self.failed,
                "retries": self.retries,
                "remaining": self.remaining,
            }