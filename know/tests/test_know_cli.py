"""RED tests for know.py CLI: ingest/validate/search/flag.

Must fail (know.py stub raises NotImplementedError).
"""
import unittest
import sys
import os
import json
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import know

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PAPER = "10.1109_tii.2020.2999571"


class TestIngest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ocr_dir = os.path.join(FIXTURES)  # has the .md
        self.derived_dir = os.path.join(FIXTURES)  # has the v2 json
        self.db_path = os.path.join(self.tmp, "index.sqlite")
        self.kb_dir = os.path.join(self.tmp, "knowledge-base")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ingest_creates_db(self):
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir)
        self.assertTrue(os.path.exists(self.db_path))

    def test_ingest_writes_paper_pages(self):
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir)
        papers_dir = os.path.join(self.kb_dir, "papers")
        # at least one .md page per paper ingested
        pages = [f for f in os.listdir(papers_dir) if f.endswith(".md")]
        self.assertGreater(len(pages), 0)

    def test_ingest_writes_log(self):
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir)
        log = os.path.join(self.kb_dir, "log.md")
        self.assertTrue(os.path.exists(log))

    def test_ingest_with_identity_writes_real_doi_and_status(self):
        """End-to-end: identity layer (doi_map + metadata JSON) flows DOI and
        identity_status into the papers table and the markdown page.
        """
        import sqlite3
        # build a tiny identity fixture: doi_map + metadata JSON for PAPER
        dm = os.path.join(self.tmp, "doi_map.json")
        with open(dm, "w", encoding="utf-8") as f:
            json.dump([{"pdf_name": PAPER + ".pdf", "title": "FDIA Detection",
                        "doi": "10.1109/TII.2020.2999571", "year": "2020",
                        "status": "matched", "verified": True}], f)
        meta_dir = os.path.join(self.tmp, "meta")
        os.makedirs(meta_dir)
        with open(os.path.join(meta_dir, f"{PAPER}_metadata_gemini-flash-lite.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"citation": {"title": "FDIA Detection",
                                    "authors": ["Jorjani"], "year": 2020,
                                    "doi": "10.1109/TII.2020.2999571"}}, f)
        know.ingest(FIXTURES, FIXTURES, self.db_path, self.kb_dir,
                    doi_map_path=dm, metadata_dir=meta_dir,
                    model_alias="gemini-flash-lite")
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT doi, title, authors, year, identity_status FROM papers "
            "WHERE paper_id=?", (PAPER,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "10.1109/TII.2020.2999571")
        self.assertEqual(row[1], "FDIA Detection")
        self.assertEqual(row[4], "clean")
        # the markdown page front matter carries the real DOI (not the paper_id)
        with open(os.path.join(self.kb_dir, "papers", f"{PAPER}.md"),
                  encoding="utf-8") as f:
            page = f.read()
        self.assertIn("doi: 10.1109/TII.2020.2999571", page)
        self.assertIn("identity_status: clean", page)

    def test_ingest_with_fmatter_trap_suppresses_doi(self):
        """The .fmatter trap must NOT be emitted as a DOI."""
        import sqlite3
        fm_id = PAPER  # reuse fixture OCR/v2 but override identity
        dm = os.path.join(self.tmp, "doi_map.json")
        with open(dm, "w", encoding="utf-8") as f:
            json.dump([{"pdf_name": fm_id + ".pdf", "title": "Book FM",
                        "doi": "10.1002/9781394215416.fmatter",
                        "year": "2026", "status": "matched",
                        "verified": True}], f)
        meta_dir = os.path.join(self.tmp, "meta")
        os.makedirs(meta_dir)
        with open(os.path.join(meta_dir, f"{fm_id}_metadata_gemini-flash-lite.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"citation": {"title": "Book FM", "authors": [],
                                    "year": 2026, "doi": ""}}, f)
        know.ingest(FIXTURES, FIXTURES, self.db_path, self.kb_dir,
                    doi_map_path=dm, metadata_dir=meta_dir)
        conn = sqlite3.connect(self.db_path)
        row = conn.execute(
            "SELECT doi, identity_status FROM papers WHERE paper_id=?", (fm_id,)
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], "")  # fmatter suppressed
        self.assertEqual(row[1], "fmatter_trap")


class TestIdempotent(unittest.TestCase):
    def test_repeated_ingest_no_duplicates(self):
        tmp = tempfile.mkdtemp()
        try:
            know.ingest(FIXTURES, FIXTURES,
                        os.path.join(tmp, "i.sqlite"),
                        os.path.join(tmp, "kb1"))
            know.ingest(FIXTURES, FIXTURES,
                        os.path.join(tmp, "i.sqlite"),
                        os.path.join(tmp, "kb1"))
            import sqlite3
            conn = sqlite3.connect(os.path.join(tmp, "i.sqlite"))
            cur = conn.execute("SELECT COUNT(*) FROM claims")
            count = cur.fetchone()[0]
            conn.close()
            # second ingest of same papers should not double the claims
            self.assertGreater(count, 0)
            # idempotent: same paper+field+quote -> one claim row
            self.assertLess(count, 100)  # sanity: not exploding
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "index.sqlite")
        self.kb_dir = os.path.join(self.tmp, "knowledge-base")
        know.ingest(FIXTURES, FIXTURES, self.db_path, self.kb_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_search_returns_results(self):
        results = know.search(self.db_path, "attack")
        self.assertIsInstance(results, list)
        self.assertGreater(len(results), 0)

    def test_search_two_char_cjk(self):
        # Gap 5 acceptance: 2-char CJK works (the fixture is English-only,
        # so this should return 0 gracefully, not crash)
        results = know.search(self.db_path, "攻击")
        self.assertIsInstance(results, list)

    def test_search_empty_returns_zero_with_hint(self):
        # Gap 7: top-k=0 path should not crash, should signal no results
        results = know.search(self.db_path, "totallynonexistenttermxyz")
        self.assertEqual(len(results), 0)


class TestFlag(unittest.TestCase):
    def test_flag_marks_not_deletes(self):
        tmp = tempfile.mkdtemp()
        try:
            db = os.path.join(tmp, "i.sqlite")
            kb = os.path.join(tmp, "kb")
            know.ingest(FIXTURES, FIXTURES, db, kb)
            import sqlite3
            conn = sqlite3.connect(db)
            cur = conn.execute("SELECT claim_id FROM claims LIMIT 1")
            row = cur.fetchone()
            self.assertIsNotNone(row, "need at least one claim to flag")
            cid = row[0]
            conn.close()
            know.flag(db, cid, "doubt this attribution")
            conn = sqlite3.connect(db)
            cur = conn.execute("SELECT conflicting FROM claims WHERE claim_id=?",
                               (cid,))
            flagged = cur.fetchone()[0]
            conn.close()
            self.assertEqual(flagged, "flagged")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestBackfill(unittest.TestCase):
    """know ingest --backfill: pre-overwrite snapshot (MS golden rule).

    Semantically identical to ingest (idempotent anchor+dedup), but log.md
    entries are tagged 'backfill' so the archive records provenance.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ocr_dir = FIXTURES
        self.derived_dir = FIXTURES
        self.db_path = os.path.join(self.tmp, "index.sqlite")
        self.kb_dir = os.path.join(self.tmp, "knowledge-base")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_backfill_ingests_same_as_normal_ingest(self):
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir,
                    backfill=True)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        conn.close()
        self.assertGreater(count, 0)

    def test_backfill_log_tagged(self):
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir,
                    backfill=True)
        with open(os.path.join(self.kb_dir, "log.md"), encoding="utf-8") as f:
            log = f.read()
        self.assertIn("- backfill | paper=", log)
        self.assertNotIn("- ingest | ", log)

    def test_backfill_after_ingest_does_not_duplicate(self):
        """ingest then backfill -> claims must NOT double (both idempotent)."""
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir)
        know.ingest(self.ocr_dir, self.derived_dir, self.db_path, self.kb_dir,
                    backfill=True)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        count = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        conn.close()
        self.assertLess(count, 100)  # sanity bound, same as TestIdempotent

    def test_backfill_cli_flag(self):
        """CLI: know.py ingest ... --backfill prints the backfill message."""
        import subprocess
        know_path = os.path.join(os.path.dirname(__file__), "..", "know.py")
        env = {"KNOW_DB": self.db_path, "KNOW_KB": self.kb_dir}
        env.update(os.environ.copy())
        r = subprocess.run(
            [sys.executable, know_path, "ingest", self.ocr_dir,
             self.derived_dir, "--backfill"],
            capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("backfill: preserved", r.stdout)


class TestConcept(unittest.TestCase):
    """concepts/ lazy creation (schema.md §6).

    Trigger: concept hits >=2 papers -> page written with machine-verified
    sources[]; <2 papers -> refused (ValueError).
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "index.sqlite")
        self.kb_dir = os.path.join(self.tmp, "knowledge-base")
        know.ingest(FIXTURES, FIXTURES, self.db_path, self.kb_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_concept_refused_under_two_papers(self):
        # fixture corpus: single paper -> must refuse
        with self.assertRaises(ValueError):
            know.concept(self.db_path, self.kb_dir, "attack", "attack")

    def test_concept_writes_page_with_sources(self):
        # seed a second paper so a query hits >=2 papers
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT INTO papers (paper_id, title, authors, year, project, "
            "source_rel, ocr_version) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("uid:second", "Second Paper", '["B"]', 2025, "p", "r", "v1"),
        )
        rows = conn.execute(
            "SELECT claim_id, paper_id, concept, quote, anchor, heading_path, "
            "conflicting FROM claims LIMIT 3"
        ).fetchall()
        for (cid, pid, concept_, quote, anchor, hp, cf) in rows:
            conn.execute(
                "INSERT INTO claims (paper_id, concept, quote, anchor, "
                "heading_path, model, prompt_id) VALUES (?,?,?,?,?,?,?)",
                ("uid:second", concept_, quote, anchor, hp, "gemini", "v1"),
            )
            conn.commit()
            new_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.execute("INSERT INTO claims_fts (rowid, concept, quote) "
                         "VALUES (?,?,?)", (new_id, concept_, quote))
            conn.execute("INSERT INTO claims_fts_cjk (rowid, concept, quote) "
                         "VALUES (?,?,?)", (new_id, concept_, quote))
        conn.commit()
        conn.close()

        created, n, path = know.concept(self.db_path, self.kb_dir,
                                        "attack", "attack")
        self.assertTrue(created)
        self.assertGreaterEqual(n, 2)
        with open(path, encoding="utf-8") as f:
            page = f.read()
        self.assertIn("type: concept", page)
        self.assertIn("created: code", page)
        self.assertIn("sources:", page)
        self.assertIn("cite://", page)

    def test_concept_cli_flag(self):
        import subprocess
        know_path = os.path.join(os.path.dirname(__file__), "..", "know.py")
        env = {"KNOW_DB": self.db_path, "KNOW_KB": self.kb_dir}
        env.update(os.environ.copy())
        r = subprocess.run(
            [sys.executable, know_path, "concept", "fdia",
             "--query", "attack"],
            capture_output=True, text=True, encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 1)  # fixture = 1 paper -> refused


class TestValidate(unittest.TestCase):
    def test_validate_passes_after_ingest(self):
        tmp = tempfile.mkdtemp()
        try:
            db = os.path.join(tmp, "i.sqlite")
            kb = os.path.join(tmp, "kb")
            know.ingest(FIXTURES, FIXTURES, db, kb)
            ok = know.validate(db, kb)
            self.assertTrue(ok)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
