"""
tex2txt - Convert LaTeX source to plain text with input/include expansion.

Package root: re-exports the public API for use by GUI and external callers.
"""

from tex2txt.resolver import resolve_inputs
from tex2txt.environment import protect_environments, PROTECTED_ENVS
from tex2txt.transforms import (
    remove_comments,
    process_abstract,
    remove_metadata,
    process_sections,
    process_formatting,
    process_citations,
    process_footnotes,
    process_lists,
    process_special_chars,
    _cleanup_whitespace,
    _remove_remaining_commands,
    _remove_command_with_braces,
    _remove_command_two_braces,
)
from tex2txt.processor import process_tex

__all__ = [
    'resolve_inputs',
    'protect_environments',
    'process_tex',
    'PROTECTED_ENVS',
]