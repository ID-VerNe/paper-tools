"""
resolver.py - Recursively resolve input and include commands.

Exports:
    resolve_inputs(filepath) -> str
        Starting from a main .tex file, walks every \input{...} and
        \include{...} encountered, replacing the command with the child
        file's content. Falls back to the root search directory when a
        path is not found relative to the current file (mimicking
        LaTeX's search-path behaviour).
"""

import os
import re
import sys


def _resolve_path(filepath, ref):
    """Resolve a \\input/\\include reference relative to *filepath*."""
    ref = ref.strip()
    if not ref.endswith('.tex'):
        ref += '.tex'
    if os.path.isabs(ref):
        return ref
    base_dir = os.path.dirname(os.path.abspath(filepath))
    return os.path.normpath(os.path.join(base_dir, ref))


def resolve_inputs(filepath, seen=None, root_dir=None):
    """Recursively resolve \\input{...} and \\include{...} commands.

    Parameters
    ----------
    filepath : str
        Path to the current .tex file.
    seen : set | None
        Absolute paths already resolved (cycle detection).
    root_dir : str | None
        Main document directory.  Used as a fallback when a child path
        is not found relative to the current file.

    Returns
    -------
    str
        Full expanded text with all inputs inlined.
    """
    if seen is None:
        seen = set()
    if root_dir is None:
        root_dir = os.path.dirname(os.path.abspath(filepath))

    abs_path = os.path.normpath(os.path.abspath(filepath))
    if abs_path in seen:
        return ''
    seen.add(abs_path)

    if not os.path.isfile(abs_path):
        print(f'Warning: file not found: {abs_path}', file=sys.stderr)
        return ''

    with open(abs_path, 'r', encoding='utf-8') as f:
        text = f.read()

    input_pat = re.compile(r'\\(?:input|include)\{(.+?)\}')
    resolved = []
    last_end = 0

    for m in input_pat.finditer(text):
        resolved.append(text[last_end:m.start()])
        ref = m.group(1).strip()
        child_path = _resolve_path(abs_path, ref)
        if not os.path.isfile(child_path):
            child_path = _resolve_path(
                os.path.join(root_dir, 'dummy.tex'), ref
            )
        resolved.append(resolve_inputs(child_path, seen, root_dir))
        last_end = m.end()

    resolved.append(text[last_end:])
    return ''.join(resolved)