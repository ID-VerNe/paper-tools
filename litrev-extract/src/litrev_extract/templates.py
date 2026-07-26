"""Prompt template loading and rendering.

Manages loading ``.txt`` prompt template files from disk and resolving
placeholder variables (such as ``{content}``) within the user prompt.

Template search order
---------------------
When locating a template file referenced by a ``PromptDef``, the system
searches in the following order:

1. **Absolute path** -- If the template path is absolute and the file exists,
   it is used directly.
2. **Relative to base directory** -- e.g. ``<project_root>/prompts/<file>``.
3. **Relative to configured templates directory** -- e.g. ``<templates_dir>/<file>``.
4. **Fallback** -- If none of the above exist, the path string itself is
   treated as the template content (inline usage).

Variable substitution
---------------------
- ``{content}`` is *always* substituted with the document text (subject to
  optional truncation).
- Additional user-supplied variables are substituted after ``{content}``.
- Unknown ``{variables}`` are left as-is (no error is raised).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from .models import PromptDef


class TemplateManager:
    """Loads and renders prompt template files.

    This class has two responsibilities:

    1. **Loading**: Resolve a ``PromptDef``'s template file path to an actual
       file on disk and read its contents (with a multi-location search).
    2. **Rendering**: Substitute ``{variables}`` (notably ``{content}``) in
       the loaded template with runtime values.

    Attributes:
        templates_dir: Optional override directory that is searched when a
                       template file cannot be found relative to the base
                       directory.

    Example:
        >>> tmgr = TemplateManager(templates_dir="/opt/templates")
        >>> prompt_def = PromptDef(file="extract_metadata.txt", system_prompt="...")
        >>> prompts = tmgr.load(prompt_def, base_dir="/home/user/project/prompts")
        >>> print(prompts["system_prompt"], prompts["user_prompt"])
    """

    def __init__(self, templates_dir: Optional[str] = None):
        """Initialise the template manager.

        Args:
            templates_dir: Optional fallback directory for locating template
                           files. If a template is not found relative to the
                           base directory, this directory is searched next.
        """
        self.templates_dir = templates_dir

    def load(self, prompt_def: PromptDef, base_dir: str) -> Dict[str, str]:
        """Load the template file for a PromptDef and return system + user prompts.

        The system prompt is taken directly from ``prompt_def.system_prompt``
        (it is a hardcoded string, not a file reference). The user prompt is
        loaded from the file referenced by ``prompt_def.file`` via
        ``_load_user_template``.

        Args:
            prompt_def:  The prompt definition containing system prompt text
                         and a user template file reference.
            base_dir:    Base directory to search for the template file,
                         typically the project root.

        Returns:
            A dict with two keys:
            - ``"system_prompt"``: The fixed system prompt string.
            - ``"user_prompt"``:   The loaded (but not yet rendered) template
                                  from the file, or the literal path string
                                  if no file was found.
        """
        system = prompt_def.system_prompt
        # Prefer the pre-loaded user_template from PromptDef (set at config
        # load time from either file or inline YAML).  Fall back to loading
        # from the file path at runtime.
        if prompt_def.user_template:
            user = prompt_def.user_template
        else:
            user = self._load_user_template(prompt_def.file, base_dir)
        return {"system_prompt": system, "user_prompt": user}

    def _load_user_template(self, file_path: str, base_dir: str) -> str:
        """Load a template file from disk, searching multiple locations.

        The search order is:
            1. Absolute path (if *file_path* is absolute and exists).
            2. Relative to *base_dir*.
            3. Relative to ``self.templates_dir`` (if configured).
            4. Fallback: return *file_path* as-is (inline template).

        Args:
            file_path: Path to the template file, as stored in the PromptDef.
            base_dir:  Primary search directory, typically the project root.

        Returns:
            The file contents as a string if found, otherwise the literal
            *file_path* string (allowing callers to use inline templates).
        """
        # Step 1: absolute path
        p = Path(file_path)
        if p.is_absolute() and p.exists():
            return p.read_text(encoding="utf-8")

        # Step 2: relative to the base directory (e.g. project root)
        abs_path = Path(base_dir) / p
        if abs_path.exists():
            return abs_path.read_text(encoding="utf-8")

        # Step 3: relative to the configured templates directory
        if self.templates_dir:
            alt = Path(self.templates_dir) / p
            if alt.exists():
                return alt.read_text(encoding="utf-8")

        # Step 4: fallback -- treat the path as the literal template content.
        # This supports use cases where the caller passes the template text
        # as a string instead of storing it in a file.
        return file_path

    @staticmethod
    def render(
        template: str,
        content: str,
        variables: Optional[Dict[str, str]] = None,
        truncation: Optional[int] = None,
    ) -> str:
        """Render a template by substituting variables into it.

        ``{content}`` is always substituted with the document text. If
        *truncation* is set, the content is clipped to that many characters
        *before* substitution, which keeps token usage predictable.

        Additional variables passed via *variables* are substituted after
        ``{content}``, so they can reference or replace parts of the template
        that are independent of the document content.

        Args:
            template:    The raw template string containing ``{variable}``
                         placeholders.
            content:     The document text to substitute for ``{content}``.
            variables:   Optional dict of ``{name: value}`` pairs for
                         additional placeholder substitution.
            truncation:  Maximum number of characters for *content*. Content
                         longer than this is silently truncated. ``None``
                         means no truncation.

        Returns:
            The template string with all recognised variables replaced by
            their values. Unknown ``{variables}`` are left unchanged.
        """
        # Apply truncation before substitution so we don't waste tokens on
        # content that will be clipped anyway.
        text = content
        if truncation is not None and len(text) > truncation:
            text = text[:truncation]

        # Always substitute {content} first
        result = template.replace("{content}", text)

        # Then substitute any extra user-supplied variables
        if variables:
            for key, val in variables.items():
                result = result.replace("{" + key + "}", val)

        return result

    @staticmethod
    def load_template_file(path: str) -> Optional[str]:
        """Load a template file from an absolute or relative path.

        This is a convenience utility for callers that already know the exact
        file path and do not need the multi-location search logic of
        ``_load_user_template``.

        Args:
            path: Absolute or relative path to a template file on disk.

        Returns:
            The file content as a string if the file exists, or ``None`` if
            the file does not exist or cannot be read.
        """
        p = Path(path)
        if p.exists():
            return p.read_text(encoding="utf-8")
        return None