"""config.py — schema definitions, constants, DB init, search.

SQLite is the query substrate (peer-review F2, scalability-engineer's
core decision). Markdown is the archive layer — never the query path.

peer-review Gap 5 fix: FTS5 trigram tokenizer returns 0 rows for 2-char
CJK queries (verified: MATCH '交易' on trigram and unicode61 both return 0).
This module ships a LIKE fallback for short CJK queries so the user's
canonical query 'know search 交易' returns rows.
"""
import sqlite3

MATCHED = "matched"
BROKEN = "broken"
UNMATCHED = "unmatched"


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS papers (
  paper_id        TEXT PRIMARY KEY,
  title           TEXT NOT NULL,
  authors         TEXT NOT NULL,
  year            INTEGER,
  project         TEXT NOT NULL,
  source_rel      TEXT,
  ocr_version     TEXT,
  doi             TEXT,
  identity_status TEXT
);

CREATE TABLE IF NOT EXISTS claims (
  claim_id    INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id    TEXT NOT NULL REFERENCES papers(paper_id),
  concept     TEXT NOT NULL,
  quote       TEXT NOT NULL,
  anchor      TEXT NOT NULL,
  heading_path TEXT NOT NULL,
  model       TEXT NOT NULL,
  prompt_id   TEXT NOT NULL,
  conflicting TEXT,
  rank        INTEGER,
  UNIQUE(paper_id, field_placeholder) -- placeholder, replaced below
);

-- English token search
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts USING fts5(
  concept, quote, content_rowid='rowid', tokenize='unicode61'
);

-- CJK substring search (trigram; works for >=3 char terms)
CREATE VIRTUAL TABLE IF NOT EXISTS claims_fts_cjk USING fts5(
  concept, quote, content_rowid='rowid', tokenize='trigram'
);
"""


def _clean_schema():
    """Return schema with the placeholder UNIQUE removed (we use a
    content-less claim_id uniqueness instead)."""
    return SCHEMA_SQL.replace(
        ",\n  UNIQUE(paper_id, field_placeholder) -- placeholder, replaced below",
        "",
    )


def init_db(db_path):
    """Create the SQLite DB with all tables if not present. Return conn."""
    conn = sqlite3.connect(db_path)
    conn.executescript(_clean_schema())
    conn.commit()
    return conn


def insert_paper(conn, paper_id, title, authors, year, project, source_rel,
                 ocr_version, doi="", identity_status="clean"):
    """Insert a paper row; on conflict, UPSERT the identity record.

    Better identity evidence can arrive later (corpus B: crossref upgraded
    11 hyphen-family DOIs from unresolved_meta to clean). Re-ingest must
    reflect the new status instead of DO NOTHING. Claims are deduped
    separately in know.ingest (by paper_id+concept+quote), so the UPSERT
    never loses evidence.
    """
    conn.execute(
        "INSERT INTO papers (paper_id, title, authors, year, project, "
        "source_rel, ocr_version, doi, identity_status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(paper_id) DO UPDATE SET "
        "title=excluded.title, authors=excluded.authors, year=excluded.year, "
        "project=excluded.project, source_rel=excluded.source_rel, "
        "ocr_version=excluded.ocr_version, doi=excluded.doi, "
        "identity_status=excluded.identity_status",
        (paper_id, title, authors, year, project, source_rel,
         ocr_version, doi, identity_status),
    )


def insert_claim(conn, paper_id, concept, quote, anchor, heading_path,
                 model, prompt_id):
    """Insert a claim row and update both FTS tables.

    Idempotency: caller (know.ingest) dedups before calling; this is the
    atomic insert. Returns the claim_id.
    """
    cur = conn.execute(
        "INSERT INTO claims (paper_id, concept, quote, anchor, heading_path, "
        "model, prompt_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (paper_id, concept, quote, anchor, heading_path, model, prompt_id),
    )
    rowid = cur.lastrowid
    conn.execute("INSERT INTO claims_fts (rowid, concept, quote) VALUES (?, ?, ?)",
                 (rowid, concept, quote))
    conn.execute("INSERT INTO claims_fts_cjk (rowid, concept, quote) VALUES (?, ?, ?)",
                 (rowid, concept, quote))
    return rowid


def search_claims(conn, concept, limit=10):
    """Search claims by concept/quote.

    Strategy (Gap 5 fix):
    - For terms >= 3 chars: use FTS5 (claims_fts unicode61 for English,
      claims_fts_cjk trigram for CJK substrings >= 3 chars).
    - For 2-char CJK terms (where both FTS tables return 0): fall back to
      LIKE '%term%' against concept + quote.
    - Merge + dedup by claim_id, order by FTS rank when available.

    Returns list of dicts: {claim_id, paper_id, concept, quote, anchor,
    heading_path, conflicting}.
    """
    results = {}
    term = concept

    # FTS path for longer terms
    try:
        rows = conn.execute(
            "SELECT c.claim_id, c.paper_id, c.concept, c.quote, c.anchor, "
            "c.heading_path, c.conflicting "
            "FROM claims_fts f JOIN claims c ON f.rowid = c.claim_id "
            "WHERE claims_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (term, limit),
        ).fetchall()
        for r in rows:
            results[r[0]] = _row_to_dict(r)
    except sqlite3.OperationalError:
        pass  # MATCH syntax error on weird terms -> fall to LIKE

    try:
        rows = conn.execute(
            "SELECT c.claim_id, c.paper_id, c.concept, c.quote, c.anchor, "
            "c.heading_path, c.conflicting "
            "FROM claims_fts_cjk f JOIN claims c ON f.rowid = c.claim_id "
            "WHERE claims_fts_cjk MATCH ? "
            "ORDER BY rank LIMIT ?",
            (term, limit),
        ).fetchall()
        for r in rows:
            if r[0] not in results:
                results[r[0]] = _row_to_dict(r)
    except sqlite3.OperationalError:
        pass

    # LIKE fallback: catches 2-char CJK and any term FTS missed
    like_term = f"%{term}%"
    rows = conn.execute(
        "SELECT claim_id, paper_id, concept, quote, anchor, heading_path, "
        "conflicting FROM claims "
        "WHERE concept LIKE ? OR quote LIKE ? LIMIT ?",
        (like_term, like_term, limit),
    ).fetchall()
    for r in rows:
        if r[0] not in results:
            results[r[0]] = {
                "claim_id": r[0], "paper_id": r[1], "concept": r[2],
                "quote": r[3], "anchor": r[4], "heading_path": r[5],
                "conflicting": r[6],
            }

    return list(results.values())[:limit]


def _row_to_dict(r):
    return {
        "claim_id": r[0], "paper_id": r[1], "concept": r[2], "quote": r[3],
        "anchor": r[4], "heading_path": r[5], "conflicting": r[6],
    }
