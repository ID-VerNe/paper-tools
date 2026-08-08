"""
tex2txt GUI — A graphical interface for the tex2txt LaTeX-to-text converter.

Built with tkinter (standard library, no external dependencies).

Usage:
    uv run python -m tex2txt.gui          # from paper-tools/
    python -m tex2txt.gui                 # with tex2txt on PYTHONPATH
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import os

from tex2txt.resolver import resolve_inputs
from tex2txt.processor import process_tex


# ═══════════════════════════════════════════════════════════════════════
#  Styles
# ═══════════════════════════════════════════════════════════════════════

class AppStyle:
    """Centralised style constants."""

    BG = '#f5f5f5'
    FG = '#1a1a1a'
    ACCENT = '#2563eb'
    ACCENT_HOVER = '#1d4ed8'
    SUCCESS = '#16a34a'
    WARNING = '#d97706'
    ERROR = '#dc2626'
    CARD_BG = '#ffffff'
    BORDER = '#e5e7eb'
    TEXT_SECONDARY = '#6b7280'
    FONT = ('Segoe UI', 10)
    FONT_BOLD = ('Segoe UI', 10, 'bold')
    FONT_HEADING = ('Segoe UI', 14, 'bold')
    FONT_MONO = ('Consolas', 10)


# ═══════════════════════════════════════════════════════════════════════
#  Main Application
# ═══════════════════════════════════════════════════════════════════════

class Tex2TxtGUI(tk.Tk):
    """Main application window."""

    def __init__(self):
        super().__init__()

        self.title('tex2txt - LaTeX to Plain Text Converter')
        self.geometry('780x620')
        self.minsize(620, 500)
        self.configure(bg=AppStyle.BG)

        self._input_path = tk.StringVar()
        self._status = tk.StringVar(value='Ready')
        self._running = False
        self._result_text = ''
        self._preview_label = 'Preview area'

        self._build_ui()
        self._bind_accelerators()

    # ── UI Construction ──────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_input_section()
        self._build_output_section()
        self._build_button_bar()
        self._build_preview()
        self._build_status_bar()

    def _build_header(self):
        header = tk.Frame(self, bg=AppStyle.BG)
        header.pack(fill=tk.X, padx=20, pady=(16, 4))

        tk.Label(
            header,
            text='tex2txt',
            font=AppStyle.FONT_HEADING,
            bg=AppStyle.BG,
            fg=AppStyle.FG,
        ).pack(side=tk.LEFT)

        tk.Label(
            header,
            text='LaTeX → Plain Text Converter',
            font=AppStyle.FONT,
            bg=AppStyle.BG,
            fg=AppStyle.TEXT_SECONDARY,
        ).pack(side=tk.LEFT, padx=(10, 0))

    def _build_input_section(self):
        card = tk.Frame(
            self, bg=AppStyle.CARD_BG, padx=16, pady=14,
            highlightbackground=AppStyle.BORDER,
            highlightthickness=1,
        )
        card.pack(fill=tk.X, padx=20, pady=(8, 16))

        tk.Label(
            card, text='Input File', font=AppStyle.FONT_BOLD,
            bg=AppStyle.CARD_BG, fg=AppStyle.FG,
        ).pack(anchor=tk.W)

        row = tk.Frame(card, bg=AppStyle.CARD_BG)
        row.pack(fill=tk.X, pady=(6, 0))

        self._input_entry = tk.Entry(
            row, textvariable=self._input_path,
            font=AppStyle.FONT, bg=AppStyle.BG,
            fg=AppStyle.FG, relief=tk.FLAT,
            highlightbackground=AppStyle.BORDER,
            highlightcolor=AppStyle.ACCENT,
            highlightthickness=2,
        )
        self._input_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, ipady=4)

        self._browse_btn = tk.Button(
            row, text='Browse', font=AppStyle.FONT,
            bg=AppStyle.ACCENT, fg='#ffffff',
            activebackground=AppStyle.ACCENT_HOVER,
            activeforeground='#ffffff',
            relief=tk.FLAT, padx=14, cursor='hand2',
            command=self._browse_input,
            underline=0,
        )
        self._browse_btn.pack(side=tk.RIGHT, padx=(8, 0))

    def _build_separator(self):
        sep = ttk.Separator(self, orient='horizontal')
        sep.pack(fill=tk.X, padx=20, pady=10)

    def _build_output_section(self):
        card = tk.Frame(
            self, bg=AppStyle.CARD_BG, padx=16, pady=14,
            highlightbackground=AppStyle.BORDER,
            highlightthickness=1,
        )
        card.pack(fill=tk.X, padx=20, pady=(0, 8))

        tk.Label(
            card, text='Output', font=AppStyle.FONT_BOLD,
            bg=AppStyle.CARD_BG, fg=AppStyle.FG,
        ).pack(anchor=tk.W)

        self._output_label = tk.Label(
            card, text='(same directory as input, .txt extension)',
            font=AppStyle.FONT, bg=AppStyle.CARD_BG,
            fg=AppStyle.TEXT_SECONDARY, anchor=tk.W,
        )
        self._output_label.pack(fill=tk.X, pady=(4, 0))

    def _build_button_bar(self):
        bar = tk.Frame(self, bg=AppStyle.BG, padx=20, pady=10)
        bar.pack(fill=tk.X)

        self._run_btn = tk.Button(
            bar, text='Convert', font=AppStyle.FONT_BOLD,
            bg=AppStyle.ACCENT, fg='#ffffff',
            activebackground=AppStyle.ACCENT_HOVER,
            activeforeground='#ffffff',
            relief=tk.FLAT, padx=20, pady=4, cursor='hand2',
            command=self._run_conversion,
            underline=0,
        )
        self._run_btn.pack(side=tk.LEFT)

        self._open_btn = tk.Button(
            bar, text='Open Output', font=AppStyle.FONT,
            bg=AppStyle.CARD_BG, fg=AppStyle.FG,
            activebackground=AppStyle.BORDER,
            relief=tk.FLAT, padx=14, pady=4, cursor='hand2',
            state=tk.DISABLED,
            command=self._open_output,
        )
        self._open_btn.pack(side=tk.LEFT, padx=(8, 0))

    def _build_preview(self):
        container = tk.Frame(
            self, bg=AppStyle.CARD_BG, padx=0, pady=0,
            highlightbackground=AppStyle.BORDER,
            highlightthickness=1,
        )
        container.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 4))

        # Header bar
        header = tk.Frame(container, bg=AppStyle.CARD_BG, padx=12, pady=6)
        header.pack(fill=tk.X)

        tk.Label(
            header, text='Preview', font=AppStyle.FONT_BOLD,
            bg=AppStyle.CARD_BG, fg=AppStyle.FG,
        ).pack(side=tk.LEFT)

        self._char_count = tk.Label(
            header, text='', font=AppStyle.FONT,
            bg=AppStyle.CARD_BG, fg=AppStyle.TEXT_SECONDARY,
        )
        self._char_count.pack(side=tk.RIGHT)

        # Scrollable text area
        text_frame = tk.Frame(container, bg=AppStyle.CARD_BG)
        text_frame.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))

        scrollbar = tk.Scrollbar(text_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self._preview = tk.Text(
            text_frame, font=AppStyle.FONT_MONO,
            bg=AppStyle.BG, fg=AppStyle.FG,
            wrap=tk.WORD, padx=8, pady=8,
            relief=tk.FLAT,
            yscrollcommand=scrollbar.set,
        )
        self._preview.pack(fill=tk.BOTH, expand=True)
        scrollbar.config(command=self._preview.yview)
        self._preview.insert('1.0', 'Select a .tex file and click Convert to see a preview.')
        self._preview.config(state=tk.DISABLED)

    def _build_status_bar(self):
        bar = tk.Frame(self, bg=AppStyle.BORDER, padx=16, pady=4)
        bar.pack(fill=tk.X, side=tk.BOTTOM)

        self._status_label = tk.Label(
            bar, textvariable=self._status,
            font=AppStyle.FONT, bg=AppStyle.BORDER,
            fg=AppStyle.TEXT_SECONDARY, anchor=tk.W,
        )
        self._status_label.pack(fill=tk.X)

    # ── Actions ──────────────────────────────────────────────────────

    def _bind_accelerators(self):
        self.bind('<Alt-b>', lambda e: self._browse_input())
        self.bind('<Alt-c>', lambda e: self._run_conversion())
        self.bind('<Control-Return>', lambda e: self._run_conversion())

    def _browse_input(self):
        path = filedialog.askopenfilename(
            title='Select LaTeX Main File',
            filetypes=[('LaTeX files', '*.tex'), ('All files', '*.*')],
        )
        if path:
            self._input_path.set(path)
            self._update_output_label()
            self._status.set(f'Selected: {os.path.basename(path)}')

    def _update_output_label(self):
        inp = self._input_path.get()
        if inp:
            out = os.path.splitext(inp)[0] + '.txt'
            self._output_label.config(text=out, fg=AppStyle.FG)

    def _run_conversion(self):
        if self._running:
            return

        inp = self._input_path.get()
        if not inp:
            messagebox.showwarning('No Input', 'Please select a .tex file first.')
            return
        if not os.path.isfile(inp):
            messagebox.showerror('File Not Found', f'File does not exist:\n{inp}')
            return

        self._running = True
        self._run_btn.config(state=tk.DISABLED, text='Converting...')
        self._status.set('Resolving embedded files...')
        self._set_preview_text('', '')
        self._open_btn.config(state=tk.DISABLED)

        thread = threading.Thread(target=self._do_convert, args=(inp,), daemon=True)
        thread.start()

    def _do_convert(self, inp):
        try:
            full_text = resolve_inputs(inp)
            self.after(0, lambda: self._status.set('Processing LaTeX to plain text...'))
            result = process_tex(full_text)

            out = os.path.splitext(inp)[0] + '.txt'
            with open(out, 'w', encoding='utf-8') as f:
                f.write(result)

            self._result_text = result
            lines = result.count('\n') + 1
            chars = len(result)

            self.after(0, lambda: self._on_success(out, lines, chars))
        except Exception as e:
            self.after(0, lambda: self._on_error(str(e)))

    def _on_success(self, out_path, lines, chars):
        self._status.set(f'Done: {os.path.basename(out_path)} ({lines} lines, {chars} chars)')
        self._status_label.config(fg=AppStyle.SUCCESS)
        self._run_btn.config(state=tk.NORMAL, text='Convert')
        self._open_btn.config(state=tk.NORMAL)
        self._output_label.config(text=out_path, fg=AppStyle.SUCCESS)
        self._char_count.config(text=f'{chars:,} chars, {lines:,} lines')

        # Show preview (first 3000 chars)
        preview = self._result_text[:3000]
        if len(self._result_text) > 3000:
            preview += '\n\n… (truncated, open file for full content)'
        self._set_preview_text(preview, AppStyle.FG)
        self._running = False

    def _on_error(self, msg):
        self._status.set(f'Error: {msg}')
        self._status_label.config(fg=AppStyle.ERROR)
        self._run_btn.config(state=tk.NORMAL, text='Convert')
        self._running = False

    def _set_preview_text(self, text, color):
        self._preview.config(state=tk.NORMAL)
        self._preview.delete('1.0', tk.END)
        self._preview.insert('1.0', text)
        self._preview.config(state=tk.DISABLED)
        # Ensure the accessible placeholder is set
        self._preview_label = text[:80] if text else 'Preview area'

    def _open_output(self):
        inp = self._input_path.get()
        if not inp:
            return
        out = os.path.splitext(inp)[0] + '.txt'
        if os.path.isfile(out):
            os.startfile(out)


# ═══════════════════════════════════════════════════════════════════════
#  Entry point
# ═══════════════════════════════════════════════════════════════════════

def main():
    app = Tex2TxtGUI()
    app.mainloop()


if __name__ == '__main__':
    main()