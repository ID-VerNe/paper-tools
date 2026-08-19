# paper-tools

A collection of tools for LaTeX paper processing.

## tex2txt — LaTeX to Plain Text Converter

Recursively resolves `\input`/`\include` and converts the LaTeX source to plain text, preserving:
- Mathematical formulas (as LaTeX source)
- Figures, tables, algorithms, tikzpictures (as-is)
- Abstract as a Markdown heading

### Quick Start (GUI)

Double-click `tex2txt_gui.bat` in the project root.

### CLI Usage

```bash
uv run python -m tex2txt.cli path/to/main.tex
```

### GUI Usage

```bash
uv run python -m tex2txt.gui
```

### Architecture

```
paper-tools/
├── tex2txt/               # Core package (modular, extensible)
│   ├── __init__.py        # Public API re-exports
│   ├── __main__.py        # python -m tex2txt entry point
│   ├── cli.py             # CLI entry point
│   ├── gui.py             # tkinter GUI
│   ├── resolver.py        # \input / \include recursive resolution
│   ├── environment.py     # Environment protection/restoration
│   ├── transforms.py      # Individual LaTeX→text transforms
│   └── processor.py       # Pipeline orchestrator
├── tex2txt_gui.bat        # Double-click to launch GUI
├── tex2txt_cli.bat        # CLI wrapper
├── pyproject.toml         # uv project config
└── README.md
```

### Adding new transforms

Add a function to `transforms.py`, then insert it into the pipeline in `processor.py:process_tex()`.  Each function receives and returns a single string, making them composable and testable independently.