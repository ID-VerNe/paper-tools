"""断点续传 / 收尾有界性回归测试。

不依赖外网、真实 API key：用本地 mock HTTP server 模拟 DeepSeek-OCR 接口。
运行：
    python -m unittest discover -s DeepSeek-OCR/tests -v
"""

import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import fitz

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ocr_engine import OCREngine  # noqa: E402


class _MockHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.server.calls += 1
        if getattr(self.server, "delay", 0):
            time.sleep(self.server.delay)
        if self.server.fail:
            body = b'{"error":"mock upstream failure"}'
            self.send_response(500)
        elif self.server.empty:
            body = json.dumps({"choices": [{"message": {"content": ""}}]}).encode()
            self.send_response(200)
        else:
            page = self.server.calls
            body = json.dumps({"choices": [{"message": {"content": f"# page {page}"}}]}).encode()
            self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def make_pdf(path, pages, blank=()):
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        if i not in blank:
            # 写成一段多行正文，墨迹比例接近真实书页（单行小字太稀疏，会被判成空白页）
            page.insert_text((72, 90), "\n".join(f"line {j} of page {i}, some text here" for j in range(8)))
    doc.save(path)
    doc.close()


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ocr-resume-")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
        self.server.calls = 0
        self.server.fail = False
        self.server.empty = False
        self.server.delay = 0
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/v1/chat/completions"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers -------------------------------------------------------
    def make_book(self, name, pages):
        pdf = os.path.join(self.tmp, f"{name}.pdf")
        make_pdf(pdf, pages)
        return pdf

    def out_dir(self, name):
        return os.path.join(self.tmp, name)

    def write_progress(self, name, mapping):
        d = self.out_dir(name)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "progress.json"), "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)

    def good_pages(self, indices, content="# cached page"):
        return {str(i): {"page_idx": i, "content": content, "success": True} for i in indices}

    def run_engine(self, pdf, **kwargs):
        params = dict(api_keys=["k"], dpi=72, api_url=self.url, workers=4, resume=True,
                      request_timeout=5, max_retries=1, page_deadline=10, save_interval=0)
        params.update(kwargs)
        engine = OCREngine(**params)
        engine.process_batch([pdf], self.tmp, None)
        return engine

    def read(self, path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # ---- tests ---------------------------------------------------------
    def test_resume_skips_completed_pages(self):
        """30% 进度 -> 只补缺的页，最终产出完整 md。"""
        pdf = self.make_book("partial", 20)
        self.write_progress("partial", self.good_pages(range(5)))

        self.run_engine(pdf)

        self.assertEqual(self.server.calls, 15)
        md = os.path.join(self.out_dir("partial"), "partial.md")
        self.assertTrue(os.path.exists(md))
        self.assertFalse(os.path.exists(md + ".partial"))
        self.assertEqual(self.read(md).count("#"), 20)

        with open(os.path.join(self.out_dir("partial"), "progress.json"), encoding="utf-8") as f:
            progress = json.load(f)
        self.assertEqual(len(progress), 20)
        self.assertTrue(all(v["success"] for v in progress.values()))

    def test_full_progress_without_md_finalizes(self):
        """进度齐全但缺 md -> 不请求 API，直接补齐 md。"""
        pdf = self.make_book("full", 5)
        self.write_progress("full", self.good_pages(range(5), content="# cached"))

        self.run_engine(pdf)

        self.assertEqual(self.server.calls, 0)
        md = os.path.join(self.out_dir("full"), "full.md")
        self.assertTrue(os.path.exists(md))
        self.assertEqual(self.read(md).count("# cached"), 5)

    def test_failed_pages_make_md_stale(self):
        """进度里有失败页 + 已存在残 md -> 归档 .stale 并只重跑失败页。"""
        pdf = self.make_book("stale", 5)
        progress = self.good_pages(range(5))
        progress["2"] = {"page_idx": 2, "content": "HTTP 502", "success": False}
        self.write_progress("stale", progress)

        out = self.out_dir("stale")
        md = os.path.join(out, "stale.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("# a\n\n# b\n\n> [Error on Page 2: HTTP 502]\n\n# d\n\n# e\n")

        self.run_engine(pdf)

        self.assertEqual(self.server.calls, 1)
        self.assertTrue(os.path.exists(md))
        self.assertNotIn("[Error on Page", self.read(md))
        self.assertEqual(self.read(md).count("#"), 5)
        self.assertFalse(os.path.exists(md + ".stale"))

    def test_failure_writes_partial_not_md(self):
        """接口持续失败 -> 不产出正式 md，只产出 .md.partial，进度记录失败。"""
        pdf = self.make_book("broken", 3)
        self.server.fail = True

        self.run_engine(pdf)

        out = self.out_dir("broken")
        self.assertFalse(os.path.exists(os.path.join(out, "broken.md")))
        self.assertTrue(os.path.exists(os.path.join(out, "broken.md.partial")))
        with open(os.path.join(out, "progress.json"), encoding="utf-8") as f:
            progress = json.load(f)
        self.assertEqual(len(progress), 3)
        self.assertEqual(sum(1 for v in progress.values() if v["success"]), 0)

        # 第二次仍然会重试（不会被 .md.partial 当完成）
        # 每页 2 次尝试 = 请求级 1 次 + worker 兜底 1 次，3 页 / 轮
        calls_before = self.server.calls
        self.run_engine(pdf)
        self.assertEqual(self.server.calls - calls_before, 6)

    def test_blank_page_rescues_empty_response(self):
        """模型对空白页返空 -> 判成功，但 md 里必须留可见标记；有字页返空 -> 仍算失败。"""
        pdf = os.path.join(self.tmp, "blank.pdf")
        make_pdf(pdf, 2, blank=[0])
        self.server.empty = True

        self.run_engine(pdf)

        with open(os.path.join(self.out_dir("blank"), "progress.json"), encoding="utf-8") as f:
            progress = json.load(f)
        self.assertTrue(progress["0"]["success"])
        self.assertTrue(progress["0"]["blank_rescued"])
        self.assertIn("[Blank page 0", progress["0"]["content"])
        self.assertFalse(progress["1"]["success"])
        md = os.path.join(self.out_dir("blank"), "blank.md")
        self.assertFalse(os.path.exists(md))
        self.assertIn("[Blank page 0", self.read(md + ".partial"))

    def test_light_gray_text_is_not_blank(self):
        """浅灰字（灰度 212）在旧阈值 200 下墨迹为 0，会被误判空白并丢内容。"""
        from PIL import Image, ImageDraw

        img = Image.new("RGB", (1000, 1400), "white")
        dr = ImageDraw.Draw(img)
        for y in range(100, 1300, 60):
            dr.text((100, y), "light gray text 0 1 2 3 4 5 6 7 8 9", fill=(212, 212, 212))
        self.assertFalse(OCREngine.is_blank_page(img))

    def test_stop_interrupts_inflight_request(self):
        """Stop 要能打断阻塞中的请求：读超时 8s，Stop 后必须秒级退出。"""
        pdf = self.make_book("inflight", 2)
        self.server.delay = 8

        engine = OCREngine(api_keys=["k"], dpi=72, api_url=self.url, workers=2, resume=True,
                           request_timeout=8, max_retries=1, page_deadline=30, save_interval=0)
        t = threading.Thread(target=lambda: engine.process_batch([pdf], self.tmp, None), daemon=True)
        t.start()
        time.sleep(0.6)
        t0 = time.time()
        engine.stop_requested = True
        t.join(timeout=6)

        self.assertFalse(t.is_alive(), "Stop 之后 6s 内没退出")
        self.assertLess(time.time() - t0, 2.0, "退出耗时接近读超时，说明请求没有被真正打断")

    def test_legacy_md_with_errors_and_no_progress_is_stale(self):
        """旧残 md（有失败标记）+ 无 progress：先归档 .stale 再整本重跑，新旧不并存。"""
        pdf = self.make_book("legacy_bad", 3)
        out = self.out_dir("legacy_bad")
        os.makedirs(out, exist_ok=True)
        md = os.path.join(out, "legacy_bad.md")
        with open(md, "w", encoding="utf-8") as f:
            f.write("> [Error on Page 1: boom]\n")

        self.run_engine(pdf)

        self.assertEqual(self.server.calls, 3)
        self.assertTrue(os.path.exists(md))
        self.assertFalse(os.path.exists(md + ".stale"))
        self.assertNotIn("[Error on Page", self.read(md))

    def test_blank_page_detection(self):
        from PIL import Image, ImageDraw

        white = Image.new("RGB", (1000, 1400), "white")
        self.assertTrue(OCREngine.is_blank_page(white))

        inked = Image.new("RGB", (1000, 1400), "white")
        ImageDraw.Draw(inked).rectangle([100, 100, 300, 300], fill="black")
        self.assertFalse(OCREngine.is_blank_page(inked))

    def test_progress_json_atomic(self):
        pdf = self.make_book("atomic", 4)
        self.run_engine(pdf)

        out = self.out_dir("atomic")
        self.assertFalse(os.path.exists(os.path.join(out, "progress.json.tmp")))
        with open(os.path.join(out, "progress.json"), encoding="utf-8") as f:
            self.assertEqual(len(json.load(f)), 4)

    def test_stop_interrupts_backoff(self):
        """Stop 后能迅速退出，不按 10 次退避傻等。"""
        pdf = self.make_book("stopping", 3)
        self.server.fail = True

        engine = OCREngine(api_keys=["k"], dpi=72, api_url=self.url, workers=2, resume=True,
                           request_timeout=5, max_retries=4, page_deadline=60, save_interval=0)
        t = threading.Thread(target=lambda: engine.process_batch([pdf], self.tmp, None), daemon=True)
        t.start()
        time.sleep(0.6)
        engine.stop_requested = True
        t.join(timeout=5)

        self.assertFalse(t.is_alive(), "engine 没有在 Stop 后及时退出")

    def test_engine_requires_api_key(self):
        with self.assertRaises(ValueError):
            OCREngine(api_keys=[], api_url=self.url)

    def test_legacy_md_without_progress_is_done(self):
        """旧语料：md 存在、无 progress.json -> 判定 done，不重转。"""
        pdf = self.make_book("legacy", 3)
        out = self.out_dir("legacy")
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "legacy.md"), "w", encoding="utf-8") as f:
            f.write("# a\n\n# b\n\n# c\n")

        self.run_engine(pdf)

        self.assertEqual(self.server.calls, 0)


class CliKeyTest(unittest.TestCase):
    def test_resolve_api_keys(self):
        import ocr_cli

        self.assertEqual(ocr_cli.resolve_api_keys({"api_key": "a,b\nc"}), ["a", "b", "c"])
        self.assertEqual(ocr_cli.resolve_api_keys({"api_key": ""}), [])
        self.assertEqual(ocr_cli.resolve_api_keys({}), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
