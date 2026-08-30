"""identity.py — three-layer paper identity mapping (zero-LLM, zero-dependency).

Resolves a paper's identity from two sources, in a strict trust order:

  1. doi_map.json  (pipeline-verified DOI: pdf_name -> doi + status)
  2. metadata JSON (LLM-extracted bibliographic: {citation: {title, authors, year, doi}})

Trust layering (MS §2.3, master plan Phase 2 step 1):
  - clean        : doi_map matched, DOI is a real DOI (not .fmatter, not None)
  - fmatter_trap : doi_map "matched" but DOI ends in .fmatter (book front-matter,
                   NOT the chapter DOI) -> DOI suppressed to "" and flagged.
                   The .fmatter suffix is a known Crossref trap on book chapters.
  - unresolved   : doi_map status=no_confidence (DOI is None) -> DOI="". The
                   bibliographic metadata is still recorded (title/authors/year
                   survive) so the paper is queryable by title, just not DOI-cited.
  - unresolved_meta : doi_map unresolved BUT the metadata JSON carried a real DOI.
                   Recorded with status=unresolved_meta (metadata DOI not promoted
                   to authoritative without verification).

A paper_id is the underscore-stem of the PDF filename (matches the directory
layout in ocr_output/ and the derived JSON filename convention).
"""
import json
import os
import re

_FMATTER_RE = re.compile(r"\.fmatter$", re.IGNORECASE)
_DOI_PREFIX_RE = re.compile(r"^10\.\d{4,9}/")
_NOT_REPORTED = {"not reported", "n/a", "na", "none", "null", ""}


def paper_id_from_pdf_name(pdf_name):
    """'10.1109_tii.2020.2999571.pdf' -> '10.1109_tii.2020.2999571'.

    The stem already uses underscores as DOI-path separators (the corpus layout),
    so we only strip the .pdf suffix.
    """
    return os.path.splitext(pdf_name)[0]


def _clean_field(value):
    """Normalize 'not reported' / null placeholders to a canonical empty form."""
    if value is None:
        return ""
    if isinstance(value, list):
        return [_clean_field(v) for v in value]
    s = str(value).strip()
    if s.lower() in _NOT_REPORTED:
        return ""
    return s


def load_doi_map(doi_map_path):
    """Load doi_map.json -> {paper_id: entry}.

    Keys are paper_id (underscore stem), values are the raw entries.
    """
    with open(doi_map_path, encoding="utf-8") as f:
        raw = json.load(f)
    out = {}
    for e in raw:
        pid = paper_id_from_pdf_name(e["pdf_name"])
        out[pid] = e
    return out


def doi_status(entry):
    """Classify a doi_map entry into one of: clean, fmatter_trap, unresolved."""
    doi = entry.get("doi") or ""
    status = entry.get("status") or ""
    if status == "no_confidence" or doi == "":
        return "unresolved"
    if _FMATTER_RE.search(doi):
        return "fmatter_trap"
    return "clean"


def load_metadata_json(metadata_path):
    """Parse a metadata derived JSON ({citation:{title,authors,year,doi,journal}}).

    Returns a dict with title, authors (list or ""), year (str), doi (str, "" if
    not reported). 'not reported' placeholders are cleaned to "".
    """
    with open(metadata_path, encoding="utf-8") as f:
        d = json.load(f)
    c = d.get("citation", {}) if isinstance(d, dict) else {}
    return {
        "title": _clean_field(c.get("title")),
        "authors": _clean_field(c.get("authors")) or "",
        "year": _clean_field(c.get("year")),
        "doi": _clean_field(c.get("doi")),
        "journal": _clean_field(c.get("journal")),
    }


def _is_real_doi(doi):
    """A DOI is real if it matches 10.NNNN/... and isn't a fmatter/not-reported."""
    if not doi:
        return False
    if _FMATTER_RE.search(doi):
        return False
    return bool(_DOI_PREFIX_RE.match(doi))


def resolve_doi(doi_map_entry, metadata_doi):
    """Resolve the authoritative DOI for one paper.

    Returns (doi, status):
      - clean           : doi_map matched, real DOI
      - fmatter_trap    : doi_map DOI is .fmatter -> suppress to ""
      - unresolved      : no real DOI from either source -> ""
      - unresolved_meta : doi_map unresolved, but metadata carried a real DOI
                          (recorded, not promoted to authoritative)
    """
    st = doi_status(doi_map_entry)
    map_doi = doi_map_entry.get("doi") or ""
    meta_doi = metadata_doi or ""

    if st == "clean":
        return map_doi, "clean"
    if st == "fmatter_trap":
        # .fmatter is NOT a real chapter DOI. If metadata has a real one,
        # record it but flag unresolved_meta (it came from extraction, not
        # crossref verification). Otherwise suppress entirely.
        if _is_real_doi(meta_doi):
            return meta_doi, "unresolved_meta"
        return "", "fmatter_trap"
    # st == "unresolved"
    if _is_real_doi(meta_doi):
        return meta_doi, "unresolved_meta"
    return "", "unresolved"


def build_identity(doi_map_path, metadata_dir, paper_ids, model_alias="gemini-flash-lite"):
    """Build {paper_id: {doi, status, title, authors, year, ...}} for the given papers.

    metadata_dir holds files named {paper_id}_metadata_{model_alias}.json.
    If a metadata JSON is missing, bibliographic fields default to "" (paper_id
    survives as the fallback title in know.py).
    """
    dm = load_doi_map(doi_map_path)
    out = {}
    for pid in paper_ids:
        entry = dm.get(pid, {"pdf_name": pid + ".pdf", "doi": None,
                             "status": "no_confidence"})
        meta_path = os.path.join(metadata_dir, f"{pid}_metadata_{model_alias}.json")
        meta = load_metadata_json(meta_path) if os.path.exists(meta_path) else {
            "title": "", "authors": "", "year": "", "doi": "", "journal": "",
        }
        doi, st = resolve_doi(entry, meta["doi"])
        out[pid] = {
            "paper_id": pid,
            "doi": doi,
            "status": st,
            "title": meta["title"] or entry.get("title", "") or pid,
            "authors": meta["authors"] if meta["authors"] != "" else
                      (_clean_field(entry.get("author")) or ""),
            "year": meta["year"] or _clean_field(entry.get("year")),
            "journal": meta["journal"],
            "source_rel": pid,
        }
    return out
