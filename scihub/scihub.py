"""Sci-Hub paper downloader.

Download academic papers from Sci-Hub by DOI, with automatic
mirror fallback, retry logic, and progress display.
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from update_link import update_link, brute_force_scan

logger = logging.getLogger(__name__)

# ── helpers ──────────────────────────────────────────────────────────


def _sanitize_filename(name: str) -> str:
    """Remove characters invalid for filenames on Windows."""
    return re.sub(r'[\\/:*?"<>|]', "_", name)[:200]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _read_lines(path: Path) -> list[str]:
    return [l.strip() for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _append_to(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(text + "\n")


# ── SciHub client ────────────────────────────────────────────────────


class SciHub:
    """Download a paper from Sci-Hub by DOI."""

    def __init__(self, doi: str, out: str | Path = ".", mirror_index: int = 0) -> None:
        self.doi = doi
        self.out = Path(out)
        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        )
        _ensure_dir(self.out)

        self._mirrors = self._load_mirrors()
        self._mirror_index = mirror_index

    # -- mirrors -------------------------------------------------------

    def _load_mirrors(self) -> list[str]:
        mirror_file = Path(__file__).parent / "link.txt"
        if not mirror_file.exists():
            logger.warning("link.txt not found at %s", mirror_file)
            return []
        return [l.strip() for l in mirror_file.read_text(encoding="utf-8").splitlines() if l.strip()]

    def refresh_mirrors(self) -> None:
        """Fetch latest Sci-Hub mirror list from upstream sources.

        Tries WordPress crawl first, falls back to brute-force scan.
        Caches results to link.txt.
        """
        count = update_link()
        if count:
            logger.info("Updated %d mirror links via WordPress crawl", count)
            self._mirrors = self._load_mirrors()
            return

        logger.info("WordPress crawl failed, trying brute-force scan...")
        count = brute_force_scan(max_workers=30, timeout=5)
        if count:
            logger.info("Brute-force scan found %d mirrors", count)
            self._mirrors = self._load_mirrors()
        else:
            logger.warning("No mirrors found via any method")

    # -- core download logic -------------------------------------------

    def download(self, output_mode: str = "doi") -> Optional[Path]:
        """Download the paper PDF.

        Returns the path to the saved PDF, or *None* on failure.
        """
        pdf_info = self._resolve_pdf()
        if pdf_info is None:
            logger.warning("Paper not found in Sci-Hub database: %s", self.doi)
            return None
        return self._save_pdf(pdf_info, output_mode=output_mode)

    def _resolve_pdf(self) -> Optional[dict]:
        """Get the real PDF URL and title for *self.doi*."""
        if self._mirror_index == -1:
            return self._auto_resolve()
        mirror = self._mirrors[self._mirror_index]
        return self._resolve_via(mirror)

    def _auto_resolve(self) -> Optional[dict]:
        """Try each mirror in order until one works."""
        for idx, mirror in enumerate(self._mirrors):
            logger.info("Trying mirror %d/%d: %s", idx + 1, len(self._mirrors), mirror)
            result = self._resolve_via(mirror)
            if result is not None:
                return result
            logger.warning("Mirror %s failed, trying next...", mirror)
        else:
            logger.error("All Sci-Hub mirrors exhausted")
            answer = input("No mirrors work. Auto-scan for working mirrors? (y/n): ").strip().lower()
            if answer == "y":
                return self._auto_scan_and_retry()
        return None

    def _auto_scan_and_retry(self) -> Optional[dict]:
        """Auto-scan for working mirrors, cache them, and retry download."""
        # First try the WordPress crawl (fast, known list)
        print("[INFO] Trying WordPress mirror list update...")
        count = update_link()
        if count:
            self._mirrors = self._load_mirrors()
            result = self._auto_resolve_pass()
            if result is not None:
                return result

        # Fallback: brute-force scan all sci-hub.{xx} TLDs
        print("[INFO] WordPress list failed. Brute-force scanning all sci-hub TLDs...")
        print("[INFO] This may take a minute (1352 URLs to probe)...")
        count = brute_force_scan(max_workers=30, timeout=5)
        if count:
            self._mirrors = self._load_mirrors()
            return self._auto_resolve_pass()
        else:
            print("[ERROR] No working mirrors found anywhere")
            return None

    def _auto_resolve_pass(self) -> Optional[dict]:
        """Try resolved mirrors once, no user prompt on failure."""
        for idx, mirror in enumerate(self._mirrors):
            logger.info("Retrying mirror %d/%d: %s", idx + 1, len(self._mirrors), mirror)
            result = self._resolve_via(mirror)
            if result is not None:
                return result
        return None

    def _resolve_via(self, mirror: str) -> Optional[dict]:
        """Try to get the PDF URL from a single Sci-Hub mirror."""
        # .red mirrors often redirect to .tw
        resolved = mirror.replace(".red", ".tw")

        paper_url = f"{resolved}/{self.doi}"
        try:
            resp = self._session.get(paper_url, stream=True, timeout=30)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.debug("Mirror %s failed: %s", mirror, exc)
            return None

        # Direct PDF
        ctype = resp.headers.get("Content-Type", "")
        if "application/pdf" in ctype:
            title = self._resolve_title_from_pdf(paper_url)
            return {"pdf_url": paper_url, "title": title}

        # Captcha page
        if self._is_captcha(resp):
            logger.warning("Captcha blocked on %s", mirror)
            return None

        # HTML page — extract PDF iframe / embed
        return self._extract_pdf_from_html(resp.text)

    def _resolve_title_from_pdf(self, pdf_url: str) -> str:
        """Fallback title from DOI when PDF is returned directly."""
        title = self.doi.replace("/", " ")
        return _sanitize_filename(title)

    def _extract_pdf_from_html(self, html: str) -> Optional[dict]:
        """Parse Sci-Hub HTML to find the embedded PDF URL and title."""
        # Check for "not found" messages
        not_found_phrases = [
            "статья не найдена в базе",
            "статья не найдена / article not found",
            "unfortunately, sci-hub doesn't have the requested document",
            "未收录本论文",
        ]
        for phrase in not_found_phrases:
            if phrase in html.lower():
                logger.debug("Article not found in Sci-Hub database")
                return None

        soup = BeautifulSoup(html, "html.parser")

        # Try iframe first, then embed
        pdf_url: Optional[str] = None
        for tag in soup.find_all("iframe"):
            src = tag.get("src")
            if src:
                pdf_url = src
                break
        if pdf_url is None:
            for tag in soup.find_all("embed"):
                src = tag.get("src")
                if src:
                    pdf_url = src
                    break
        if pdf_url is None:
            logger.error("Could not locate PDF URL in Sci-Hub page")
            return None

        # Normalise URL
        if pdf_url.startswith("//"):
            pdf_url = "https:" + pdf_url
        elif pdf_url.startswith("http:"):
            pdf_url = pdf_url.replace("http:", "https:", 1)

        # Extract title from page title
        title = "Unknown"
        if soup.title and "|" in soup.title.text:
            raw = soup.title.text.split("|", 1)[1].strip()
            title = raw.split("/")[0].split(".")[0].strip()
        if not title or title == "Unknown":
            title = pdf_url.rsplit("/", 1)[-1].replace(".pdf", "")
        title = _sanitize_filename(title)

        logger.info("PDF URL: %s", pdf_url)
        logger.info("Title:   %s", title)
        return {"pdf_url": pdf_url, "title": title}

    # -- download ------------------------------------------------------

    def _save_pdf(self, pdf_info: dict, output_mode: str = "doi") -> Optional[Path]:
        """Download the PDF from its URL and save to disk."""
        pdf_url = pdf_info["pdf_url"]

        if output_mode == "doi":
            filename = self.doi.replace("/", "_").replace("\\", "_").replace(".", "_") + ".pdf"
        else:
            filename = pdf_info["title"] + ".pdf"

        filepath = self.out / filename

        try:
            resp = self._session.get(pdf_url, stream=True, timeout=60)
            resp.raise_for_status()
        except requests.RequestException as exc:
            logger.error("Download failed: %s", exc)
            return None

        if self._is_captcha(resp):
            logger.warning("Captcha blocked on PDF URL: %s", pdf_url)
            return None

        # Retry until Content-Length appears
        retries = 0
        while "Content-Length" not in resp.headers and retries < 10:
            logger.debug("Retrying (no Content-Length)…")
            resp.close()
            resp = self._session.get(pdf_url, stream=True, timeout=60)
            retries += 1

        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0

        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=64 * 1024):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\rDownloading: [{pct:3d}%] {downloaded} / {total}", end="", flush=True)
                else:
                    print(f"\rDownloading: {downloaded} bytes", end="", flush=True)
        print()

        logger.info("Saved to %s", filepath)
        return filepath

    # -- misc ----------------------------------------------------------

    @staticmethod
    def _is_captcha(resp: requests.Response) -> bool:
        cc = resp.headers.get("Cache-Control", "")
        return "must-revalidate" in cc


# ── batch processing ──────────────────────────────────────────────────


def _batch_download(
    doi_list: list[str],
    mirror_index: int = 0,
    output_mode: str = "doi",
    sleep_base: int = 30,
    max_retries: int = 5,
) -> tuple[int, int]:
    """Download a batch of DOIs.

    Returns (success_count, fail_count).
    """
    success = 0
    failed = 0

    for idx, doi in enumerate(doi_list):
        logger.info("Processing %d/%d: %s", idx + 1, len(doi_list), doi)

        # Periodic rest
        if idx and idx % 30 == 0:
            for sec in range(sleep_base, 0, -1):
                print(f"\rResting {sec}s…", end="", flush=True)
                time.sleep(1)
            print()

        # Retry loop
        for attempt in range(1, max_retries + 1):
            try:
                client = SciHub(doi, out="paper", mirror_index=mirror_index)
                result = client.download(output_mode=output_mode)
                if result is not None:
                    success += 1
                    logger.info("Done: %s", doi)
                    break
                else:
                    logger.warning("Not found in Sci-Hub: %s", doi)
                    failed += 1
                    break
            except Exception as exc:
                logger.error("Attempt %d/%d failed: %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    wait = sleep_base + (attempt - 1) * 10
                    for sec in range(wait, 0, -1):
                        print(f"\rRetry in {sec}s…", end="", flush=True)
                        time.sleep(1)
                    print()
                else:
                    logger.error("Giving up on %s after %d attempts", doi, max_retries)
                    failed += 1
        else:
            # max_retries == 0 — try once with no retry
            try:
                client = SciHub(doi, out="paper", mirror_index=mirror_index)
                result = client.download(output_mode=output_mode)
                if result is not None:
                    success += 1
                else:
                    failed += 1
            except Exception as exc:
                logger.error("Failed: %s", exc)
                failed += 1

    return success, failed


# ── CLI ───────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sci-Hub paper downloader")
    parser.add_argument(
        "-p", "--doi-file", default="doi.txt",
        help="File containing DOIs, one per line (default: doi.txt)",
    )
    parser.add_argument(
        "--doi", nargs="+",
        help="Download one or more DOIs directly (overrides -p)",
    )
    parser.add_argument(
        "--no-retry", action="store_true",
        help="Disable automatic retry on failure",
    )
    parser.add_argument(
        "--sleep", type=int, default=30,
        help="Base sleep seconds between items (default: 30)",
    )
    parser.add_argument(
        "--max-retries", type=int, default=5,
        help="Maximum retry attempts per DOI (default: 5)",
    )
    parser.add_argument(
        "-m", "--mode", choices=["doi", "title"], default="doi",
        help="Output filename mode (default: doi)",
    )
    parser.add_argument(
        "--mirror", type=int, default=0,
        help="Mirror index (default: 0, -1 = auto)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Show debug-level log messages",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s  %(levelname)-8s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Collect DOIs
    doi_list: list[str] = []
    if args.doi:
        doi_list = args.doi
    else:
        doi_path = Path(args.doi_file)
        if not doi_path.exists():
            logger.error("DOI file not found: %s", doi_path)
            sys.exit(1)
        doi_list = _read_lines(doi_path)

    if not doi_list:
        logger.warning("No DOIs to process")
        return

    ok, fail = _batch_download(
        doi_list,
        mirror_index=args.mirror,
        output_mode=args.mode,
        sleep_base=args.sleep,
        max_retries=0 if args.no_retry else args.max_retries,
    )
    logger.info("Batch complete: %d ok, %d failed", ok, fail)


if __name__ == "__main__":
    main()