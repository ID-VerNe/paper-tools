"""Post-processor plugin registry with decorator-based discovery.

This module manages the lifecycle of post-processing plugins:

1. **Registration** — via the :func:`register_post_processor` decorator.
2. **Discovery** — scanning user-provided scripts from the project's
   ``scripts/`` directory with :func:`discover_processors`.
3. **Instantiation** — resolving step configuration to a live processor
   instance via :func:`instantiate_processor`.
4. **Pipeline execution** — running all configured steps in order via
   :func:`run_pipeline`.

Architecture
    The registry uses a **decorator pattern** rather than manual registration:
    each processor module applies ``@register_post_processor("name")`` to its
    class, which adds it to the global ``_REGISTRY`` dict at import time.
    User-provided scripts placed in the project's ``scripts/`` directory are
    imported automatically when the pipeline runs.

See Also
    :class:`litrev_extract.postproc.base.PostProcessor` :
        The abstract base class that all registered processors implement.
    :func:`litrev_extract.postproc.aggregate.AggregateProcessor.run` :
        Example of a registered processor.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import Any, Dict, List, Optional, Type

from ..models import PostprocStepConfig, ReviewConfig
from .base import PostProcessor

# ---------------------------------------------------------------------------
# Global registry
# ---------------------------------------------------------------------------
# Maps processor name (str) → PostProcessor subclass.
# Populated by the @register_post_processor decorator at import time.
_REGISTRY: Dict[str, Type[PostProcessor]] = {}


def register_post_processor(name: str):
    """Decorator that registers a :class:`PostProcessor` subclass by name.

    The decorator sets ``cls.name = name`` and inserts the class into the
    global ``_REGISTRY`` so it can be looked up later by :func:`get_processor`
    or :func:`instantiate_processor`.

    Parameters
    ----------
    name : str
        Unique processor name.  This must match ``step.name`` in the
        pipeline configuration (``litrev.yaml``).

    Returns
    -------
    Callable[[Type[PostProcessor]], Type[PostProcessor]]
        A decorator that registers the class and returns it unmodified.

    Example
    -------
    .. code-block:: python

        @register_post_processor("aggregate")
        class AggregateProcessor(PostProcessor):
            ...
    """
    def wrapper(cls: Type[PostProcessor]) -> Type[PostProcessor]:
        # Override the class-level name so it matches what the config expects
        cls.name = name
        _REGISTRY[name] = cls
        return cls

    return wrapper


def get_processor(name: str) -> Type[PostProcessor]:
    """Look up a registered processor class by name.

    Parameters
    ----------
    name : str
        The processor name as registered via :func:`register_post_processor`.

    Returns
    -------
    Type[PostProcessor]
        The processor class (not yet instantiated).

    Raises
    ------
    KeyError
        If *name* has not been registered.
    """
    if name not in _REGISTRY:
        raise KeyError(
            f"Post-processor '{name}' not found. "
            f"Registered: {list(_REGISTRY.keys())}"
        )
    return _REGISTRY[name]


def import_user_module(module_path: str) -> None:
    """Import a user-provided Python module, triggering decorator registration.

    The module is expected to contain one or more ``@register_post_processor``
    decorated classes.  Importing the module executes the decorators, which
    populate the global ``_REGISTRY``.

    Resolution order
        1. Try *module_path* as a **dotted module path** (e.g.
           ``"my_project.scripts.custom_stats"``).
        2. If that fails, treat *module_path* as a **file path** (e.g.
           ``"/path/to/scripts/custom_stats.py"``), add its parent directory
           to ``sys.path``, and import by basename.

    Parameters
    ----------
    module_path : str
        Absolute or dotted path to a Python module.

    Raises
    ------
    ImportError
        If the module cannot be imported via either resolution strategy.
    FileNotFoundError
        If *module_path* is a file path that does not exist on disk.
    """
    # Strategy 1: Try as a dotted Python module path (e.g. "pkg.mod")
    try:
        importlib.import_module(module_path)
        return
    except ImportError:
        pass

    # Strategy 2: Treat as a filesystem path
    if os.path.exists(module_path):
        abs_path = os.path.abspath(module_path)
        try:
            spec = importlib.util.spec_from_file_location(
                f"litrev_user_module_{os.path.basename(abs_path)}",
                abs_path,
            )
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                return
        except Exception as e:
            raise ImportError(
                f"Could not import user module '{module_path}': {e}"
            ) from e
    raise FileNotFoundError(f"User module not found: {module_path}")


def discover_processors(extra_paths: Optional[List[str]] = None) -> None:
    """Scan directories or files for post-processor modules to register.

    Iterates over *extra_paths*; for each:

    * **File** — imported directly via :func:`import_user_module`.
    * **Directory** — every ``.py`` file (except ``_``-private ones) is
      imported, which triggers their ``@register_post_processor`` decorators.

    This is called automatically by :func:`run_pipeline` for the project's
    ``scripts/`` directory.

    Parameters
    ----------
    extra_paths : list of str, optional
        Filesystem paths to scan.  Each element can be a ``.py`` file or a
        directory of ``.py`` files.
    """
    for path in extra_paths or []:
        if os.path.isfile(path):
            import_user_module(path)
        elif os.path.isdir(path):
            # Import all .py files in the directory, sorted for determinism
            for fname in sorted(os.listdir(path)):
                if fname.endswith(".py") and not fname.startswith("_"):
                    fpath = os.path.join(path, fname)
                    import_user_module(fpath)


def instantiate_processor(
    step: PostprocStepConfig,
) -> PostProcessor:
    """Resolve a pipeline step config to a live :class:`PostProcessor` instance.

    Steps:

    1. **Import** the module specified by ``step.module`` (try dotted path
       first, then file path).
    2. **Look up** the registered processor by ``step.name`` in ``_REGISTRY``.
    3. **Fallback**: if no matching registration is found, scan the module's
       top-level attributes for any :class:`PostProcessor` subclass.
    4. **Instantiate** via :meth:`PostProcessor.from_config`.

    Parameters
    ----------
    step : PostprocStepConfig
        A single pipeline step, containing ``name``, ``module``, ``enabled``,
        and ``config`` fields.

    Returns
    -------
    PostProcessor
        An instantiated, ready-to-run processor.

    Raises
    ------
    ImportError
        If *step.module* cannot be imported through any resolution strategy.
    ValueError
        If no registered or scanned :class:`PostProcessor` class is found for
        *step.name*.
    """
    module_path = step.module

    # --- 1. Import the module ---
    try:
        importlib.import_module(module_path)
    except ImportError:
        # Fall back to treating module_path as a file path
        if os.path.exists(module_path):
            import_user_module(module_path)
        else:
            raise ImportError(
                f"Cannot import post-processor module '{module_path}' "
                f"for step '{step.name}'"
            ) from None

    # --- 2. Look up the registered class ---
    proc_cls = _REGISTRY.get(step.name)
    if not proc_cls:
        # --- 3. Fallback: scan module for any PostProcessor subclass ---
        # This is useful when a user module defines the class without the
        # @register_post_processor decorator (or with a different name).
        mod = sys.modules.get(module_path)
        if mod:
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, PostProcessor)
                    and attr is not PostProcessor  # exclude the ABC itself
                ):
                    proc_cls = attr
                    break

    if not proc_cls:
        raise ValueError(
            f"No PostProcessor class found for step '{step.name}' in '{module_path}'"
        )

    # --- 4. Instantiate ---
    return proc_cls.from_config(step.config)


def run_pipeline(
    config: ReviewConfig,
    model_alias: str,
    steps_filter: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute the full post-processing pipeline defined in the configuration.

    Workflow
    --------
    1. Discovers user-provided processors from ``<project>/scripts/``.
    2. Iterates through ``config.postproc_pipeline`` **in order**.
    3. Skips disabled steps and steps not in *steps_filter* (if provided).
    4. Runs each step via :meth:`PostProcessor.run`, collecting results.
    5. Catches and records exceptions per-step so one failure does not abort
       the entire pipeline.

    Parameters
    ----------
    config : ReviewConfig
        The full review configuration loaded from ``litrev.yaml``.
    model_alias : str
        Model identifier used to locate the correct result subdirectory.
    steps_filter : list of str, optional
        If provided, only run steps whose ``name`` appears in this list.
        When ``None``, all enabled steps run.

    Returns
    -------
    dict of {str: Any}
        Mapping from step name to its result (or an error dict if the step
        failed).  Example::

            {
                "aggregate": {"year_distribution": 42, ...},
                "stats": {"metadata.citation.year": {...}},
                "report_md": "# Project Report...",
            }
    """
    results: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Discover user-provided scripts from the project's scripts/ dir
    # ------------------------------------------------------------------
    # The scripts directory is located relative to the state file path.
    # If state_file is absolute, derive from it; otherwise resolve relative
    # to the current working directory.
    state_dir = (
        os.path.dirname(config.state_file) if os.path.isabs(config.state_file)
        else os.path.join(os.getcwd(), os.path.dirname(config.state_file))
    )
    scripts_dir = os.path.join(state_dir, "scripts")
    if os.path.isdir(scripts_dir):
        discover_processors([scripts_dir])

    # ------------------------------------------------------------------
    # Run each pipeline step
    # ------------------------------------------------------------------
    for step in config.postproc_pipeline:
        # Skip disabled steps and those excluded by the filter
        if not step.enabled:
            continue
        if steps_filter and step.name not in steps_filter:
            continue

        print(f"\n  >> Running post-processing step: {step.name}...")
        processor = instantiate_processor(step)
        try:
            result = processor.run(config, model_alias)
            results[step.name] = result
            print(f"  [OK] Step '{step.name}' complete")
        except Exception as e:
            # Record the error and continue with the next step
            print(f"  [FAIL] Step '{step.name}' failed: {e}")
            results[step.name] = {"error": str(e)}

    return results