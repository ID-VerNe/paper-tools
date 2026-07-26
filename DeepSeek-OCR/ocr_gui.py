import os
import sys

# --- Embedded Python Tcl/Tk Fix ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
EMBED_TCL_DIR = os.path.join(BASE_DIR, "python_embed", "Lib", "site-packages", "tcl")
if os.path.exists(EMBED_TCL_DIR):
    os.environ["TCL_LIBRARY"] = os.path.join(EMBED_TCL_DIR, "tcl8.6")
    os.environ["TK_LIBRARY"] = os.path.join(EMBED_TCL_DIR, "tk8.6")
# ----------------------------------

import tkinter as tk
from tkinter import ttk, filedialog, scrolledtext, messagebox
import threading
import json
import os
from ocr_engine import OCREngine

# --- 配置文件路径 ---
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

def load_settings():
    """从 setting.json 加载配置"""
    defaults = {
        "api_url": "https://api.siliconflow.cn/v1/chat/completions",
        "api_key": "",
        "model_name": "deepseek-ai/DeepSeek-OCR",
        "dpi": 144,
        "workers": 10,
        "resume": True
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
        except:
            pass
    return defaults

def save_settings(settings):
    """保存配置到 setting.json"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Failed to save settings: {e}")
        return False


class OCRApp:
    def __init__(self, root):
        self.root = root
        self.root.title("DeepSeek PDF/EPUB to Markdown (GUI)")
        self.root.geometry("750x620")

        self.engine = None
        self.is_running = False

        # 加载已保存的配置
        self.saved_settings = load_settings()

        self.setup_ui()

    def setup_ui(self):
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)

        # ========== API 配置区域 ==========
        api_frame = ttk.LabelFrame(main_frame, text="API Configuration", padding="5")
        api_frame.pack(fill=tk.X, pady=5)

        # API URL
        ttk.Label(api_frame, text="API URL:").grid(row=0, column=0, sticky=tk.W, padx=2)
        self.api_url_var = tk.StringVar(value=self.saved_settings.get("api_url", ""))
        self.api_url_entry = ttk.Entry(api_frame, textvariable=self.api_url_var)
        self.api_url_entry.grid(row=0, column=1, sticky=(tk.W, tk.E), padx=5, pady=2)

        # API Key
        ttk.Label(api_frame, text="API Key:").grid(row=1, column=0, sticky=tk.W, padx=2)
        self.api_key_var = tk.StringVar(value=self.saved_settings.get("api_key", ""))
        self.api_key_entry = ttk.Entry(api_frame, textvariable=self.api_key_var)
        self.api_key_entry.grid(row=1, column=1, sticky=(tk.W, tk.E), padx=5, pady=2)

        # Model Name
        ttk.Label(api_frame, text="Model:").grid(row=2, column=0, sticky=tk.W, padx=2)
        self.model_var = tk.StringVar(value=self.saved_settings.get("model_name", ""))
        self.model_entry = ttk.Entry(api_frame, textvariable=self.model_var)
        self.model_entry.grid(row=2, column=1, sticky=(tk.W, tk.E), padx=5, pady=2)

        api_frame.columnconfigure(1, weight=1)

        # ========== 处理参数区域 ==========
        param_frame = ttk.LabelFrame(main_frame, text="Processing Parameters", padding="5")
        param_frame.pack(fill=tk.X, pady=5)

        ttk.Label(param_frame, text="DPI:").grid(row=0, column=0, sticky=tk.W, padx=2)
        self.dpi_var = tk.StringVar(value=str(self.saved_settings.get("dpi", 144)))
        ttk.Entry(param_frame, textvariable=self.dpi_var, width=8).grid(row=0, column=1, padx=5, sticky=tk.W)

        ttk.Label(param_frame, text="Threads:").grid(row=0, column=2, sticky=tk.W, padx=10)
        self.workers_var = tk.StringVar(value=str(self.saved_settings.get("workers", 10)))
        ttk.Entry(param_frame, textvariable=self.workers_var, width=8).grid(row=0, column=3, padx=5, sticky=tk.W)

        self.resume_var = tk.BooleanVar(value=self.saved_settings.get("resume", True))
        ttk.Checkbutton(param_frame, text="Resume (断点续传)", variable=self.resume_var).grid(row=0, column=4, sticky=tk.W, padx=15)

        # Source Selection
        file_frame = ttk.LabelFrame(main_frame, text="Source", padding="5")
        file_frame.pack(fill=tk.X, pady=5)

        self.path_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.path_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(file_frame, text="File", command=self.browse_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(file_frame, text="Folder", command=self.browse_dir).pack(side=tk.LEFT, padx=2)

        # Output Section
        out_frame = ttk.LabelFrame(main_frame, text="Output Directory", padding="5")
        out_frame.pack(fill=tk.X, pady=5)
        self.out_var = tk.StringVar(value="")
        ttk.Entry(out_frame, textvariable=self.out_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)
        ttk.Button(out_frame, text="Change", command=self.browse_out).pack(side=tk.LEFT, padx=2)

        # Progress Section
        log_frame = ttk.LabelFrame(main_frame, text="Progress & Logs", padding="5")
        log_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        self.progress = ttk.Progressbar(log_frame, orient=tk.HORIZONTAL, mode='determinate')
        self.progress.pack(fill=tk.X, pady=5)

        self.log_area = scrolledtext.ScrolledText(log_frame, height=12, state='disabled', font=("Consolas", 9))
        self.log_area.pack(fill=tk.BOTH, expand=True)

        # Controls
        btn_frame = ttk.Frame(main_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        self.start_btn = ttk.Button(btn_frame, text="Start Processing", command=self.start_task)
        self.start_btn.pack(side=tk.RIGHT, padx=5)

        self.stop_btn = ttk.Button(btn_frame, text="Stop", command=self.stop_task, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.RIGHT)

    def log(self, message):
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, message + "\n")
        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def browse_file(self):
        path = filedialog.askopenfilename(filetypes=[("Supported files", "*.pdf;*.epub"), ("PDF files", "*.pdf"), ("EPUB files", "*.epub"), ("All files", "*.*")])
        if path:
            self.path_var.set(path)
            self.out_var.set(os.path.dirname(path))

    def browse_dir(self):
        path = filedialog.askdirectory()
        if path:
            self.path_var.set(path)
            self.out_var.set(path)

    def browse_out(self):
        path = filedialog.askdirectory()
        if path: self.out_var.set(path)

    def update_progress(self, current, total, message):
        self.root.after(0, lambda: self._update_progress_ui(current, total, message))

    def _update_progress_ui(self, current, total, message):
        self.progress['maximum'] = total
        self.progress['value'] = current
        self.log(message)

    def start_task(self):
        # --- 先保存当前配置到 setting.json ---
        settings = {
            "api_url": self.api_url_var.get().strip(),
            "api_key": self.api_key_var.get().strip(),
            "model_name": self.model_var.get().strip(),
            "dpi": int(self.dpi_var.get()) if self.dpi_var.get().strip().isdigit() else 144,
            "workers": int(self.workers_var.get()) if self.workers_var.get().strip().isdigit() else 10,
            "resume": self.resume_var.get(),
        }
        save_settings(settings)
        self.log("Settings saved to setting.json")

        # --- 校验输入 ---
        input_path = self.path_var.get()
        if not input_path:
            messagebox.showerror("Error", "Please select an input path!")
            return

        api_url = settings["api_url"]
        api_key = settings["api_key"]
        model = settings["model_name"]
        dpi = settings["dpi"]
        workers = settings["workers"]
        resume = settings["resume"]

        if not api_url:
            messagebox.showerror("Error", "Please enter API URL!")
            return
        if not api_key:
            messagebox.showerror("Error", "Please enter API Key!")
            return
        if not model:
            messagebox.showerror("Error", "Please enter Model Name!")
            return

        output_dir = self.out_var.get()
        if not output_dir:
            if os.path.isfile(input_path):
                output_dir = os.path.dirname(input_path)
            else:
                output_dir = input_path

        resume_label = "ON" if resume else "OFF"
        self.log(f"Resume mode: {resume_label}")
        self.engine = OCREngine(api_keys=[api_key], dpi=dpi, model=model, api_url=api_url, workers=workers, resume=resume)
        self.is_running = True
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)

        threading.Thread(target=self.run_engine, args=(input_path, output_dir), daemon=True).start()

    def stop_task(self):
        if self.engine:
            self.engine.stop_requested = True
            self.log("Stopping... (waiting for current pages to finish)")
            self.stop_btn.config(state=tk.DISABLED)

    def run_engine(self, input_path, output_dir):
        try:
            file_paths = []
            if os.path.isfile(input_path):
                file_paths = [input_path]
            else:
                files = [f for f in os.listdir(input_path) if f.lower().endswith(('.pdf', '.epub'))]
                if not files:
                    self.log("No PDF or EPUB files found in directory.")
                    return
                file_paths = [os.path.join(input_path, f) for f in files]

            self.log(f"Starting batch process for {len(file_paths)} file(s)...")
            self.engine.process_batch(file_paths, output_dir, self.update_progress)

            if not self.engine.stop_requested:
                self.log("--- All Tasks Completed ---")
            else:
                self.log("--- Process Stopped by User ---")
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
        finally:
            self.is_running = False
            self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))
            self.root.after(0, lambda: self.stop_btn.config(state=tk.DISABLED))


def main():
    root = tk.Tk()
    try:
        root.iconbitmap(default=None)
    except:
        pass
    app = OCRApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()