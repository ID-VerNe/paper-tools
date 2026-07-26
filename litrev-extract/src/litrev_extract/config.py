"""Configuration loading from YAML files.

This module replaces the hardcoded configuration approach from the original
project with a dynamic ``ConfigLoader`` that parses ``litrev.yaml`` files into
:class:`~litrev_extract.models.ReviewConfig` dataclass instances.

The loader performs validation on required fields, resolves environment variable
references (``$VAR`` / ``${VAR}``), loads external prompt template files
relative to the YAML directory, and normalises input format extensions.

Typical usage::

    from litrev_extract.config import ConfigLoader

    config = ConfigLoader.from_file("litrev.yaml")
    # config is now a fully-populated ReviewConfig instance

    # For testing or programmatic use:
    config = ConfigLoader.from_dict({"project": {"name": "test"}, ...})
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .models import (
    InputFormat,
    ModelConfig,
    NamingConfig,
    OutputConfig,
    PostprocStepConfig,
    PromptDef,
    RateLimitConfig,
    ReviewConfig,
)


def _normalize_ext(ext: str) -> str:
    """Normalize a file extension string (e.g. ``.MD`` becomes ``.md``).

    Args:
        ext: Raw extension string, possibly with leading/trailing whitespace
            or inconsistent casing.

    Returns:
        Lower-cased, stripped extension string.
    """
    return ext.strip().lower()


def _parse_input_formats(raw: List[str]) -> List[InputFormat]:
    """Parse a list of extension strings into ``InputFormat`` enum values.

    Each entry is first normalised (lower-cased, stripped) and then matched
    against :class:`~litrev_extract.models.InputFormat` members by value.
    If value matching fails, a fallback pass tries matching by member name.

    Args:
        raw: List of extension strings (e.g. ``[".md", ".txt"]``).

    Returns:
        List of corresponding ``InputFormat`` enum members.  Unrecognised
        extensions are logged as warnings (but not fatal).
    """
    import logging
    logger = logging.getLogger(__name__)

    formats: List[InputFormat] = []
    for f in raw:
        ext = _normalize_ext(f)
        try:
            formats.append(InputFormat(ext))
        except ValueError:
            matched = False
            for fmt in InputFormat:
                if fmt.name.lower() == ext or fmt.value == ext:
                    formats.append(fmt)
                    matched = True
                    break
            if not matched:
                logger.warning(
                    "Unrecognized input format '%s' -- ignored. "
                    "Supported: %s", f, [e.value for e in InputFormat]
                )
    return formats


def _resolve_env_var(value: str) -> str:
    """Resolve environment variable references in a configuration string.

    Supports two syntaxes:
    - ``${VAR_NAME}`` -- brace-delimited (POSIX-style).
    - ``$VAR_NAME``  -- bare dollar prefix.

    If the referenced variable is not set, the original string is returned
    unchanged.  Currently does *not* support default-value syntax
    (``${VAR:-default}``).

    Args:
        value: A string that may contain environment variable references.

    Returns:
        The input string with any recognised environment variables replaced
        by their values, or the original string if the variable is unset.
    """
    if value.startswith("${") and value.endswith("}"):
        return os.environ.get(value[2:-1], value)
    if value.startswith("$"):
        return os.environ.get(value[1:], value)
    return value


def _validate_config(config: dict, path: str) -> List[str]:
    """Validate a parsed configuration dictionary.

    Checks for the presence and correct types of required top-level sections.
    Validation is intentionally lenient -- it collects all errors before
    returning rather than failing at the first one.

    Args:
        config: Raw configuration dictionary parsed from YAML.
        path: Human-readable source identifier used in error messages
            (e.g. the YAML file path or ``"<inline>"``).

    Returns:
        A list of error message strings.  An empty list means the
        configuration is valid.
    """
    errors: List[str] = []

    if not config.get("project"):
        errors.append(f"{path}: missing 'project' section")

    project = config.get("project", {})
    if not project.get("name"):
        errors.append(f"{path}: 'project.name' is required")

    if not config.get("models"):
        errors.append(f"{path}: at least one 'models' entry is required")
    elif not isinstance(config["models"], list):
        errors.append(f"{path}: 'models' must be a list")

    if not config.get("prompts"):
        errors.append(f"{path}: at least one 'prompts' entry is required")
    elif not isinstance(config["prompts"], list):
        errors.append(f"{path}: 'prompts' must be a list")

    # Validate input config
    inp = config.get("input", {})
    if not inp.get("directory"):
        errors.append(f"{path}: 'input.directory' is required")

    return errors


def _load_prompt_file(file_path: str, base_dir: str) -> Optional[str]:
    """Load a prompt template file from disk.

    Relative paths are resolved against *base_dir* (typically the directory
    containing the YAML config file).  If the file does not exist, ``None``
    is returned rather than raising an error, allowing callers to fall back
    to an inline template.

    Args:
        file_path: Path to the template file.  May be absolute or relative.
        base_dir: Directory used to resolve relative paths.

    Returns:
        The file contents as a UTF-8 string, or ``None`` if the file does
        not exist or *file_path* is empty.
    """
    if not file_path or not file_path.strip():
        return None
    path = Path(file_path)
    if not path.is_absolute():
        path = Path(base_dir) / path
    if path.exists():
        return path.read_text(encoding="utf-8")
    return None


class ConfigLoader:
    """Loads and validates ``litrev.yaml`` configuration files.

    The loader searches for ``litrev.yaml`` in a list of directories
    (defaulting to the current working directory) or accepts an explicit
    file path.  Once loaded, the YAML is validated, environment variables
    are resolved, prompt templates are read, and the result is returned as
    a :class:`~litrev_extract.models.ReviewConfig` instance.

    Usage::

        # Load from a specific file path
        config = ConfigLoader.from_file("./project/litrev.yaml")

        # Load from a dictionary (useful in tests)
        config = ConfigLoader.from_dict({"project": {"name": "test"}, ...})

    Attributes:
        search_paths: Ordered list of directories to search for
            ``litrev.yaml``.  Only used by :meth:`discover` (not
            :meth:`from_file` or :meth:`from_dict`).
    """

    def __init__(self, search_paths: Optional[List[str]] = None) -> None:
        """Initialise the loader with a list of search directories.

        Args:
            search_paths: Directories to scan for ``litrev.yaml`` in order
                of preference.  Defaults to ``[os.getcwd()]``.
        """
        self.search_paths: List[str] = search_paths or [os.getcwd()]

    @classmethod
    def from_file(cls, path: str) -> ReviewConfig:
        """Load and parse a ``litrev.yaml`` configuration file.

        The YAML file is opened, validated, and parsed into a
        :class:`~litrev_extract.models.ReviewConfig` instance.  Prompt
        template file paths are resolved relative to the YAML file's
        parent directory.

        Args:
            path: Absolute or relative path to the YAML configuration file.

        Returns:
            A fully populated ``ReviewConfig`` instance.

        Raises:
            FileNotFoundError: If *path* does not exist.
            ValueError: If the YAML content is empty, malformed, or fails
                validation.
        """
        yaml_path = Path(path).resolve()
        base_dir: str = str(yaml_path.parent)

        with open(yaml_path, "r", encoding="utf-8") as f:
            raw: Optional[dict] = yaml.safe_load(f)

        if not raw:
            raise ValueError(f"Empty or invalid YAML file: {path}")

        errors = _validate_config(raw, str(yaml_path))
        if errors:
            raise ValueError("\n".join(errors))

        return cls._parse(raw, base_dir)

    @classmethod
    def from_dict(cls, data: dict, base_dir: str = ".") -> ReviewConfig:
        """Load configuration from an in-memory Python dictionary.

        This is primarily useful for unit tests, as it bypasses file I/O
        while still exercising the full validation and parsing logic.

        Args:
            data: Raw configuration dictionary matching the structure of a
                parsed YAML file.
            base_dir: Directory used to resolve relative prompt template
                file paths.  Defaults to the current directory.

        Returns:
            A fully populated ``ReviewConfig`` instance.

        Raises:
            ValueError: If *data* fails validation.
        """
        errors = _validate_config(data, "<inline>")
        if errors:
            raise ValueError("\n".join(errors))
        return cls._parse(data, base_dir)

    @classmethod
    def _parse(cls, raw: dict, base_dir: str) -> ReviewConfig:
        """Internal method: transform a validated dict into ``ReviewConfig``.

        This is the single entry-point for both :meth:`from_file` and
        :meth:`from_dict`.  It unpacks each top-level section of the raw
        dictionary, instantiates the corresponding dataclasses, and
        assembles the final ``ReviewConfig``.

        Args:
            raw: Validated configuration dictionary as returned by YAML
                parsing.
            base_dir: Base directory for resolving relative paths (prompt
                template files, etc.).

        Returns:
            A new ``ReviewConfig`` instance fully populated from *raw*.
        """
        project: dict = raw.get("project", {})
        inp: dict = raw.get("input", {})
        outp: dict = raw.get("output", {})

        # --- Project metadata ---
        name: str = project.get("name", "unnamed-review")
        description: str = project.get("description", "")

        # --- Input configuration ---
        input_dir: str = inp.get("directory", "./documents")
        input_formats: List[InputFormat] = _parse_input_formats(inp.get("formats", [".md"]))
        recursive: bool = inp.get("recursive", True)
        exclude_patterns: List[str] = inp.get("exclude_patterns", [])

        # --- Output configuration ---
        out_naming: dict = outp.get("file_naming", {})
        output: OutputConfig = OutputConfig(
            directory=outp.get("directory", "./output"),
            structure=outp.get("structure", "flat"),
            result_subdir=outp.get("result_subdir", "derived"),
            aggregate_subdir=outp.get("aggregate_subdir", "aggregate"),
            report_subdir=outp.get("report_subdir", "reports"),
            plot_subdir=outp.get("plot_subdir", "plots"),
            file_naming=NamingConfig(
                pattern=out_naming.get(
                    "pattern", "{base}_{prompt_name}_{model_alias}.json"
                )
            ),
        )

        # --- Model endpoints ---
        models: List[ModelConfig] = []
        for m in raw.get("models", []):
            rl: dict = m.get("rate_limit", {})
            models.append(
                ModelConfig(
                    alias=m.get("alias", "default"),
                    api_key_env=m.get("api_key_env", ""),
                    base_url=_resolve_env_var(m.get("base_url", "")),
                    model_name=m.get("model_name", ""),
                    max_concurrent=m.get("max_concurrent", 3),
                    max_retries=m.get("max_retries", 10),
                    retry_delay_base=m.get("retry_delay_base", 2),
                    rate_limit=RateLimitConfig(
                        max_requests=rl.get("max_requests", 0),
                        window_seconds=rl.get("window_seconds", 60),
                    ),
                )
            )

        # --- Prompt definitions ---
        prompts: List[PromptDef] = []
        for p in raw.get("prompts", []):
            prompt_file: str = p.get("file", "")
            template_content: Optional[str] = _load_prompt_file(prompt_file, base_dir)
            # Load the user template: prefer file on disk, fall back to YAML inline
            user_template: str = template_content if template_content is not None else p.get("user_template", "")

            prompts.append(
                PromptDef(
                    name=p.get("name", "unnamed"),
                    id=p.get("id", f"v1_{p.get('name', 'unnamed')}"),
                    file=prompt_file,
                    system_prompt=p.get("system_prompt", ""),
                    user_template=user_template,
                    content_truncation=p.get("content_truncation"),
                    multimodal=p.get("multimodal", False),
                )
            )

        # --- Post-processing pipeline ---
        postproc_raw: dict = raw.get("postproc", {})
        pipeline: List[dict] = postproc_raw.get("pipeline", [])
        postproc_steps: List[PostprocStepConfig] = [PostprocStepConfig.from_dict(s) for s in pipeline]

        # --- State file ---
        state_file: str = raw.get("state_file", ".litrev_state.json")

        return ReviewConfig(
            project_name=name,
            description=description,
            input_dir=input_dir,
            input_formats=input_formats,
            output=output,
            models=models,
            prompts=prompts,
            postproc_pipeline=postproc_steps,
            state_file=state_file,
            recursive=recursive,
            exclude_patterns=exclude_patterns,
            dry_run=raw.get("dry_run", False),
            schema_version=raw.get("schema_version", 1),
        )