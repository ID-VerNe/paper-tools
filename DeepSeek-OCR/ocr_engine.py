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
# 单次请求的读超时（秒），连接超时固定 10s
DEFAULT_REQUEST_TIMEOUT = 180
# 单页最多尝试次数（请求级 + worker 级共用同一预算）
DEFAULT_MAX_RETRIES = 6
# 单页所有尝试的总时长上限（秒），超过即放弃该页
DEFAULT_PAGE_DEADLINE = 900
# progress.json 同一本书的最小落盘间隔（秒）
DEFAULT_SAVE_INTERVAL = 3.0
# worker 级兜底重试次数（在请求级预算之外，整页最多再排一轮）
WORKER_RETRY_LIMIT = 1
# md 里失败页占位标记
ERROR_PLACEHOLDER = "[Error on Page"


class OCREngine:
    def __init__(self, api_keys=None, dpi=DEFAULT_DPI, model=MODEL_NAME, api_url=API_URL,
                 workers=None, resume=True, request_timeout=DEFAULT_REQUEST_TIMEOUT,
                 max_retries=DEFAULT_MAX_RETRIES, page_deadline=DEFAULT_PAGE_DEADLINE,
                 save_interval=DEFAULT_SAVE_INTERVAL):
        self.api_keys = api_keys if api_keys else DEFAULT_API_KEYS
        if not self.api_keys:
            raise ValueError("OCREngine 需要至少一个 API key（api_keys 为空）")
        self.dpi = dpi
        self.model = model
        self.api_url = api_url
        self.workers = workers
        self.resume = resume
        self.request_timeout = request_timeout
        self.max_retries = max(1, int(max_retries))
        self.page_deadline = page_deadline
        self.save_interval = save_interval
        self.stop_requested = False

        # 进度落盘节流 & 串行化
        self._save_lock = threading.Lock()
        self._last_save = {}

        # 收尾有界：worker 存活计数 / 流水线异常中止标记
        self._workers_alive = 0
        self._workers_lock = threading.Lock()
        self._pipeline_aborted = False

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

    def _save_progress(self, out_dir, data, force=False):
        """原子落盘 progress.json（tmp + os.replace），按 save_interval 节流。

        失败不再静默：写 error.log，避免「进度悄悄没写进去 → 下次整本重跑」。
        """
        if not out_dir:
            return False
        progress_path = os.path.join(out_dir, "progress.json")
        tmp_path = progress_path + ".tmp"
        with self._save_lock:
            now = time.time()
            last = self._last_save.get(out_dir, 0)
            if not force and self.save_interval > 0 and (now - last) < self.save_interval:
                return False
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp_path, progress_path)
                self._last_save[out_dir] = now
                return True
            except Exception as e:
                try:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)
                except Exception:
                    pass
                self._log_error(out_dir, f"Progress save failed for {progress_path}: {e}")
                return False

    def _load_progress(self, out_dir):
        progress_path = os.path.join(out_dir, "progress.json")
        if os.path.exists(progress_path):
            try:
                with open(progress_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not isinstance(data, dict):
                    raise ValueError(f"unexpected progress type: {type(data).__name__}")
                return data
            except Exception as e:
                self._log_error(out_dir, f"Progress file unreadable ({progress_path}): {e}")
                return {}
        return {}

    @staticmethod
    def _progress_state(progress_data, total):
        """complete / incomplete / missing —— 判断 progress.json 是否覆盖全部页且全部成功。"""
        if not progress_data:
            return "missing"
        for i in range(total):
            v = progress_data.get(str(i))
            if not isinstance(v, dict) or not v.get("success"):
                return "incomplete"
        return "complete"

    @staticmethod
    def _md_has_errors(md_path):
        try:
            with open(md_path, "r", encoding="utf-8") as f:
                for line in f:
                    if ERROR_PLACEHOLDER in line:
                        return True
        except Exception:
            return True
        return False

    def _archive_stale(self, md_path, out_dir, reason):
        """把不可信的旧 md 改名 .stale：既不与新产物并存，也留一份现场。"""
        stale_path = md_path + ".stale"
        try:
            os.replace(md_path, stale_path)
            self._log_error(out_dir, f"Stale markdown archived -> {os.path.basename(stale_path)} ({reason})")
        except Exception as e:
            self._log_error(out_dir, f"Failed to archive stale markdown {md_path}: {e}")

    def _book_state(self, out_dir, name, total):
        """done / resume / fresh。

        只有「md 存在 + 进度覆盖全部页且全成功 + md 无失败标记」才算 done。
        残缺的 md（有失败页/缺 progress）一律归档成 .stale 后重做，
        否则旧 md 会和新产物并存，下次又被当成「已完成」。
        进度缺失但 md 干净时按 done 处理（兼容本次改动之前产出的语料）。
        """
        md_path = os.path.join(out_dir, f"{name}.md")
        if not os.path.exists(md_path):
            progress = self._load_progress(out_dir) if self.resume else {}
            return "resume" if progress else "fresh"

        progress = self._load_progress(out_dir) if self.resume else {}
        state = self._progress_state(progress, total)
        if state == "complete" and not self._md_has_errors(md_path):
            return "done"
        if state == "missing":
            # 旧语料：没有 progress.json，md 也没失败标记 -> 认为已完成
            if not self._md_has_errors(md_path):
                return "done"
            self._archive_stale(md_path, out_dir, "legacy markdown has failure marks but no progress.json")
            return "resume"

        # 进度不完整（有失败页/缺页）：md 是“残缺成品”，归档后重做
        self._archive_stale(md_path, out_dir, "progress incomplete, will re-run missing pages")
        return "resume"

    def _sleep_interruptible(self, seconds):
        """可被 stop_requested 打断的 sleep；返回 False 表示已被要求停止。"""
        deadline = time.time() + seconds
        while True:
            if self.stop_requested:
                return False
            remaining = deadline - time.time()
            if remaining <= 0:
                return True
            time.sleep(min(0.2, remaining))

    @staticmethod
    def pil_to_base64(image):
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        return base64.b64encode(buffered.getvalue()).decode('utf-8')

    @staticmethod
    def is_blank_page(image, ink_ratio_threshold=0.002, dark_level=235):
        """近空白页判定：暗于 dark_level 的像素占比 < 阈值即认为没有可识别内容。

        只用于「模型返回空」时兜底：空白页送 OCR 必然返回空，若一律算失败，
        整本会永远卡在 .md.partial 反复重试（实测某书 517 页里 2 页空白）。

        dark_level 取 235 而不是更暗的 200：浅灰字（灰度 205~230）也要算内容，
        否则「字很淡但确实有字」的页会被误判成空白、悄悄丢掉（实测灰度 212 的文字
        在 200 阈值下墨迹为 0%，在 235 阈值下 0.59%）。阈值 0.2% 仍有足够余量：
        实测正文页 3.4%~8.0%，纯白页 0.006%~0.014%。
        """
        try:
            g = image.convert("L")
            hist = g.histogram()
            total = float(g.width * g.height) or 1.0
            return sum(hist[:dark_level]) / total < ink_ratio_threshold
        except Exception:
            return False

    @staticmethod
    def re_match(text):
        pattern = r'(<\|ref\|>(.*?)<\|/ref\|><\|det\|>(.*?)<\|/det\|>)'
        matches = re.findall(pattern, text, re.DOTALL)
        return matches

    def _post_with_stop(self, headers, payload, timeout):
        """在守护线程里发请求，主线程 0.2s 轮询 stop_requested。

        requests 的阻塞读没法从外部打断：直接 post 的话，点 Stop 要等到读超时
        （默认 180s）才返回，看起来就是「点了没反应」。这里把请求放到守护线程，
        主线程只负责轮询；一旦 Stop 就立刻抛错返回，残留 socket 交给守护线程自然超时。
        """
        box = {}

        def _run():
            try:
                box["resp"] = requests.post(self.api_url, headers=headers, json=payload, timeout=timeout)
            except BaseException as e:  # 交给主线程按原类型重新抛出
                box["err"] = e

        t = threading.Thread(target=_run, daemon=True, name="ocr-http")
        t.start()
        while t.is_alive():
            t.join(timeout=0.2)
            if self.stop_requested:
                raise Exception("Stopped by user")
        if "err" in box:
            raise box["err"]
        return box["resp"]

    def _call_openai_compatible_api(self, base64_image, api_key, read_timeout=None):
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

        timeout = (10, read_timeout if read_timeout else self.request_timeout)
        response = self._post_with_stop(headers, payload, timeout)
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
        调用 API 并实现指数退避重试。

        - 最多 self.max_retries 次尝试
        - 单页累计耗时不超过 self.page_deadline，最后一次尝试的读超时会按剩余预算收敛
        - 退避 sleep 可被 stop_requested 立即打断（不再傻等 60s×N）
        """
        deadline = time.time() + self.page_deadline
        last_exception = None
        attempt = 0

        while attempt < self.max_retries:
            if self.stop_requested:
                raise Exception("Stopped by user")

            remaining = deadline - time.time()
            if remaining <= 1:
                break

            attempt += 1
            try:
                return self._call_openai_compatible_api(
                    base64_image, api_key,
                    read_timeout=min(self.request_timeout, max(5, remaining)),
                )
            except requests.exceptions.Timeout as e:
                last_exception = e
                err_str = f"Timeout (attempt {attempt}/{self.max_retries})"
            except requests.exceptions.ConnectionError as e:
                last_exception = e
                err_str = f"Connection error (attempt {attempt}/{self.max_retries})"
            except Exception as e:
                if self.stop_requested:
                    raise Exception("Stopped by user")
                err_str_l = str(e).lower()
                # 不可重试的错误 —— 直接抛出
                if "invalid api key" in err_str_l or "authentication" in err_str_l:
                    raise Exception("Fatal: Invalid API Key")
                if "insufficient" in err_str_l and "balance" in err_str_l:
                    raise Exception("Fatal: API balance insufficient")
                if "invalid_token" in err_str_l:
                    raise Exception("Fatal: Invalid API Key (invalid_token)")

                last_exception = e
                err_str = f"API error (attempt {attempt}/{self.max_retries}): {e}"

            if attempt >= self.max_retries:
                break

            # 指数退避: min(2^(n-1), 60) + random jitter，但不越过整页预算
            delay = min(2 ** (attempt - 1), 60) + random.uniform(0, 1)
            if time.time() + delay >= deadline:
                print(f"[Retry] {err_str}, page deadline reached, giving up.")
                break

            print(f"[Retry] {err_str}, waiting {delay:.1f}s before retry...")
            if not self._sleep_interruptible(delay):
                raise Exception("Stopped by user")

        raise Exception(
            f"Retry budget exhausted after {attempt}/{self.max_retries} attempts "
            f"(deadline={self.page_deadline}s): {last_exception}"
        )

    def process_page_once(self, image, page_idx, api_key):
        if self.stop_requested:
            return {"page_idx": page_idx, "content": "Stopped", "success": False}
        base64_image = self.pil_to_base64(image)
        try:
            content = self._call_api_with_retry(base64_image, api_key)
            if content:
                return {"page_idx": page_idx, "content": content, "success": True}
            if self.is_blank_page(image):
                # 空白页：模型返空是正确输出，判成功让整本能收尾；但 md 里留可见标记，
                # 万一其实是「模型漏字」也能一眼看出来（并可用 blank_rescued 检索）。
                return {"page_idx": page_idx,
                        "content": f"> [Blank page {page_idx}: model returned empty]",
                        "success": True, "blank_rescued": True}
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
        """把所有页拼成 md。

        只有全部页 success 才产出正式 <name>.md；否则产出 <name>.md.partial，
        下次运行会自动补跑缺页（不会被当成“已完成”跳过）。
        返回 (path, ok)。
        """
        name = info["name"]
        out_dir = info["out_dir"]
        total = info["total"]
        if not out_dir:
            raise RuntimeError(f"finalize without out_dir: {pdf_path}")

        final_md = ""
        failed_pages = []
        for i in range(total):
            res = info["data"].get(str(i)) or info["data"].get(i) \
                or {"success": False, "content": "Missing", "page_idx": i}
            if not res.get("success"):
                failed_pages.append(res.get("page_idx", i))
                final_md += f"\n\n> [Error on Page {res.get('page_idx', i)}: {res.get('content')}]\n\n"
                continue
            final_md += res["content"] + "\n\n"

        # 先强制落盘进度，保证 md 与 progress.json 一致
        self._save_progress(out_dir, info["data"], force=True)

        if failed_pages:
            output_file = os.path.join(out_dir, f"{name}.md.partial")
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(final_md)
            self._log_error(
                out_dir,
                f"Finalize partial for {name}: {len(failed_pages)}/{total} pages failed "
                f"(first: {failed_pages[:5]}); partial md kept, missing pages will retry next run",
            )
            return output_file, False

        output_file = os.path.join(out_dir, f"{name}.md")
        tmp_file = output_file + ".tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(final_md)
        os.replace(tmp_file, output_file)

        for extra in (output_file + ".partial", output_file + ".stale"):
            try:
                if os.path.exists(extra):
                    os.remove(extra)
            except Exception:
                pass
        return output_file, True

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
        max_workers = self.workers if self.workers else min(len(self.api_keys) * 5, 10)
        # 「长时间没有任何页完成」才算异常；上万页的批次本身就要跑几小时
        stall_limit = max(300, self.page_deadline + 60)

        def renderer():
            nonlocal completed_all_pages
            while not self.stop_requested and not self._pipeline_aborted:
                pdf_path = self.file_queue.get()
                if pdf_path is None: break

                try:
                    name = pdf_results[pdf_path]["name"]
                    out_dir = os.path.join(output_base, name)
                    pc = pdf_results[pdf_path]["total"]

                    # 完成判定：md + progress.json + md 内容三者一致才 skip
                    state = self._book_state(out_dir, name, pc)
                    if state == "done":
                        with progress_lock:
                            completed_all_pages += pc
                            pdf_results[pdf_path]["received"] = pc
                        if progress_callback:
                            progress_callback(completed_all_pages, total_all_pages, f"Skipped: {name} (Complete)")
                        continue

                    os.makedirs(os.path.join(out_dir, "images"), exist_ok=True)
                    pdf_results[pdf_path]["out_dir"] = out_dir
                    saved_data = self._load_progress(out_dir) if self.resume else {}

                    doc = fitz.open(pdf_path)
                    matrix = fitz.Matrix(self.dpi/72.0, self.dpi/72.0)

                    valid = 0
                    for k, v in saved_data.items():
                        if isinstance(v, dict) and v.get("success") and "<|ref|>" not in v.get("content", ""):
                            pdf_results[pdf_path]["data"][k] = v
                            valid += 1

                    with progress_lock:
                        completed_all_pages += valid
                        pdf_results[pdf_path]["received"] = valid

                    if valid == pc:
                        try:
                            self._finalize_pdf(pdf_path, pdf_results[pdf_path])
                        except Exception as e:
                            self._log_error(output_base, f"Finalize failed for {name}: {e}")
                        doc.close()
                        continue

                    for i in range(pc):
                        if self.stop_requested or self._pipeline_aborted: break
                        if str(i) in pdf_results[pdf_path]["data"]: continue

                        if self._workers_alive <= 0:
                            self._abort_pipeline(
                                output_base,
                                f"No worker thread alive while page {i} of '{name}' is still pending "
                                f"(completed {completed_all_pages}/{total_all_pages}); abort to avoid 60s/page stall",
                            )
                            break

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

            # 等待所有任务处理完：以“无进展”为界，而不是固定总时长
            last_completed, last_change = -1, time.time()
            while not self.stop_requested:
                with progress_lock:
                    cur_completed = completed_all_pages
                    done_all = total_all_pages == 0 or completed_all_pages >= total_all_pages

                if done_all: break

                if self._workers_alive <= 0:
                    self._abort_pipeline(
                        output_base,
                        f"All workers exited while pages remain ({completed_all_pages}/{total_all_pages})",
                    )
                    break

                if cur_completed != last_completed:
                    last_completed, last_change = cur_completed, time.time()
                elif time.time() - last_change > stall_limit:
                    self._log_error(
                        output_base,
                        f"No page progress for {stall_limit}s "
                        f"({completed_all_pages}/{total_all_pages} pages completed); aborting wait",
                    )
                    self.stop_requested = True
                    break
                time.sleep(0.5)

            # 收尾哨兵：不阻塞（队列满就停，避免 renderer 永久卡在 put）
            for _ in range(max_workers):
                try:
                    self.task_queue.put(None, timeout=1)
                except queue.Full:
                    self._log_error(output_base, "Task queue full while sending shutdown sentinels")
                    break

        # 3. Worker Threads
        with self._workers_lock:
            self._workers_alive = max_workers
        self._pipeline_aborted = False

        def worker(idx):
            nonlocal completed_all_pages
            key = self.api_keys[idx % len(self.api_keys)]
            try:
                while not self.stop_requested:
                    try:
                        task = self.task_queue.get(timeout=5)
                    except queue.Empty:
                        with progress_lock:
                            done = total_all_pages > 0 and completed_all_pages >= total_all_pages
                        if done:
                            break
                        continue

                    if task is None: break

                    p_path, p_idx, p_img, att = task
                    res = self.process_page_once(p_img, p_idx, key)

                    if res["success"]:
                        try:
                            images_dir = os.path.join(pdf_results[p_path]["out_dir"], "images")
                            matches_all = self.re_match(res['content'])
                            res['content'] = self.clean_markdown(res['content'], p_idx, images_dir, p_img, matches_all)
                        except Exception as e:
                            self._log_error(pdf_results[p_path]["out_dir"],
                                            f"Post-process failed for page {p_idx} of {pdf_results[p_path]['name']}: {e}")

                    # worker 级兜底重试：只再排一轮，退避可被 Stop 打断
                    if not res["success"] and att < WORKER_RETRY_LIMIT and not self.stop_requested:
                        delay = min(2 ** att, 60) + random.uniform(0, 1)
                        if self._sleep_interruptible(delay):
                            try:
                                self.task_queue.put((p_path, p_idx, p_img, att + 1), timeout=5)
                                continue
                            except queue.Full:
                                res["content"] = f"Retry queue full after {att + 1} attempts: {res['content']}"

                    with progress_lock:
                        pdf_results[p_path]["data"][str(p_idx)] = res
                        pdf_results[p_path]["received"] += 1
                        completed_all_pages += 1
                        self._save_progress(pdf_results[p_path]["out_dir"], pdf_results[p_path]["data"])

                        if progress_callback:
                            progress_callback(completed_all_pages, total_all_pages,
                                              f"OCR: {pdf_results[p_path]['name']} ({pdf_results[p_path]['received']}/{pdf_results[p_path]['total']})")

                        need_finalize = pdf_results[p_path]["received"] == pdf_results[p_path]["total"]

                    if need_finalize:
                        try:
                            self._finalize_pdf(p_path, pdf_results[p_path])
                        except Exception as e:
                            self._log_error(pdf_results[p_path]["out_dir"],
                                            f"Finalize failed for {pdf_results[p_path]['name']}: {e}")
            finally:
                with self._workers_lock:
                    self._workers_alive -= 1

        # Start Pipeline
        threads = [threading.Thread(target=scanner, daemon=True, name="ocr-scanner"),
                   threading.Thread(target=renderer, daemon=True, name="ocr-renderer")]
        for i in range(max_workers):
            threads.append(threading.Thread(target=worker, args=(i,), daemon=True, name=f"ocr-worker-{i}"))

        for t in threads: t.start()

        # 收尾也以「长时间无进展」为界：
        # 上万页的批次本就要跑几小时，固定 join 超时会提前“宣布完成”并留下后台线程；
        # 真正的假死（请求无超时、线程全卡住）则会被 stall_limit 戳破后主动收工。
        last_completed, last_change = -1, time.time()
        while any(t.is_alive() for t in threads[1:]):
            with progress_lock:
                cur_completed = completed_all_pages
            if cur_completed != last_completed:
                last_completed, last_change = cur_completed, time.time()
            elif time.time() - last_change > stall_limit:
                alive = [t.name for t in threads[1:] if t.is_alive()]
                self._log_error(
                    output_base,
                    f"No page progress for {stall_limit}s ({cur_completed}/{total_all_pages}); "
                    f"giving up on {len(alive)} thread(s): {alive[:6]}",
                )
                self.stop_requested = True
                break
            time.sleep(1)

    def _abort_pipeline(self, out_dir, message):
        if not self._pipeline_aborted:
            self._pipeline_aborted = True
            self._log_error(out_dir, f"Pipeline aborted: {message}")

    def process_pdf(self, pdf_path, output_base, progress_callback=None):
        return self.process_batch([pdf_path], output_base, progress_callback)