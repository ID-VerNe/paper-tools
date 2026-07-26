"""Fetch the latest Sci-Hub mirror list from upstream sources.

Provides two methods:
- crawl: scrapes lovescihub.wordpress.com for known mirrors
- brute_force_scan: probes all sci-hub.{xx} TLD combinations to find working mirrors

Both write results to link.txt.
"""

from __future__ import annotations

import itertools
import re
import string
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

MIRROR_FILE = Path(__file__).parent / "link.txt"

# ── helpers ──────────────────────────────────────────────────────────


def _make_session() -> requests.Session:
    """Create a session with retry logic and short timeouts."""
    session = requests.Session()
    retries = Retry(total=1, backoff_factor=0.5, status_forcelist=[429, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retries, pool_connections=20, pool_maxsize=20)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })
    return session


def _write_mirrors(mirrors: list[str]) -> None:
    """Deduplicate and write mirror list to *link.txt*."""
    seen: set[str] = set()
    unique: list[str] = []
    for m in mirrors:
        m = m.strip()
        if m not in seen and m:
            seen.add(m)
            unique.append(m)

    if not unique:
        return

    MIRROR_FILE.write_text("\n".join(unique) + "\n", encoding="utf-8")


# ── crawl method ─────────────────────────────────────────────────────


def update_link() -> int:
    """Crawl lovescihub.wordpress.com for active Sci-Hub mirrors.

    Returns the number of mirrors found (0 on failure).
    """
    pattern = re.compile(r">(htt[^:]+://sci-hub\.[^</]+)<")
    src_url = "https://lovescihub.wordpress.com/"

    try:
        resp = requests.get(src_url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[ERROR] Failed to fetch mirror list: {exc}")
        return 0

    mirrors = pattern.findall(resp.text)
    seen: set[str] = set()
    unique: list[str] = []
    for m in mirrors:
        m = m.strip()
        if m not in seen and m:
            seen.add(m)
            unique.append(m)

    if not unique:
        return 0

    _write_mirrors(unique)
    print(f"[INFO] Updated {len(unique)} mirror links")
    for link in unique:
        print(f"       {link}")
    return len(unique)


# ── brute-force scan method ──────────────────────────────────────────


def _check_mirror(url: str, session: requests.Session, timeout: int = 5) -> str | None:
    """Check if *url* is a working Sci-Hub mirror.

    Returns the URL if it responds with "Sci-Hub" in the page title, else None.
    """
    try:
        resp = session.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    # Sci-Hub pages typically have <title>Sci-Hub ...</title>
    if re.search(r"<title>\s*(Sci-Hub)", resp.text, re.IGNORECASE):
        return url

    return None


def _generate_candidates() -> list[str]:
    """Generate all sci-hub.{xx} candidate URLs (676 × 2 = 1352 combinations)."""
    candidates = []
    for a, b in itertools.product(string.ascii_lowercase, repeat=2):
        host = f"sci-hub.{a}{b}"
        candidates.append(f"http://{host}")
        candidates.append(f"https://{host}")
    return candidates


def brute_force_scan(max_workers: int = 30, timeout: int = 5) -> int:
    """Probe all sci-hub.{xx} TLD combinations to find working mirrors.

    Uses concurrent HTTP requests for speed. Writes found mirrors to
    *link.txt* and returns the count.
    """
    candidates = _generate_candidates()
    print(f"[INFO] Scanning {len(candidates)} candidate URLs ({max_workers} concurrent)...")

    session = _make_session()
    found: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_map = {
            executor.submit(_check_mirror, url, session, timeout): url
            for url in candidates
        }

        completed = 0
        total = len(candidates)
        for future in as_completed(future_map):
            completed += 1
            if completed % 100 == 0:
                print(f"[INFO] Progress: {completed}/{total} checked, {len(found)} found")

            result = future.result()
            if result is not None:
                print(f"  [FOUND] {result}")
                found.append(result)

    session.close()

    if not found:
        print("[WARN] No working Sci-Hub mirrors found via brute-force scan")
        return 0

    _write_mirrors(found)
    print(f"[INFO] Brute-force scan complete: {len(found)} mirrors found")
    return len(found)


# ── CLI ──────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Update Sci-Hub mirror list")
    parser.add_argument("--scan", action="store_true", help="Use brute-force scan instead of crawl")
    parser.add_argument("--workers", type=int, default=30, help="Concurrent workers for scan")
    parser.add_argument("--timeout", type=int, default=5, help="Timeout per URL in seconds")
    args = parser.parse_args()

    if args.scan:
        count = brute_force_scan(max_workers=args.workers, timeout=args.timeout)
    else:
        count = update_link()

    print(f"Done: {count} mirrors")