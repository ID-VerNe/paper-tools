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
    python know.py concept <slug> --query "<concept>" [--db PATH] [--kb DIR]
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
import identity


# ---------- paper page (markdown archive) ----------

_PAPER_PAGE_TEMPLATE = """---
type: paper
doi: {doi}
title: {title}
authors: {authors}
year: {year}
project: {project}
source_rel: {source_rel}
ocr_version: {ocr_version}
identity_status: {identity_status}
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
        doi=paper_meta.get("doi", ""),
        title=paper_meta.get("title", paper_id),
        authors=paper_meta.get("authors", "[]"),
        year=paper_meta.get("year", ""),
        project=paper_meta.get("project", ""),
        source_rel=paper_meta.get("source_rel", ""),
        ocr_version=paper_meta.get("ocr_version", ""),
        identity_status=paper_meta.get("identity_status", "clean"),
        claims_section=claims_section,
        anchors_section=anchors_section,
    )
    with open(page_path, "w", encoding="utf-8") as f:
        f.write(page)


def _append_log(kb_dir, paper_id, n_claims, tag="ingest"):
    """Append a code-written entry to log.md (never hand-written).

    tag is "ingest" (initial/full load) or "backfill" (pre-overwrite
    snapshot — see ingest(..., backfill=True)).
    """
    log_path = os.path.join(kb_dir, "log.md")
    os.makedirs(kb_dir, exist_ok=True)
    # timestamp is caller's job in real runs; tests pass a stable marker
    entry = f"- {tag} | paper={paper_id} | claims={n_claims}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(entry)


def _write_catalog(kb_dir):
    """Regenerate catalog.md (code-owned index, never hand-written).

    The header count is len(pages), not the last batch size, so merging a
    second corpus (A + B in one layer) keeps the index accurate.
    """
    cat_path = os.path.join(kb_dir, "catalog.md")
    os.makedirs(kb_dir, exist_ok=True)
    papers_dir = os.path.join(kb_dir, "papers")
    pages = sorted(f for f in os.listdir(papers_dir) if f.endswith(".md"))
    lines = [f"# catalog\n", f"papers: {len(pages)}\n", ""]
    for p in pages:
        lines.append(f"- [{p.replace('.md','')}]({os.path.join('papers', p)})")
    with open(cat_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------- metadata extraction from identity layer ----------

def _build_paper_meta(identity_rec, paper_id, project="power-system-cyber-security-review",
                      ocr_version="deepseek-ocr-v1"):
    """Build the paper_meta dict the ingest loop needs, from an identity record.

    identity_rec comes from identity.build_identity(); it carries doi, status,
    title, authors, year, journal, source_rel. The authors field is normalized
    to a JSON string for the papers table (front matter wants a list shape).
    """
    authors = identity_rec.get("authors") or ""
    if isinstance(authors, list):
        import json as _json
        authors = _json.dumps(authors, ensure_ascii=False)
    return {
        "paper_id": paper_id,
        "doi": identity_rec.get("doi", ""),
        "title": identity_rec.get("title") or paper_id,
        "authors": authors or "[]",
        "year": identity_rec.get("year", ""),
        "project": project,
        "source_rel": identity_rec.get("source_rel", paper_id),
        "ocr_version": ocr_version,
        "identity_status": identity_rec.get("status", "clean"),
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

def ingest(ocr_dir, derived_dir, db_path, kb_dir,
           doi_map_path=None, metadata_dir=None, model_alias="gemini-flash-lite",
           project="power-system-cyber-security-review", backfill=False):
    """Scan derived_v2 JSONs + OCR md -> write index.sqlite + markdown pages.

    Idempotent: re-ingesting the same papers does not duplicate claim rows
    (dedup by (paper_id, concept, quote) before insert). Source corpus
    (ocr_dir, derived_dir) is read-only.

    When doi_map_path + metadata_dir are given, each paper's title/authors/
    year/doi come from identity.build_identity (doi_map + metadata JSON),
    with the three-layer trust order (clean / fmatter_trap / unresolved).

    backfill=True is the pre-overwrite snapshot (MS golden rule, master plan
    Phase 2 step 6): call it BEFORE a litrev-extract re-run overwrites the
    derived JSON, so the old extraction claims are preserved in the layer.
    Semantically identical to a normal ingest (same idempotent anchor+dedup),
    but log.md entries are tagged 'backfill' so the archive records which
    claims came from a preservation snapshot vs the initial load.

    project tags every paper page + papers row (schema: papers.project),
    so one knowledge layer can hold several reviews (e.g. corpus A
    'power-system-cyber-security-review' and corpus B 'thesis-litrev')
    without mixing their scopes (master plan Phase 2 step 2 merge).
    """
    conn = config.init_db(db_path)
    os.makedirs(kb_dir, exist_ok=True)

    # discover derived_v2 JSONs in derived_dir
    derived_jsons = sorted(
        os.path.join(derived_dir, f)
        for f in os.listdir(derived_dir)
        if f.endswith(".json") and "_v2_" in f
    )

    # identity mapping (one-shot) for all papers being ingested
    paper_ids_in_batch = [derive._paper_id_from_path(p) for p in derived_jsons]
    id_map = {}
    if doi_map_path and metadata_dir:
        id_map = identity.build_identity(doi_map_path, metadata_dir,
                                         paper_ids_in_batch, model_alias)

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
        identity_rec = id_map.get(paper_id, {"paper_id": paper_id})
        paper_meta = _build_paper_meta(identity_rec, paper_id, project=project)

        # remember the stored identity before upserting (page rewrite gate)
        prev = conn.execute(
            "SELECT doi, identity_status FROM papers WHERE paper_id=?",
            (paper_id,)).fetchone()

        # upsert paper row (with DOI + identity_status from the identity layer)
        config.insert_paper(
            conn, paper_meta["paper_id"], paper_meta["title"],
            paper_meta["authors"], paper_meta["year"], paper_meta["project"],
            paper_meta["source_rel"], paper_meta["ocr_version"],
            paper_meta["doi"], paper_meta["identity_status"],
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
        identity_changed = (
            prev is not None and (
                (prev[0] or "") != (paper_meta["doi"] or "")
                or (prev[1] or "") != (paper_meta["identity_status"] or "")
            )
        )
        if (inserted > 0 or not _paper_has_page(kb_dir, paper_id)
                or identity_changed):
            _write_paper_page(kb_dir, paper_id, paper_meta, claims)
        _append_log(kb_dir, paper_id, inserted, tag=("backfill" if backfill else "ingest"))
        paper_count += 1

    _write_catalog(kb_dir)
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


def concept(db_path, kb_dir, slug, query, limit=30):
    """Lazily create a concept page (schema.md §6).

    Trigger: the same concept is hit across >=2 distinct papers by
    `know search`. The page is the layer's only LLM write — this
    function fills the machine-verifiable scaffold (frontmatter with
    verified sources[]), and the querying agent writes the 定义/演进
    prose on top.

    Returns (created, n_papers, path) or (False, n_papers, None):
      - created=True  -> wrote concepts/{slug}.md
      - created=False, n_papers>=... -> the ValueError floated up
    """
    conn = sqlite3.connect(db_path)
    try:
        rows = config.search_claims(conn, query, limit)
    finally:
        conn.close()

    # group matched claims by paper
    by_paper = {}
    for r in rows:
        by_paper.setdefault(r["paper_id"], []).append(r)

    if len(by_paper) < 2:
        raise ValueError(
            f"concept '{slug}' hits {len(by_paper)} paper(s); needs >=2 "
            f"to be a review-level concept (schema.md §6)"
        )

    concepts_dir = os.path.join(kb_dir, "concepts")
    os.makedirs(concepts_dir, exist_ok=True)
    page_path = os.path.join(concepts_dir, f"{slug}.md")

    # sources[] = verified cite:// of matched claims (all survived existence
    # gate at ingest, so every anchor is a real paragraph)
    sources = []
    source_lines = []
    seen = set()
    for paper_id in sorted(by_paper):
        for c in by_paper[paper_id]:
            cite = f"cite://{paper_id}/{c['anchor']}"
            if cite in seen:
                continue
            seen.add(cite)
            sources.append(cite)
            source_lines.append(
                f"- **{c['concept']}**: {c['quote']}  ({cite}, "
                f"{c['heading_path'] or 'no heading'})"
            )

    header = (
        f"---\n"
        f"type: concept\n"
        f"title: {slug}\n"
        f"sources:\n"
    )
    for s in sources:
        header += f"  - \"{s}\"\n"
    header += "created: code\n---\n"

    body = (
        f"\n# {slug}\n\n"
        f"(页面由 agent 在一次查询会话里懒创建 —— 层内唯一 LLM 写入。"
        f"sources 由代码从匹配 claims 生成并核验，agent 不编造来源。)\n\n"
        f"## 定义\n\n"
        f"<!-- agent: 一句话定义 -->\n\n"
        f"## 跨篇演进（{len(by_paper)} 篇）\n\n"
        + "\n".join(source_lines)
        + "\n\n## 相关概念\n\n<!-- [[other-concept]] 仅指向 concepts/ 已有页 -->\n"
    )

    with open(page_path, "w", encoding="utf-8") as f:
        f.write(header + body)
    return True, len(by_paper), page_path


# ---------- CLI dispatch ----------

def _main(argv):
    if len(argv) < 2:
        print("usage: know.py {ingest|validate|search|flag} ...", file=sys.stderr)
        return 2
    cmd = argv[1]
    if cmd == "ingest":
        if len(argv) < 4:
            print("usage: know.py ingest <ocr_dir> <derived_v2_dir> "
                  "[--doi-map PATH] [--metadata-dir PATH] [--model-alias NAME] "
                  "[--project NAME] [--backfill]",
                  file=sys.stderr)
            return 2
        ocr_dir, derived_dir = argv[2], argv[3]
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        kb = os.environ.get("KNOW_KB", "knowledge-base")
        doi_map = os.environ.get("KNOW_DOI_MAP", "")
        metadata_dir = os.environ.get("KNOW_METADATA_DIR", "")
        model_alias = os.environ.get("KNOW_MODEL_ALIAS", "gemini-flash-lite")
        project = os.environ.get("KNOW_PROJECT", "power-system-cyber-security-review")
        backfill = "--backfill" in argv
        # CLI overrides
        if "--doi-map" in argv:
            doi_map = argv[argv.index("--doi-map") + 1]
        if "--metadata-dir" in argv:
            metadata_dir = argv[argv.index("--metadata-dir") + 1]
        if "--model-alias" in argv:
            model_alias = argv[argv.index("--model-alias") + 1]
        if "--project" in argv:
            project = argv[argv.index("--project") + 1]
        n = ingest(ocr_dir, derived_dir, db, kb,
                   doi_map_path=(doi_map or None),
                   metadata_dir=(metadata_dir or None),
                   model_alias=model_alias,
                   project=project,
                   backfill=backfill)
        if backfill:
            print(f"backfill: preserved {n} papers' claims (pre-overwrite "
                  f"snapshot) -> {db}")
        else:
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
    if cmd == "concept":
        if len(argv) < 4:
            print('usage: know.py concept <slug> --query "<concept>"',
                  file=sys.stderr)
            return 2
        db = os.environ.get("KNOW_DB", "knowledge-base/index.sqlite")
        kb = os.environ.get("KNOW_KB", "knowledge-base")
        slug = argv[2]
        if "--query" not in argv:
            print("usage: know.py concept <slug> --query \"<concept>\"",
                  file=sys.stderr)
            return 2
        query = " ".join(argv[argv.index("--query") + 1:]) or "concept"
        try:
            created, n, path = concept(db, kb, slug, query)
        except ValueError as e:
            print(e, file=sys.stderr)
            return 1
        print(f"concept page {'written' if created else 'exists'}: {path} "
              f"({n} papers)")
        return 0
    print(f"unknown command: {cmd}", file=sys.stderr)
    return 2


# avoid name collision with search() at module level for CLI
search_ = search

if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
