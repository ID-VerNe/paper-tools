"""corpusB_verify.py — verify corpus B DOIs (structural + metadata) via Crossref.

Corpus B (thesis-litrev, P2P energy trading) has no pipeline doi_map. Two DOI
sources are candidates for verification:

  1. underscore-family dir names  (structural decode, first `_` after
     `10.NNNN` is the `/` separator):
        10.1007_978-981-97-6349-8_58  -> 10.1007/978-981-97-6349-8_58
  2. LLM metadata JSON `citation.doi` (14 papers carry a real-looking DOI,
     incl. most hyphen-family papers).

Both are candidate-only. This script verifies each via Crossref: the DOI must
resolve AND the Crossref title must match the LLM-extracted metadata title
(NFKC + casefold + whitespace collapse, similarity >= 0.9). Verified entries
are written to corpusB_doi_map.json — the identity layer's top trust tier
(pipeline-verified), which upgrades those papers to `clean` on ingest. DOIs
that fail (crossref 404 / title mismatch) are left OUT so the metadata record
stays `unresolved_meta` (recorded, not promoted without verification).

Usage:
    uv run --no-project --with httpx python phase0-lab/corpusB_verify.py \
        [--corpus DIR] [--out phase0-lab/corpusB_doi_map.json]
"""
import json
import os
import re
import argparse
import unicodedata
import difflib

import httpx

# corpus B root
CORPUS = r"C:/Onedrive/VerNe/HKU/论文/01_Literature/论文库/新版本"
METADATA_DIR = os.path.join(CORPUS, "extracted", "derived")
API = "https://api.crossref.org/works/{doi}"
UA = "paper-tools/1.0 (mailto:yuu_seeing@foxmail.com)"
SIM_THRESHOLD = 0.9

# underscore-family paper ids (the only structural-decode candidates)
UNDERSCORE_PAPERS = [
    "10.1007_978-981-97-6349-8_58",
    "10.1049_rpg2.70075",
    "10.1109_ACCESS.2025.3572174",
    "10.3390_app142411510",
    "10.3390_en17071702",
]


def decode_underscore(paper_id):
    """'10.3390_en17071702' -> '10.3390/en17071702' (first _ after 10.NNNN)."""
    m = re.match(r"^(10\.\d{4,9})_(.+)$", paper_id)
    if not m:
        return None
    return m.group(1) + "/" + m.group(2)


def _norm(text):
    s = unicodedata.normalize("NFKC", str(text))
    return re.sub(r"\s+", " ", s).strip().casefold()


def crossref_record(doi):
    """Return {'title','year','found'} for a DOI (found=False on 404)."""
    url = API.format(doi=doi.replace("/", "%2F"))
    with httpx.Client(timeout=30, verify=False) as c:
        r = c.get(url, headers={"User-Agent": UA})
    if r.status_code != 200:
        return {"title": "", "year": "", "found": False}
    msg = r.json().get("message", {})
    titles = msg.get("title") or []
    year = ""
    for k in ("published-print", "published-online", "issued"):
        for d in (msg.get(k) or {}).get("date-parts") or []:
            if d:
                year = d[0]
                break
        if year:
            break
    return {"title": titles[0] if titles else "", "year": year, "found": True}


def metadata_citation(paper_id):
    path = os.path.join(METADATA_DIR, f"{paper_id}_metadata_deepseek.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("citation", {}) or {}


def paper_choices(mode, metadata_dir):
    """Candidate (paper_id, doi_candidate, source) pairs to verify."""
    candidates = []
    for pid in UNDERSCORE_PAPERS:
        doi = decode_underscore(pid)
        if doi:
            candidates.append((pid, doi, "structural"))
    if mode == "structural":
        return candidates
    # "all": also verify every metadata doi (hyphen family etc.)
    for f in sorted(os.listdir(metadata_dir)):
        if not f.endswith("_metadata_deepseek.json"):
            continue
        pid = f.removesuffix("_metadata_deepseek.json")
        if pid in UNDERSCORE_PAPERS:
            continue  # structural pass covers these
        cite = metadata_citation(pid)
        doi = cite.get("doi")
        if isinstance(doi, str) and doi.strip():
            candidates.append((pid, doi.strip(), "metadata"))
    return candidates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default=CORPUS)
    ap.add_argument("--out", default=os.path.join(
        os.path.dirname(__file__), "corpusB_doi_map.json"))
    ap.add_argument("--mode", choices=["structural", "all"], default="all",
                    help="structural=underscore decode only; "
                         "all=+ every metadata doi (default)")
    args = ap.parse_args()

    entries = []
    pairs = paper_choices(args.mode, os.path.join(args.corpus, "extracted",
                                                  "derived"))
    print(f"verifying {len(pairs)} DOI candidates via crossref "
          f"(mode={args.mode})...")
    for pid, doi, source in pairs:
        rec = crossref_record(doi)
        if not rec["found"]:
            print(f"  {pid[:48]:50s} {doi[:34]:36s} NOT FOUND")
            continue
        meta_title = metadata_citation(pid).get("title", "")
        sim = difflib.SequenceMatcher(
            None, _norm(meta_title), _norm(rec["title"])).ratio()
        match = bool(rec["title"]) and sim >= SIM_THRESHOLD
        print(f"  {pid[:48]:50s} {doi[:34]:36s} "
              f"title sim={sim:.2f} {'-> VERIFIED' if match else '(no match)'}")
        if match:
            entries.append({
                "pdf_name": pid + ".pdf",
                "doi": doi,
                "title": rec["title"],
                "year": rec["year"] or "",
                "status": "matched",
                "verified": True,
                "method": f"crossref-title-sim>={SIM_THRESHOLD} ({source})",
            })

    # dedup by pdf_name (keep first)
    seen = set()
    entries = [e for e in entries
               if not (e["pdf_name"] in seen or seen.add(e["pdf_name"]))]
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    print(f"\nwrote {len(entries)} verified entries -> {args.out}")
    for e in sorted(entries, key=lambda x: x["pdf_name"]):
        print(f"  {e['doi']:38s} {e['title'][:70]}")


if __name__ == "__main__":
    main()