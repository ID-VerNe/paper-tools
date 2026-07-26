import sys
import argparse
import os
import json
from ocr_engine import OCREngine, DEFAULT_API_KEYS, MODEL_NAME, API_URL
from tqdm import tqdm

# 配置文件路径
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

def load_cli_settings():
    """从 setting.json 加载配置"""
    defaults = {
        "api_url": API_URL,
        "api_key": ",".join(DEFAULT_API_KEYS),
        "model_name": MODEL_NAME,
        "resume": True,
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
        except:
            pass
    return defaults

class Colors:
    RED = '\033[31m'
    GREEN = '\033[32m'
    YELLOW = '\033[33m'
    BLUE = '\033[34m'
    RESET = '\033[0m'

def run_cli(args):
    # 从 setting.json 加载配置，再用命令行参数覆盖
    cli_settings = load_cli_settings()

    api_url = args.api_url if args.api_url else cli_settings.get("api_url", API_URL)
    model = args.model if args.model else cli_settings.get("model_name", MODEL_NAME)
    # api_key 优先级: --api-key > --keys > settings.json
    if args.api_key:
        api_keys = [args.api_key]
    elif args.keys:
        api_keys = args.keys
    else:
        api_keys = [cli_settings.get("api_key", DEFAULT_API_KEYS[0])]

    # resume 优先级: 命令行 --resume/--no-resume > settings.json
    if args.resume is not None:
        resume = args.resume
    else:
        resume = cli_settings.get("resume", True)

    engine = OCREngine(api_keys=api_keys, dpi=args.dpi, model=model, api_url=api_url, workers=args.workers, resume=resume)

    resume_label = "ON" if resume else "OFF"
    print(f"{Colors.BLUE}API URL: {api_url}{Colors.RESET}")
    print(f"{Colors.BLUE}Model: {model}{Colors.RESET}")
    print(f"{Colors.BLUE}Resume: {resume_label}{Colors.RESET}")
    print(f"{Colors.BLUE}Workers: {args.workers}{Colors.RESET}")
    
    input_path = os.path.abspath(args.input)
    
    # Determine default output directory
    output_dir = args.output
    if output_dir is None:
        if os.path.isfile(input_path):
            output_dir = os.path.dirname(input_path)
        else:
            output_dir = input_path
    
    def process_batch_with_tqdm(file_paths):
        pbar = [None]
        def callback(curr, total, msg):
            if pbar[0] is None:
                pbar[0] = tqdm(total=total, desc="Batch Progress", unit="%")
            pbar[0].total = total
            pbar[0].n = curr
            pbar[0].set_postfix_str(msg)
            pbar[0].refresh()
            if curr >= total:
                pbar[0].close()

        print(f"{Colors.BLUE}Processing batch of {len(file_paths)} files...{Colors.RESET}")
        engine.process_batch(file_paths, output_dir, callback)
        print(f"{Colors.GREEN}All done.{Colors.RESET}")

    if os.path.isfile(input_path):
        process_batch_with_tqdm([input_path])
    elif os.path.isdir(input_path):
        # 支持 PDF 和 EPUB 文件
        files = [os.path.join(input_path, f) for f in os.listdir(input_path)
                 if f.lower().endswith(('.pdf', '.epub'))]
        if files:
            process_batch_with_tqdm(files)
        else:
            print(f"{Colors.YELLOW}No PDF or EPUB files found in {input_path}{Colors.RESET}")
    else:
        print(f"{Colors.RED}Invalid input: {input_path}{Colors.RESET}")

def main():
    # If no arguments provided, try to launch GUI
    if len(sys.argv) == 1:
        try:
            import ocr_gui
            print("Launching GUI...")
            ocr_gui.main()
        except ImportError as e:
            print(f"Failed to load GUI: {e}")
            print("Usage: python ocr_cli.py <input_path> [--output <output_dir>] [--keys <key1> <key2> ...]")
    else:
        parser = argparse.ArgumentParser(description="DeepSeek-OCR PDF/EPUB to Markdown CLI")
        parser.add_argument("input", help="Path to PDF/EPUB file or directory")
        parser.add_argument("--output", default=None, help="Base output directory (default: same as input)")
        parser.add_argument("--keys", nargs="+", help="Optional: List of API keys (deprecated, use --api-key)")
        parser.add_argument("--api-key", default=None, help="API key (default: from settings.json)")
        parser.add_argument("--api-url", default=None, help="API URL (default: from settings.json)")
        parser.add_argument("--model", default=None, help="Model name (default: from settings.json)")
        parser.add_argument("--dpi", type=int, default=144, help="DPI for PDF rendering (default: 144)")
        parser.add_argument("--workers", type=int, default=None, help="Number of parallel workers (default: 50 per key)")
        parser.add_argument("--resume", action="store_true", default=None, dest="resume", help="Enable resume (断点续传)")
        parser.add_argument("--no-resume", action="store_false", default=None, dest="resume", help="Disable resume (从头开始)")
        args = parser.parse_args()
        run_cli(args)

if __name__ == "__main__":
    main()
