"""anchors.py — deterministic paragraphization + anchor id computation.

Phase 0/0.5/1.0 validated: OCR paragraph text is stable (<5% drift,
all ratio >= 0.95). Content-addressed anchors (sha1-8 of normalized
paragraph) are the primary anchor; fuzzy rematch is the fallback for
the ~5% drift lines.

This module is zero-LLM and zero-dependency (stdlib only).
"""
import re
import hashlib
import unicodedata
from difflib import SequenceMatcher

MATCHED = "matched"
BROKEN = "broken"
UNMATCHED = "unmatched"

_HEADING_RE = re.compile(r"^#{1,6}\s")


def _normalize(text):
    """NFKC normalize + collapse all whitespace to single spaces + strip."""
    return " ".join(unicodedata.normalize("NFKC", text).split())


def coerce_quote(value):
    """Coerce an evidence_quotes field value to a string (or None).

    The v2 prompt contract says evidence_quotes values are verbatim strings
    or null. In practice the LLM sometimes returns a list for fields that are
    naturally multi-valued (e.g. key_limitations_mentioned). A list of quotes
    is joined with ' ... ' so each item becomes a fragment — classify_fragmented
    then enforces 'all fragments must anchor', so a list with one bad item is
    dropped (not fabricated). None stays None. Non-string scalars are str()'d.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, list):
        parts = [str(x).strip() for x in value
                 if x is not None and str(x).strip()]
        return " ... ".join(parts) if parts else None
    return str(value).strip() or None


def paragraphize(ocr_md_text):
    """Split OCR markdown into a list of content paragraphs.

    Skips blank lines and heading lines (^#{1,6}\\s). Each surviving line is
    NFKC-normalized and whitespace-folded. Returns a list of paragraph strings.
    """
    out = []
    for raw in ocr_md_text.split("\n"):
        t = raw.rstrip("\r")
        if not t.strip():
            continue
        if _HEADING_RE.match(t):
            continue
        out.append(_normalize(t))
    return out


def anchor_id(paragraph):
    """Return 'p' + first 8 hex chars of sha1(normalized paragraph).

    The normalization matches paragraphize, so the same logical paragraph
    yields a stable id regardless of incidental whitespace differences.
    """
    norm = _normalize(paragraph)
    h = hashlib.sha1(norm.encode("utf-8")).hexdigest()
    return "p" + h[:8]


def _window_best(quote, para):
    """Best ratio of quote vs a same-length sliding window inside para."""
    nq, np_ = len(quote), len(para)
    if np_ <= nq:
        return SequenceMatcher(None, quote, para).ratio()
    best = 0.0
    step = max(1, nq // 4)
    for i in range(0, np_ - nq + 1, step):
        r = SequenceMatcher(None, quote, para[i:i + nq]).ratio()
        if r > best:
            best = r
        if best >= 0.99:
            break
    return best


def rematch(quote, paragraphs):
    """Fuzzy-match quote against paragraphs.

    Exact substring -> MATCHED (1.0). Otherwise sliding-window difflib ratio:
    >=0.85 MATCHED, 0.60-0.85 BROKEN, <0.60 UNMATCHED.

    Returns (status, ratio).
    """
    nq = _normalize(quote)
    if not nq:
        return (UNMATCHED, 0.0)
    for p in paragraphs:
        if nq in p:
            return (MATCHED, 1.0)
    best_r = 0.0
    for p in paragraphs:
        r = _window_best(nq, p)
        if r > best_r:
            best_r = r
    if best_r >= 0.85:
        return (MATCHED, best_r)
    if best_r >= 0.60:
        return (BROKEN, best_r)
    return (UNMATCHED, best_r)


def classify_fragmented(quote, paragraphs):
    """Anchor a quote that may join fragments with '...'.

    Splits on '...' (per the v2 prompt rules), classifies each fragment
    independently, returns the worst (lowest-ratio) outcome as the binding
    constraint. Phase 1.0 confirmed fragments are the anchorable unit.
    """
    nq = _normalize(quote)
    if not nq:
        return (UNMATCHED, 0.0)
    frags = [f.strip() for f in re.split(r"\s*\.{3}\s*", nq) if f.strip()]
    if len(frags) <= 1:
        return rematch(quote, paragraphs)
    worst = (MATCHED, 1.0)
    for f in frags:
        st, r = rematch(f, paragraphs)
        if r < worst[1]:
            worst = (st, r)
    return worst
