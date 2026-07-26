import os
import base64
import io
import re
import fitz
import time
import threading
import json
import queue
import ast
import random
import requests
from collections import deque
from PIL import Image
from epub_converter import EPUBConverter

# 静默 MuPDF 的控制台错误输出
try:
    fitz.TOOLS.mupdf_display_errors(False)
except: pass

# --- 默认配置 ---
DEFAULT_API_KEYS = []
MODEL_NAME = "deepseek-ai/DeepSeek-OCR"
API_URL = "https://api.siliconflow.cn/v1/chat/completions"
PROMPT = "<image>\n<|grounding|>Convert the document to markdown."
DEFAULT_DPI = 144


class OCREngine:
    def __init__(self, api_keys=None, dpi=DEFAULT_DPI, model=MODEL_NAME, api_url=API_URL, workers=None, resume=True):
        self.api_keys = api_keys if api_keys else DEFAULT_API_KEYS
        self.dpi = dpi
        self.model = model
        self.api_url = api_url
        self.workers = workers
        self.resume = resume
        self.stop_requested = False

        # 初始化 EPUB 转换器
        self.epub_converter = EPUBConverter()

        # 任务队列 (Renderer -> Worker)，增加 maxsize 限制内存 (back-pressure)
        max_q = (self.workers if self.workers else 10) * 2
        self.task_queue = queue.Queue(maxsize=max_q)

        # 文件队列 (Scanner -> Renderer)
        self.file_queue = queue.Queue()

    def _log_error(self, out_dir, message):
        try:
            log_path = os.path.join(out_dir, "error.log")
            if not os.path.exists(out_dir): os.makedirs(out_dir, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as f:
                timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"[{timestamp}] {message}\n")
            print(f"[ERROR] {message}")
        except: pass

    def _save_progress(self, out_dir, data):
        try:
            progress_path = os.path.join(out_dir, "progress.json")
            with open(progress_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except: pass

    def _load_progress(self, out_dir):
        progress_path = os.path.join(out_dir, "progress.json")
        if os.path.exists(progress_path):
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except: return {}
        return {}

    @staticmethod
    def pil_to_base64(image):
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    @staticmethod
    def re_match(text):
        pattern = r'(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)'
        matches = re.findall(pattern, text, re.DOTALL)
        return matches

    def _call_openai_compatible_api(self, base64_image, api_key):
        """直接调用 OpenAI 兼容接口（不再使用 litellm）"""
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "model": self.model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{base64_image}"}},
                    {"type": "text", "text": PROMPT}
                ]
            }],
            "temperature": 0,
            "max_tokens": 4096
        }

        response = requests.post(self.api_url, headers=headers, json=payload, timeout=120)
        if response.status_code == 200:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return content
        else:
            err_text = response.text
            status = response.status_code
            raise Exception(f"HTTP {status}: {err_text}")

    def _call_api_with_retry(self, base64_image, api_key):
        """
        调用 API 并实现指数退避重试（最多 10 次）
        退避策略: delay = min(2^attempt, 60) + random_jitter
        """
        max_retries = 10
        last_exception = None

        for attempt in range(max_retries):
            if self.stop_requested:
                raise Exception("Stopped by user")

            try:
                return self._call_openai_compatible_api(base64_image, api_key)
            except requests.exceptions.Timeout as e:
                last_exception = e
                err_str = f"Timeout (attempt {attempt + 1}/{max_retries})"
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                err_str = f"Connection error (attempt {attempt + 1}/{max_retries})"
            except Exception as e:
                err_str = str(e).lower()
                # 不可重试的错误 —— 直接抛出
                if "invalid api key" in err_str or "authentication" in err_str:
                    raise Exception("Fatal: Invalid API Key")
                if "insufficient" in err_str and "balance" in err_str:
                    raise Exception("Fatal: API balance insufficient")
                if "invalid_token" in err_str:
                    raise Exception("Fatal: Invalid API Key (invalid_token)")

                last_exception = e
                err_str = f"API error (attempt {attempt + 1}/{max_retries}): {e}"

            # 最后一次尝试失败，不再等待
            if attempt == max_retries - 1:
                raise Exception(f"Retry queue failed after {max_retries} attempts: {last_exception}")

            # 指数退避: min(2^attempt, 60) + random jitter
            delay = min(2 ** attempt, 60) + random.uniform(0, 1)
            print(f"[Retry] {err_str}, waiting {delay:.1f}s before retry...")
            time.sleep(delay)

        # 不应到达这里
        raise Exception(f"Retry queue failed after {max_retries} attempts: {last_exception}")

    def process_page_once(self, image, page_idx, api_key):
        base64_image = self.pil_to_base64(image)
        if self.stop_requested:
            return {"page_idx": page_idx, "content": "Stopped", "success": False}
        try:
            content = self._call_api_with_retry(base64_image, api_key)
            if content:
                return {"page_idx": page_idx, "content": content, "success": True}
            return {"page_idx": page_idx, "content": "Empty response", "success": False}
        except Exception as e:
            err_str = str(e).lower()
            if "fatal:" in err_str:
                self.stop_requested = True
                return {"page_idx": page_idx, "content": str(e), "success": False}
            return {"page_idx": page_idx, "content": str(e), "success": False}

    def clean_markdown(self, content, page_idx, images_dir, image_pil, matches_ref):
        image_width, image_height = image_pil.size
        img_idx = 0
        for ref in matches_ref:
            if '<|ref|>image<|/ref|>' in ref[0]:
                try:
                    coords = ast.literal_eval(ref[2])
                    for points in coords:
                        x1, y1, x2, y2 = [int(p / 999 * (image_width if i%2==0 else image_height)) for i, p in enumerate(points)]
                        image_pil.crop((x1, y1, x2, y2)).save(os.path.join(images_dir, f"p{page_idx}_{img_idx}.jpg"))
                        content = content.replace(ref[0], f'![](images/p{page_idx}_{img_idx}.jpg)\n')
                        img_idx += 1
                except: pass
        content = re.sub(r'\|?ref\|>.*?\|/ref\|>', '', content)
        content = re.sub(r'\|?det\|>.*?\|/det\|>', '', content)
        content = content.replace(r'\coloneqq', ':=').replace(r'\eqqcolon', '=:').replace(r'\approx', '≈')
        content = content.replace("<<", "").strip()
        return re.sub(r'\n\n+', '\n', content)

    def _finalize_pdf(self, pdf_path, info):
        final_md = ""
        for i in range(info["total"]):
            res = info["data"].get(str(i), info["data"].get(i, {"success": False, "content": "Missing", "page_idx": i}))
            if not res['success']:
                final_md += f"\n\n> [Error on Page {res['page_idx']}: {res['content']}]\n\n"
                continue
            final_md += res['content'] + "\n\n"

        output_file = os.path.join(info["out_dir"], f"{info['name']}.md")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        return output_file

    def process_batch(self, file_paths, output_base, progress_callback=None):
        """
        批量处理文件，自动识别 PDF 和 EPUB 格式

        Args:
            file_paths: 文件路径列表（支持 .pdf 和 .epub）
            output_base: 输出基础目录
            progress_callback: 进度回调函数
        """
        if not file_paths: return
        self.stop_requested = False

        # 按文件类型分类
        pdf_paths = []
        epub_paths = []

        for path in file_paths:
            ext = os.path.splitext(path)[1].lower()
            if ext == '.pdf':
                pdf_paths.append(path)
            elif ext == '.epub':
                epub_paths.append(path)

        total_files = len(pdf_paths) + len(epub_paths)
        completed_files = 0

        # 处理 EPUB 文件（顺序处理，速度快）
        for epub_path in epub_paths:
            if self.stop_requested:
                break

            try:
                name = os.path.splitext(os.path.basename(epub_path))[0].strip()

                def epub_progress(curr, total, msg):
                    file_progress = completed_files + (curr / total if total > 0 else 0)
                    global_progress = int(file_progress / total_files * 100) if total_files > 0 else 0
                    if progress_callback:
                        progress_callback(global_progress, 100, f"[EPUB] {msg}")

                self.epub_converter.convert_epub_to_markdown(epub_path, output_base, epub_progress)
                completed_files += 1

                if progress_callback:
                    progress_callback(int(completed_files / total_files * 100), 100, f"[EPUB] Completed: {name}")

            except Exception as e:
                self._log_error(output_base, f"EPUB conversion failed for {epub_path}: {e}")
                completed_files += 1

        # 处理 PDF 文件（使用现有的并行 OCR pipeline）
        if pdf_paths and not self.stop_requested:
            def pdf_progress(curr, total, msg):
                base_progress = int(completed_files / total_files * 100) if total_files > 0 else 0
                pdf_weight = len(pdf_paths) / total_files if total_files > 0 else 1
                pdf_progress_val = int((curr / total if total > 0 else 0) * pdf_weight * 100)

                if progress_callback:
                    progress_callback(base_progress + pdf_progress_val, 100, f"[PDF] {msg}")

            self._process_pdf_batch(pdf_paths, output_base, pdf_progress)

    def _process_pdf_batch(self, pdf_paths, output_base, progress_callback=None):
        """原有的 PDF 批处理逻辑"""
        if not pdf_paths: return
        self.stop_requested = False

        pdf_results = {p: {"total": 0, "received": 0, "data": {}, "name": os.path.splitext(os.path.basename(p))[0].strip(), "out_dir": ""} for p in pdf_paths}
        total_all_pages = 0
        completed_all_pages = 0
        progress_lock = threading.Lock()

        # 清空之前的残留任务（如果有）
        while not self.task_queue.empty():
            try: self.task_queue.get_nowait()
            except: break

        # 1. Scanner Thread
        def scanner():
            nonlocal total_all_pages
            found_any = False
            for p in pdf_paths:
                if self.stop_requested: break
                try:
                    doc = fitz.open(p)
                    pc = doc.page_count
                    doc.close()
                    with progress_lock:
                        pdf_results[p]["total"] = pc
                        total_all_pages += pc
                    self.file_queue.put(p)
                    found_any = True
                    if progress_callback: progress_callback(completed_all_pages, total_all_pages, f"Indexed: {pdf_results[p]['name']}")
                except Exception as e:
                    self._log_error(output_base, f"Scanner failed to open {p}: {e}")

            # 如果没有找到任何有效的 PDF，也需要放入结束标记
            self.file_queue.put(None)
            if not found_any and progress_callback:
                progress_callback(0, 0, "No valid PDF files found.")

        # 2. Renderer Thread
        def renderer():
            nonlocal completed_all_pages
            while not self.stop_requested:
                pdf_path = self.file_queue.get()
                if pdf_path is None: break

                try:
                    name = pdf_results[pdf_path]["name"]
                    out_dir = os.path.join(output_base, name)
                    pc = pdf_results[pdf_path]["total"]

                    output_file = os.path.join(out_dir, f"{name}.md")
                    if os.path.exists(output_file):
                        with progress_lock:
                            completed_all_pages += pc
                            pdf_results[pdf_path]["received"] = pc
                        if progress_callback:
                            progress_callback(completed_all_pages, total_all_pages, f"Skipped: {name} (Exists)")
                        continue

                    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
                    pdf_results[pdf_path]["out_dir"] = out_dir
                    saved_data = self._load_progress(out_dir) if self.resume else {}

                    doc = fitz.open(pdf_path)
                    zoom, matrix = self.dpi/72.0, fitz.Matrix(self.dpi/72.0, self.dpi/72.0)

                    valid = 0
                    for k, v in saved_data.items():
                        if v.get("success") and "<|ref|>" not in v.get("content", ""):
                            pdf_results[pdf_path]["data"][k] = v
                            valid += 1

                    with progress_lock:
                        completed_all_pages += valid
                        pdf_results[pdf_path]["received"] = valid

                    if valid == pc:
                        self._finalize_pdf(pdf_path, pdf_results[pdf_path])
                        doc.close()
                        continue

                    for i in range(pc):
                        if self.stop_requested: break
                        if str(i) in pdf_results[pdf_path]["data"]: continue

                        try:
                            pix = doc[i].get_pixmap(matrix=matrix, alpha=False)
                            img = Image.open(io.BytesIO(pix.tobytes("png")))
                            self.task_queue.put((pdf_path, i, img, 0), timeout=60)
                        except queue.Full:
                            self._log_error(output_base, f"Task queue full timeout for page {i} of {name}")
                            with progress_lock:
                                pdf_results[pdf_path]["data"][str(i)] = {"page_idx": i, "content": "Queue timeout", "success": False}
                                pdf_results[pdf_path]["received"] += 1
                                completed_all_pages += 1
                        except Exception as e:
                            with progress_lock:
                                pdf_results[pdf_path]["data"][str(i)] = {"page_idx": i, "content": f"Render error: {e}", "success": False}
                                pdf_results[pdf_path]["received"] += 1
                                completed_all_pages += 1
                    doc.close()
                except Exception as e:
                    self._log_error(output_base, f"Renderer overall error: {e}")

            # 等待所有任务处理完
            wait_start = time.time()
            while not self.stop_requested:
                with progress_lock:
                    if total_all_pages > 0 and completed_all_pages >= total_all_pages: break
                    if total_all_pages == 0: break

                    elapsed = time.time() - wait_start
                    if elapsed > 300:
                        self._log_error(output_base, f"Renderer wait timeout: {completed_all_pages}/{total_all_pages} pages completed after {elapsed:.1f}s")
                        break
                time.sleep(0.5)

            for _ in range(max_workers): self.task_queue.put(None)

        # 3. Worker Threads
        max_workers = self.workers if self.workers else min(len(self.api_keys) * 5, 10)
        def worker(idx):
            nonlocal completed_all_pages
            key = self.api_keys[idx % len(self.api_keys)]
            while not self.stop_requested:
                try:
                    task = self.task_queue.get(timeout=10)
                except queue.Empty:
                    with progress_lock:
                        if total_all_pages > 0 and completed_all_pages >= total_all_pages:
                            break
                    continue

                if task is None: break

                p_path, p_idx, p_img, att = task
                res = self.process_page_once(p_img, p_idx, key)

                if res["success"]:
                    images_dir = os.path.join(pdf_results[p_path]["out_dir"], "images")
                    matches_all = self.re_match(res['content'])
                    res['content'] = self.clean_markdown(res['content'], p_idx, images_dir, p_img, matches_all)

                # 如果失败且需要重试（worker 级别兜底重试，仅在 _call_api_with_retry 也失败时触发）
                if not res["success"] and att < 10:
                    delay = min(2 ** att, 60) + random.uniform(0, 1)
                    time.sleep(delay)
                    try:
                        self.task_queue.put((p_path, p_idx, p_img, att + 1), timeout=5)
                        continue
                    except:
                        res["content"] = f"Retry queue failed after {att} attempts: {res['content']}"

                with progress_lock:
                    pdf_results[p_path]["data"][str(p_idx)] = res
                    pdf_results[p_path]["received"] += 1
                    completed_all_pages += 1
                    self._save_progress(pdf_results[p_path]["out_dir"], pdf_results[p_path]["data"])

                    if progress_callback:
                        progress_callback(completed_all_pages, total_all_pages, f"OCR: {pdf_results[p_path]['name']} ({pdf_results[p_path]['received']}/{pdf_results[p_path]['total']})")

                    if pdf_results[p_path]["received"] == pdf_results[p_path]["total"]:
                        self._finalize_pdf(p_path, pdf_results[p_path])

        # Start Pipeline
        threads = [threading.Thread(target=scanner, daemon=True),
                   threading.Thread(target=renderer, daemon=True)]
        for i in range(max_workers):
            threads.append(threading.Thread(target=worker, args=(i,), daemon=True))

        for t in threads: t.start()
        threads[1].join() # 等待 renderer 结束
        for t in threads[2:]: t.join() # 等待 workers 结束

    def process_pdf(self, pdf_path, output_base, progress_callback=None):
        return self.process_batch([pdf_path], output_base, progress_callback)