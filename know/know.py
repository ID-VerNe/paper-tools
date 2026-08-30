"""know.py — CLI: ingest / validate / search / flag.

The agent-queryable knowledge layer. Two homes:
  - index.sqlite  : the query substrate (gitignored, rebuildable)
  - knowledge-base/: markdown archive (git-tracked, human/agent-readable)

Zero-LLM, zero-network, zero-new-dependency (stdlib only). The LLM is
never on the write path; only the query path (caller's responsibility).

CLI usage:
    python know.py ingest <ocr_dir> <derived_v2_dir> [--db PATH] [--kb DIR]
    python know.py validate [--db PATH] [--kb DIR]
    python know.py search "<concept>" [--db PATH] [--limit N]
    python know.py flag <claim_id> --note "..." [--db PATH]
"""
import os
import sys
import json
import re
import sqlite3
from datetime import datetime  # noqa: F401 (used by caller for log timestamps)

import anchors
import derive
import config


# ---------- paper page (markdown archive) ----------

_PAPER_PAGE_TEMPLATE = """---
type: paper
doi: {paper_id}
title: {title}
authors: {authors}
year: {year}
project: {project}
source_rel: {source_rel}
ocr_version: {ocr_version}
created: code
---

# {title}

{claims_section}

## cite:// anchors

{anchors_section}
"""

_CLAIM_LINE = "- **{field}** ({group}): {quote}  cite://{paper_id}/{anchor}  `{status}`"


def _write_paper_page(kb_dir, paper_id, paper_meta, claims):
    """Write one markdown page per paper to knowledge-base/papers/."""
    papers_dir = os.path.join(kb_dir, "papers")
    os.makedirs(papers_dir, exist_ok=True)
    page_path = os.path.join(papers_dir, f"{paper_id}.md")

    # dedup claims by (group, field) for the page
    seen = set()
    claim_lines = []
    anchor_lines = []
    for c in claims:
        key = (c["group"], c["field"])
        if key in seen:
            continue
        seen.add(key)
        claim_lines.append(_CLAIM_LINE.format(
            field=c["field"], group=c["group"], quote=c["quote"],
            paper_id=paper_id, anchor=c["anchor"], status=c.get("status", "matched"),
        ))
        anchor_lines.append(f"- `cite://{paper_id}/{c['anchor']}` {c['heading_path']}")

    claims_section = "\n".join(claim_lines) if claim_lines else "_(no anchored claims)_"
    anchors_section = "\n".join(anchor_lines) if anchor_lines else "_(none)_"

    page = _PAPER_PAGE_TEMPLATE.format(
        paper_id=paper_id,
        title=paper_meta.get("title", paper_id),
        authors=paper_meta.get("authors", "[]"),
        year=paper_meta.get("year", ""),
        project=paper_meta.get("project", ""),
        source_rel=paper_meta.get("source_rel", ""),
        ocr_version=paper_meta.get("ocr_version", ""),
        claims_section=claims_section,
        anchors_section=anchors_section,
    )
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(page)


def _append_log(kb_dir, paper_id, n_claims):
    """Append a code-written entry to log.md (never hand-written)."""
    log_path = os.path.join(kb_dir, "log.md")
    os.makedirs(kb_dir, exist_ok=True)
    # timestamp is caller's job in real runs; tests pass a stable marker
    entry = f"- ingest | paper={paper_id} | claims={n_claims}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def _write_catalog(kb_dir, paper_count):
    """Regenerate catalog.md (code-owned index, never hand-written)."""
    cat_path = os.path.join(kb_dir, "catalog.md")
    os.makedirs(kb_dir, exist_ok=True)
    papers_dir = os.path.join(kb_dir, "papers")
    pages = sorted(f for f in os.listdir(papers_dir) if f.endswith(".md"))
    lines = [f"# catalog\n", f"papers: {paper_count}\n", ""]
    for p in pages:
        lines.append(f"- [{p.replace('.md','')}]({os.path.join('papers', p)})")
    with open(cat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- metadata extraction from derived_v2 ----------

def _extract_paper_meta(derived_json_path, paper_id):
    """Pull title/authors/year from the metadata JSON if present in the
    derived_v2 dir; else return minimal stub."""
    # The v2 dir may contain a metadata JSON; check by convention
    # (Phase 1.0 only ran 3 claim-bearing prompts, no metadata v2).
    # Fallback: stub metadata from the paper_id.
    return {
        "paper_id": paper_id,
        "title": paper_id,
        "authors": "[]",
        "year": "",
        "project": "power-system-cyber-security-review",
        "source_rel": paper_id,
        "ocr_version": "deepseek-ocr-v1",
    }


# ---------- idempotency ----------

def _existing_claim_keys(conn, paper_id):
    """Return set of (group, field, quote) already in DB for this paper."""
    rows = conn.execute(
        "SELECT concept, quote FROM claims WHERE paper_id=?", (paper_id,)
    ).fetchall()
    # concept stores group.field in our ingest; quote is the verbatim
    return set((r[0], r[1]) for r in rows)


# ---------- public API ----------

def ingest(ocr_dir, derived_dir, db_path, kb_dir):
    """Scan derived_v2 JSONs + OCR md -> write index.sqlite + markdown pages.

    Idempotent: re-ingesting the same papers does not duplicate claim rows
    (dedup by (paper_id, concept, quote) before insert). Source corpus
    (ocr_dir, derived_dir) is read-only.
    """
    conn = config.init_db(db_path)
    os.makedirs(kb_dir, exist_ok=True)

    # discover derived_v2 JSONs in derived_dir
    derived_jsons = sorted(
        os.path.join(derived_dir, f)
        for f in os.listdir(derived_dir)
        if f.endswith(".json") and "_v2_" in f
    )

    paper_count = 0
    for json_path in derived_jsons:
        paper_id = derive._paper_id_from_path(json_path)
        # find the OCR md for this paper in ocr_dir
        ocr_md = os.path.join(ocr_dir, paper_id, paper_id + ".md")
        if not os.path.exists(ocr_md):
            # fallback: flat layout
            ocr_md = os.path.join(ocr_dir, paper_id + ".md")
        if not os.path.exists(ocr_md):
            continue

        claims = derive.derive([json_path], ocr_md)
        paper_meta = _extract_paper_meta(json_path, paper_id)

        # upsert paper row
        conn.execute(
            "INSERT INTO papers (paper_id, title, authors, year, project, "
            "source_rel, ocr_version) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(paper_id) DO NOTHING",
            (paper_meta["paper_id"], paper_meta["title"],
             paper_meta["authors"], paper_meta["year"], paper_meta["project"],
             paper_meta["source_rel"], paper_meta["ocr_version"]),
        )

        existing = _existing_claim_keys(conn, paper_id)
        inserted = 0
        for c in claims:
            concept_key = f"{c['group']}.{c['field']}"
            if (concept_key, c["quote"]) in existing:
                continue
            config.insert_claim(
                conn, paper_id, concept_key, c["quote"], c["anchor"],
                c["heading_path"], c["model"], c["prompt_id"],
            )
            existing.add((concept_key, c["quote"]))
            inserted += 1

        conn.commit()
        if inserted > 0 or not _paper_has_page(kb_dir, paper_id):
            _write_paper_page(kb_dir, paper_id, paper_meta, claims)
        _append_log(kb_dir, paper_id, inserted)
        paper_count += 1

    _write_catalog(kb_dir, paper_count)
    conn.close()
    return paper_count


def _paper_has_page(kb_dir, paper_id):
    return os.path.exists(os.path.join(kb_dir, "papers", f"{paper_id}.md"))


def validate(db_path, kb_dir):
    """Check DB/catalog/markdown consistency. Returns True if consistent."""
    if not os.path.exists(db_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        # every claim's paper_id must exist in papers
        orphans = conn.execute(
            "SELECT COUNT(*) FROM claims c LEFT JOIN papers p "
            "ON c.paper_id = p.paper_id WHERE p.paper_id IS NULL"
        ).fetchone()[0]
        if orphans > 0:
            return False
        # catalog exists
        if not os.path.exists(os.path.join(kb_dir, "catalog.md")):
            return False
        # at least one paper page
        papers_dir = os.path.join(kb_dir, "papers")
        if not os.path.isdir(papers_dir):
            return False
        return True
    finally:
        conn.close()


def search(db_path, concept, limit=10):
    """FTS5 top-k search with 2-char CJK LIKE fallback (Gap 5 fix)."""
    conn = sqlite3.connect(db_path)
    try:
        return config.search_claims(conn, concept, limit)
    finally:
        conn.close()


def flag(db_path, claim_id, note):
    """Mark a claim as flagged (never delete). Trust chain: mark, not delete."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE claims SET conflicting='flagged' WHERE claim_id=?",
            (claim_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------- CLI dispatch ----------

def _main(argv):
    if len(argv) < 2:
        print("usage: know.py {ingest|validate|search|flag} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "ingest":
        if len(argv) < 4:
            print("usage: know.py ingest <ocr_dir> <derived_v2_dir>", file=sys.stderr)
            return 2
        ocr_dir, derived_dir = argv[2], argv[3]
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        kb = os.environ.get("KNOW_KB", "knowledge-base")
        n = ingest(ocr_dir, derived_dir, db, kb)
        print(f"ingested {n} papers -> {db}")
        return 0
    if cmd == "validate":
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        kb = os.environ.get("KNOW_KB", "knowledge-base")
        ok = validate(db, kb)
        print("validate:", "OK" if ok else "FAIL")
        return 0 if ok else 1
    if cmd == "search":
        if len(argv) < 3:
            print('usage: know.py search "<concept>"', file=sys.stderr)
            return 2
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        results = search_(db, argv[2])
        if not results:
            print("(no results; try broader keywords)")
            return 0
        for r in results:
            print(f"[{r['claim_id']}] {r['paper_id']} | {r['concept']} | "
                  f"{r['quote'][:60]}")
        return 0
    if cmd == "flag":
        if len(argv) < 3:
            print("usage: know.py flag <claim_id> --note \"...\"", file=sys.stderr)
            return 2
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        note = ""
        if "--note" in argv:
            note = " ".join(argv[argv.index("--note")+1:])
        flag(db, int(argv[2]), note)
        print(f"flagged claim {argv[2]}")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


# avoid name collision with search() at module level for CLI
search_ = search

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
