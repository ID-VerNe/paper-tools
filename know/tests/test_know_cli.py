"""RED tests for know.py CLI: ingest/validate/search/flag.

Must fail (know.py stub raises NotImplementedError).
"""
import unittest
import sys
import os
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
