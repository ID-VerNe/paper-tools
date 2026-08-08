"""
processor.py — Orchestrates the full LaTeX-to-text pipeline.

Exports:
    process_tex(text) -> str
        Runs the complete pipeline: extract body, remove comments,
        protect environments, process abstract, remove metadata,
        convert sections, formatting, citations, footnotes, lists,
        special characters, remaining commands, restore environments,
        and finally clean up whitespace.
"""

import re

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
)
from tex2txt.environment import protect_environments, PROTECTED_ENVS


def _extract_body(text):
    """Extract content between ``\\begin{document}`` and ``\\end{document}``."""
    body_start = text.find(r'\begin{document}')
    body_end = text.find(r'\end{document}')

    if body_start >= 0:
        text = text[body_start + len(r'\begin{document}'):]
    if body_end >= 0:
        text = text[:body_end]

    text = text.replace(r'\begin{document}', '')
    text = text.replace(r'\end{document}', '')
    return text


def process_tex(text):
    """Run the full LaTeX-to-text processing pipeline.

    Parameters
    ----------
    text : str
        Raw LaTeX source (already resolved via ``resolve_inputs``).

    Returns
    -------
    str
        Plain-text output.
    """
    # Step 0: Extract body only
    text = _extract_body(text)

    # Step 1: Remove comments
    text = remove_comments(text)

    # Step 2: Protect environments (preserve as-is)
    text, protected_blocks = protect_environments(text, PROTECTED_ENVS)

    # Step 3: Process abstract
    text = process_abstract(text)

    # Step 4: Remove metadata
    text = remove_metadata(text)

    # Step 5: Process sections
    text = process_sections(text)

    # Step 6: Process formatting
    text = process_formatting(text)

    # Step 7: Process citations
    text = process_citations(text)

    # Step 8: Process footnotes
    text = process_footnotes(text)

    # Step 9: Process lists
    text = process_lists(text)

    # Step 10: Process special characters
    text = process_special_chars(text)

    # Step 11: Remove remaining LaTeX commands
    text = _remove_remaining_commands(text)

    # Step 12: Restore protected environments
    for placeholder, original in protected_blocks.items():
        text = text.replace(placeholder, original)

    # Step 13: Cleanup whitespace
    text = _cleanup_whitespace(text)

    # Step 14: Remove any remaining \x00 placeholders (shouldn't happen)
    text = re.sub(r'\x00[A-Z_]+\d+\x00', '', text)

    return text