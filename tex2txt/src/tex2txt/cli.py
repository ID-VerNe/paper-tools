"""
cli.py — Command-line entry point for tex2txt.

Usage:
    python -m tex2txt.cli <main.tex>
    python -m tex2txt <main.tex>

Output is written to the same directory as the input file, with a ``.txt``
extension.
"""

import sys
import os

from tex2txt.resolver import resolve_inputs
from tex2txt.processor import process_tex


def main():
    if len(sys.argv) != 2:
        print(
            f'Usage: python {os.path.basename(sys.argv[0])} <main.tex>',
            file=sys.stderr,
        )
        sys.exit(1)

    main_tex = sys.argv[1]
    if not os.path.isfile(main_tex):
        print(f'Error: file not found: {main_tex}', file=sys.stderr)
        sys.exit(1)

    print(f'Resolving \\input/\\include from {main_tex} ...', file=sys.stderr)
    full_text = resolve_inputs(main_tex)

    print('Processing LaTeX → plain text ...', file=sys.stderr)
    result = process_tex(full_text)

    output_path = os.path.splitext(main_tex)[0] + '.txt'
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(result)

    chars = len(result)
    lines = result.count('\n') + 1
    print(f'Done: {output_path} ({lines} lines, {chars} chars)', file=sys.stderr)


if __name__ == '__main__':
    main()