"""
environment.py — Protect and restore LaTeX environments.

Certain environments (equations, figures, algorithms, tikzpictures, …)
are preserved as-is during the plain-text conversion.  This module
replaces top-level instances of those environments with unique
placeholders, runs the rest of the pipeline, and then restores the
original content.

Exports:
    protect_environments(text, protected_envs) -> (str, dict)
    PROTECTED_ENVS     — the default set of environments to preserve
"""

import re

# ── Default set of environments preserved as-is ─────────────────────
PROTECTED_ENVS = {
    'figure', 'figure*', 'table', 'table*',
    'tikzpicture', 'pgfplotstable', 'pgfpicture',
    'algorithm', 'algorithmic',
    'equation', 'equation*', 'eqnarray', 'eqnarray*',
    'align', 'align*', 'gather', 'gather*',
    'multline', 'multline*', 'alignat', 'alignat*',
    'flalign', 'flalign*',
    'subfigure', 'subfig',
}


def _ensure_set(envs):
    """Normalise environment names: add ``*``-less variants for lookup."""
    expanded = set()
    for e in envs:
        expanded.add(e)
        if e.endswith('*'):
            expanded.add(e[:-1])
    return expanded


def protect_environments(text, protected_envs):
    """Replace outermost protected environments with unique placeholders.

    Parameters
    ----------
    text : str
        The LaTeX source.
    protected_envs : set of str
        Environment names to preserve (e.g. ``{'figure', 'equation'}``).

    Returns
    -------
    (modified_text, placeholder_map)
        *modified_text* has placeholders (``\\x00BLOCK_N\\x00``) in place
        of each protected environment.
        *placeholder_map* maps placeholder → original block content.
    """
    protected_set = _ensure_set(protected_envs)
    env_pat = re.compile(r'\\(begin|end)\{(\w+)\*?\}')

    # Collect all begin/end pairs using a stack
    all_envs = []   # [(name, start, end)]
    stack = []      # [(name, start)]

    for m in env_pat.finditer(text):
        if m.group(1) == 'begin':
            stack.append((m.group(2), m.start()))
        else:
            if stack and stack[-1][0] == m.group(2):
                name, start = stack.pop()
                all_envs.append((name, start, m.end()))

    all_envs.sort(key=lambda x: x[1])

    # Keep only top-level (non-overlapping) environments
    top_level = []
    last_end = 0
    for name, start, end in all_envs:
        if start >= last_end:
            top_level.append((name, start, end))
            last_end = end

    # Replace protected top-level blocks with placeholders
    blocks = {}
    counter = 0
    result = text
    offset = 0

    for name, start, end in top_level:
        if name not in protected_set:
            continue
        orig = text[start:end]
        placeholder = f'\x00BLOCK_{counter}\x00'
        blocks[placeholder] = orig
        counter += 1

        adj_start = start + offset
        adj_end = end + offset
        result = result[:adj_start] + placeholder + result[adj_end:]
        offset += len(placeholder) - (end - start)

    return result, blocks