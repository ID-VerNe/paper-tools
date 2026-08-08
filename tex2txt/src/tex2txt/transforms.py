"""
transforms.py — LaTeX-to-text transformation functions.

Each function in this module performs a single, composable
transformation on LaTeX source text.  They are designed to be
called in sequence by :func:`processor.process_tex`.
"""

import re

# ── Special character mapping ────────────────────────────────────────
SPECIAL_CHARS = [
    (r'\\&', '&'),
    (r'\\#', '#'),
    (r'\\\$', '$'),
    (r'\\_', '_'),
    (r'\\\{', '{'),
    (r'\\\}', '}'),
    (r'\\textasciitilde\{\}', '~'),
    (r'\\textasciitilde', '~'),
    (r'\\textbackslash\{\}', '\\'),
    (r'\\textbackslash', '\\'),
    (r'\\textasciicircum\{\}', '^'),
    (r'\\textasciicircum', '^'),
    (r'\\textasciigrave\{\}', '`'),
    (r'\\textasciigrave', '`'),
    (r'\\textquotedbl\{\}', '"'),
    (r'\\textquotedbl', '"'),
    (r'\\textquoteleft\{\}', '`'),
    (r'\\textquoteleft', '`'),
    (r'\\textquoteright\{\}', "'"),
    (r'\\textquoteright', "'"),
    (r'\\textdagger', '†'),
    (r'\\textsection', '§'),
    (r'\\texteuro', '€'),
    (r'\\textdegree', '°'),
    (r'\\textellipsis', '...'),
    (r'\\textemdash', '—'),
    (r'\\textendash', '–'),
    (r'\\textbullet', '•'),
    (r'\\P', '¶'),
    (r'\\S', '§'),
    (r'\\dots', '...'),
    (r'\\ldots', '...'),
]

# ── Section heading level mapping ────────────────────────────────────
SECTION_LEVELS = {
    'part': '# ',
    'chapter': '# ',
    'section': '# ',
    'subsection': '## ',
    'subsubsection': '### ',
    'paragraph': '#### ',
    'subparagraph': '##### ',
}

# ── Metadata patterns to remove (within the body) ────────────────────
METADATA_PATTERNS = [
    (r'\\maketitle', ''),
    (r'\\IEEEpeerreviewmaketitle', ''),
    (r'\\bibliographystyle\{.*?\}', ''),
    (r'\\bibliography\{.*?\}', ''),
    (r'\\begin\{IEEEkeywords\}.*?\\end\{IEEEkeywords\}', ''),
    (r'\\IEEEpubid\{.*?\}', ''),
    (r'\\IEEEaftertitletext\{.*?\}', ''),
]


# ═══════════════════════════════════════════════════════════════════════
#  Helper: brace-counting command removal
# ═══════════════════════════════════════════════════════════════════════

def _remove_command_with_braces(text, cmd_name):
    """Remove ``\\cmd_name{...}`` where ``...`` may contain nested braces.

    Uses brace-counting to find the matching closing brace, correctly
    handling arbitrary nesting depth.
    """
    pattern = re.compile(r'\\' + re.escape(cmd_name) + r'\{')
    result = []
    pos = 0
    for m in pattern.finditer(text):
        result.append(text[pos:m.start()])
        start = m.end()
        brace_depth = 1
        while brace_depth > 0 and start < len(text):
            if text[start] == '{':
                brace_depth += 1
            elif text[start] == '}':
                brace_depth -= 1
            start += 1
        pos = start
    result.append(text[pos:])
    return ''.join(result)


def _remove_command_two_braces(text, cmd_name):
    """Remove ``\\cmd_name{arg1}{arg2}`` with nested braces in both args.

    Unlike a simple regex, this handles nested braces inside each of
    the two brace groups via brace-counting.  Whitespace between the
    two closing/opening braces is also handled.
    """
    pattern = re.compile(r'\\' + re.escape(cmd_name) + r'\{')
    result = []
    pos = 0
    for m in pattern.finditer(text):
        result.append(text[pos:m.start()])
        start = m.end()
        brace_depth = 1
        while brace_depth > 0 and start < len(text):
            if text[start] == '{':
                brace_depth += 1
            elif text[start] == '}':
                brace_depth -= 1
            start += 1
        while start < len(text) and text[start] in ' \t\n\r':
            start += 1
        if start < len(text) and text[start] == '{':
            start += 1
            brace_depth = 1
            while brace_depth > 0 and start < len(text):
                if text[start] == '{':
                    brace_depth += 1
                elif text[start] == '}':
                    brace_depth -= 1
                start += 1
        pos = start
    result.append(text[pos:])
    return ''.join(result)


# ═══════════════════════════════════════════════════════════════════════
#  Individual transforms
# ═══════════════════════════════════════════════════════════════════════

def remove_comments(text):
    """Remove LaTeX comments (unescaped ``%`` to end of line)."""
    PERCENT_MARKER = '\x00PERCENT\x00'
    text = text.replace('\\%', PERCENT_MARKER)
    text = re.sub(r'%.*$', '', text, flags=re.MULTILINE)
    text = text.replace(PERCENT_MARKER, '%')
    return text


def process_special_chars(text):
    """Convert LaTeX special-character escapes to Unicode."""
    for pattern, replacement in SPECIAL_CHARS:
        text = text.replace(pattern, replacement)
    text = text.replace('~', ' ')
    return text


def process_abstract(text):
    """Extract and format the abstract.

    Surrounds the abstract content with a Markdown-level-1 heading.
    """
    pat = re.compile(
        r'\\begin\{abstract\}(.*?)\\end\{abstract\}',
        re.DOTALL,
    )

    def replacer(m):
        content = m.group(1).strip()
        return f'# Abstract\n\n{content}\n\n'

    return pat.sub(replacer, text)


def remove_metadata(text):
    """Remove document-structure metadata commands."""
    for pattern, replacement in METADATA_PATTERNS:
        text = re.sub(pattern, replacement, text, flags=re.DOTALL)
    return text


def process_sections(text):
    """Convert ``\\section``, ``\\subsection`` etc. to Markdown headings."""
    sec_names = sorted(SECTION_LEVELS.keys(), key=len, reverse=True)
    for name in sec_names:
        marker = SECTION_LEVELS[name]
        pat = re.compile(r'\\' + re.escape(name) + r'\*?\{(.*?)\}', re.DOTALL)
        text = pat.sub(lambda m, mk=marker: f'{mk}{m.group(1).strip()}\n\n', text)
    return text


def process_formatting(text):
    """Convert ``\\textbf``, ``\\textit``, ``\\emph`` to Markdown ``**`` / ``*``."""
    text = re.sub(r'\\textbf\{(.*?)\}', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'\\textit\{(.*?)\}', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'\\emph\{(.*?)\}', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'\\textsf\{(.*?)\}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\texttt\{(.*?)\}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\textsc\{(.*?)\}', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'\\textsuperscript\{(.*?)\}', r'^\1', text, flags=re.DOTALL)
    text = re.sub(r'\\textsubscript\{(.*?)\}', r'_\1', text, flags=re.DOTALL)
    return text


def process_citations(text):
    """Replace ``\\cite``, ``\\ref``, ``\\label`` with placeholders."""
    text = re.sub(r'\\cite(?:\[.*?\])?\{.*?\}', '[citation]', text)
    text = re.sub(r'\\ref\{.*?\}', '[ref]', text)
    text = re.sub(r'\\label\{.*?\}', '[label]', text)
    text = re.sub(r'\\pageref\{.*?\}', '[pageref]', text)
    text = re.sub(r'\\autoref\{.*?\}', '[ref]', text)
    text = re.sub(r'\\eqref\{.*?\}', '[eqref]', text)
    return text


def process_footnotes(text):
    """Inline ``\\footnote{...}`` content in parentheses."""
    text = re.sub(
        r'\\footnote\{(.*?)\}',
        lambda m: f' ({m.group(1).strip()}) ',
        text,
        flags=re.DOTALL,
    )
    return text


def process_lists(text):
    """Convert ``itemize`` / ``enumerate`` environments to Markdown lists."""
    text = re.sub(
        r'\\begin\{itemize\}(.*?)\\end\{itemize\}',
        _process_itemize,
        text,
        flags=re.DOTALL,
    )
    text = re.sub(
        r'\\begin\{enumerate\}(.*?)\\end\{enumerate\}',
        _process_enumerate,
        text,
        flags=re.DOTALL,
    )
    return text


def _process_itemize(m):
    content = m.group(1)
    items = [i.strip() for i in re.split(r'\\item\s*', content) if i.strip()]
    return '\n'.join(f'- {item}' for item in items) + '\n\n'


def _process_enumerate(m):
    content = m.group(1)
    items = [i.strip() for i in re.split(r'\\item\s*', content) if i.strip()]
    return '\n'.join(f'{i}. {item}' for i, item in enumerate(items, 1)) + '\n\n'


def _cleanup_whitespace(text):
    """Remove excessive blank lines and trailing whitespace."""
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' +\n', '\n', text)
    return text.strip()


def _remove_remaining_commands(text):
    """Remove remaining LaTeX commands not handled by earlier transforms."""
    # Commands with nested braces
    text = _remove_command_with_braces(text, 'title')
    text = _remove_command_with_braces(text, 'author')
    text = _remove_command_with_braces(text, 'thanks')
    text = _remove_command_with_braces(text, 'IEEEmembership')

    # Spacing commands
    text = re.sub(
        r'\\' + r'(?:enspace|quad|qquad|hfill|vspace\*?|hspace\*?)\{.*?\}',
        '', text,
    )
    text = re.sub(r'\\' + r'(?:enspace|quad|qquad|hfill)', '', text)
    text = re.sub(r'\\(?:newline|\\\\s*)', '\n', text)
    text = re.sub(r'\\noindent', '', text)
    text = re.sub(r'\\(?:small|normalsize|footnotesize|scriptsize|tiny)', '', text)
    text = re.sub(r'\\hrule', '', text)
    text = re.sub(r'\\centering', '', text)
    text = re.sub(r'\\hfill', '', text)
    text = re.sub(r'\\vfill', '', text)

    # Preamble / misc commands
    text = re.sub(r'\\pgfplotsset\{.*?\}', '', text)
    text = re.sub(r'\\usepgfplotslibrary\{.*?\}', '', text)
    text = re.sub(r'\\usetikzlibrary\{.*?\}', '', text)
    text = re.sub(r'\\hyphenation\{.*?\}', '', text)
    text = re.sub(r'\\setlength\{.*?\}\{.*?\}', '', text)

    # Text-case / markboth
    text = re.sub(r'\\MakeLowercase\{(.*?)\}', r'\1', text)
    text = _remove_command_two_braces(text, 'markboth')

    # Misc
    text = re.sub(r'\\IEEEPARstart\{.*?\}\{.*?\}', '', text)
    text = re.sub(r'\\and\s+', ' ', text)
    text = re.sub(
        r'\\begin\{IEEEbiographynophoto\}.*?\\end\{IEEEbiographynophoto\}',
        '', text, flags=re.DOTALL,
    )
    text = _remove_command_with_braces(text, 'resizebox')
    text = re.sub(r'\\resizebox\{.*?\}', '', text)

    return text