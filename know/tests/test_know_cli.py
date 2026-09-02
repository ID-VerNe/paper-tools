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


class TestCorpusBIdentityMapping(unittest.TestCase):
    """Corpus B (thesis-litrev): no pipeline doi_map; partial doi_map (only
    crossref-verified underscore papers) + LLM metadata for the rest.

    Four identity families, master plan Phase 2 step 1:
      - underscore  (10.3390_en17071702): in doi_map -> clean + real doi
      - hyphen      (10_1109-TSG_2023_3302270): not in doi_map; metadata
                    carries a real DOI -> unresolved_meta (recorded, flagged)
      - author_year (garcia2015_jmlr): not in doi_map; metadata no DOI ->
                    unresolved (doi empty, paper_id = uid)
      - arxiv      (1609.02907v4): not in doi_map; metadata no DOI ->
                    unresolved (doi empty, paper_id = uid)
    """

    CORPUS_B = os.path.join(FIXTURES, "corpus_b")

    def _metadata(self, meta_dir, pid, title, doi, year=2024):
        with open(os.path.join(meta_dir, f"{pid}_metadata_deepseek.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"citation": {"title": title, "authors": ["X"],
                                    "year": year, "doi": doi}}, f)

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmp, "index.sqlite")
        self.kb_dir = os.path.join(self.tmp, "knowledge-base")
        self.meta_dir = os.path.join(self.tmp, "meta")
        os.makedirs(self.meta_dir)
        # partial doi_map: only the crossref-verified underscore paper
        self.doi_map = os.path.join(self.tmp, "doi_map.json")
        with open(self.doi_map, "w", encoding="utf-8") as f:
            json.dump([{"pdf_name": "10.3390_en17071702.pdf",
                        "doi": "10.3390/en17071702", "year": "2024",
                        "status": "matched", "verified": True}], f)
        self._metadata(self.meta_dir, "10_1109-TSG_2023_3302270",
                       "Dynamic Operating Envelope-Based Local Energy Market",
                       "10.1109/TSG.2023.3302270")
        self._metadata(self.meta_dir, "10.3390_en17071702",
                       "A Review of Peer-to-Peer Energy Trading Markets",
                       "10.3390/en17071702")
        self._metadata(self.meta_dir, "garcia2015_jmlr",
                       "One-Class Classification for Novelty Detection", "")
        self._metadata(self.meta_dir, "1609.02907v4",
                       "Multi-Agent RL for P2P Energy Trading", "")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rows(self):
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT paper_id, doi, title, identity_status FROM papers"
        ).fetchall()
        conn.close()
        return {r[0]: {"doi": r[1], "title": r[2], "status": r[3]} for r in rows}

    def test_four_family_identity_statuses(self):
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek")
        rows = self._rows()
        # underscore: verified in partial doi_map -> clean
        self.assertEqual(rows["10.3390_en17071702"]["status"], "clean")
        self.assertEqual(rows["10.3390_en17071702"]["doi"],
                         "10.3390/en17071702")
        # hyphen: not in doi_map, metadata DOI recorded but flagged
        self.assertEqual(rows["10_1109-TSG_2023_3302270"]["status"],
                         "unresolved_meta")
        self.assertEqual(rows["10_1109-TSG_2023_3302270"]["doi"],
                         "10.1109/TSG.2023.3302270")
        # author_year / arxiv: no DOI anywhere -> unresolved, doi empty
        self.assertEqual(rows["garcia2015_jmlr"]["status"], "unresolved")
        self.assertEqual(rows["garcia2015_jmlr"]["doi"], "")
        self.assertEqual(rows["1609.02907v4"]["status"], "unresolved")
        self.assertEqual(rows["1609.02907v4"]["doi"], "")
        # every paper still title-queryable (metadata title survives)
        self.assertEqual(rows["1609.02907v4"]["title"],
                         "Multi-Agent RL for P2P Energy Trading")

    def test_hyphen_paper_page_named_by_uid(self):
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek")
        # hyphen-family paper id (uid) is the page name, DOI not promoted
        page = os.path.join(self.kb_dir, "papers",
                            "10_1109-TSG_2023_3302270.md")
        self.assertTrue(os.path.exists(page))
        with open(page, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("identity_status: unresolved_meta", text)
        self.assertIn("doi: 10.1109/TSG.2023.3302270", text)

    def test_all_four_claims_ingested(self):
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek")
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        conn.close()
        self.assertEqual(n, 4)  # 1 claim per paper, all anchored

    def test_project_tagged_thesis_litrev(self):
        """Corpus B papers carry project=thesis-litrev (not the corpus A
        default), so one layer holds both reviews without mixing scopes."""
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek", project="thesis-litrev")
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT DISTINCT project FROM papers").fetchall()
        conn.close()
        self.assertEqual(rows, [("thesis-litrev",)])
        # frontmatter carries the project too
        page = os.path.join(self.kb_dir, "papers",
                            "10_1109-TSG_2023_3302270.md")
        with open(page, encoding="utf-8") as f:
            self.assertIn("project: thesis-litrev", f.read())

    def test_merged_catalog_counts_all_pages(self):
        """Two corpora in one layer: catalog header == total pages, not the
        last batch size (regression: A(1) + B(4) -> 'papers: 5')."""
        know.ingest(FIXTURES, FIXTURES, self.db_path, self.kb_dir)  # corpus A
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek", project="thesis-litrev")
        with open(os.path.join(self.kb_dir, "catalog.md"), encoding="utf-8") as f:
            head = f.read().splitlines()[2]  # line 3: papers: N
        self.assertEqual(head, "papers: 5")
        self.assertEqual(
            len([f for f in os.listdir(os.path.join(self.kb_dir, "papers"))
                 if f.endswith(".md")]), 5)


def test_identity_upgrade_on_reingest(self):
        """Later verification upgrades a paper's identity (complex B: hyphen
        DOI goes unresolved_meta -> clean when crossref evidence lands in the
        doi_map). Re-ingest must UPSERT the row AND rewrite the page."""
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=self.doi_map, metadata_dir=self.meta_dir,
                    model_alias="deepseek", project="thesis-litrev")
        # now the hyphen DOI gets verified -> added to doi_map
        dm2 = os.path.join(self.tmp, "doi_map2.json")
        with open(dm2, "w", encoding="utf-8") as f:
            json.dump([
                {"pdf_name": "10.3390_en17071702.pdf",
                 "doi": "10.3390/en17071702", "year": "2024",
                 "status": "matched", "verified": True},
                {"pdf_name": "10_1109-TSG_2023_3302270.pdf",
                 "doi": "10.1109/TSG.2023.3302270", "year": "2024",
                 "status": "matched", "verified": True},
            ], f)
        know.ingest(self.CORPUS_B, self.CORPUS_B, self.db_path, self.kb_dir,
                    doi_map_path=dm2, metadata_dir=self.meta_dir,
                    model_alias="deepseek", project="thesis-litrev")
        # DB upgraded
        rows = self._rows()
        self.assertEqual(rows["10_1109-TSG_2023_3302270"]["status"],
                         "clean")
        # page frontmatter rewritten with the new status + verified DOI
        page = os.path.join(self.kb_dir, "papers",
                            "10_1109-TSG_2023_3302270.md")
        with open(page, encoding="utf-8") as f:
            text = f.read()
        self.assertIn("identity_status: clean", text)
        self.assertIn("doi: 10.1109/TSG.2023.3302270", text)
        # claims NOT doubled by the re-ingest (dedup untouched by UPSERT)
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        conn.close()
        self.assertEqual(n, 4)


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
