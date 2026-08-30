"""RED tests for identity.py — must fail (module missing until GREEN)."""
import unittest
import os
import json
import tempfile

sys_path = os.path.join(os.path.dirname(__file__), "..")
import sys
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from identity import (
    paper_id_from_pdf_name,
    load_doi_map,
    doi_status,
    load_metadata_json,
    resolve_doi,
    build_identity,
)

FMATTER_PDF = "10.1002_9781394215416.pdf"
NC1_PDF = "10.1007_978-3-031-29290-3_4.pdf"
NC2_PDF = "10.1109_pesgm41954.2020.9281941.pdf"
CLEAN_PDF = "10.1109_tii.2020.2999571.pdf"


def _write_doi_map(path):
    data = [
        {"pdf_name": CLEAN_PDF, "title": "FDIA Detection", "doi": "10.1109/TII.2020.2999571",
         "year": "2020", "status": "matched", "verified": True},
        {"pdf_name": FMATTER_PDF, "title": "Autonomous Cyber Resilience",
         "doi": "10.1002/9781394215416.fmatter", "year": "2026",
         "status": "matched", "verified": True},
        {"pdf_name": NC1_PDF, "title": "DSE Protection", "doi": None,
         "year": "2023", "status": "no_confidence"},
        {"pdf_name": NC2_PDF, "title": "Physics-Guided ML OPF", "doi": None,
         "year": "2024", "status": "no_confidence"},
    ]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestPaperId(unittest.TestCase):
    def test_stem_from_pdf_name(self):
        self.assertEqual(paper_id_from_pdf_name(CLEAN_PDF),
                         "10.1109_tii.2020.2999571")

    def test_underscore_doi_path_stays_intact(self):
        # 10.1007_978-3-031-29290-3_4.pdf -> stem keeps the underscores
        self.assertEqual(paper_id_from_pdf_name(NC1_PDF),
                         "10.1007_978-3-031-29290-3_4")


class TestDoiStatus(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "doi_map.json")
            _write_doi_map(p)
            self.dm = load_doi_map(p)

    def test_clean(self):
        self.assertEqual(doi_status(self.dm["10.1109_tii.2020.2999571"]), "clean")

    def test_fmatter_trap(self):
        self.assertEqual(doi_status(self.dm["10.1002_9781394215416"]), "fmatter_trap")

    def test_unresolved(self):
        for pid in ("10.1007_978-3-031-29290-3_4", "10.1109_pesgm41954.2020.9281941"):
            self.assertEqual(doi_status(self.dm[pid]), "unresolved")


class TestResolveDoi(unittest.TestCase):
    def setUp(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "doi_map.json")
            _write_doi_map(p)
            self.dm = load_doi_map(p)

    def test_clean_doi_wins(self):
        entry = self.dm["10.1109_tii.2020.2999571"]
        doi, st = resolve_doi(entry, "")
        self.assertEqual(doi, "10.1109/TII.2020.2999571")
        self.assertEqual(st, "clean")

    def test_fmatter_never_emitted_as_doi(self):
        entry = self.dm["10.1002_9781394215416"]
        # metadata DOI is the same fmatter -> no real DOI anywhere
        doi, st = resolve_doi(entry, "10.1002/9781394215416.fmatter")
        self.assertEqual(doi, "")
        self.assertEqual(st, "fmatter_trap")
        self.assertNotIn(".fmatter", doi)

    def test_unresolved_no_metadata_doi(self):
        entry = self.dm["10.1007_978-3-031-29290-3_4"]
        doi, st = resolve_doi(entry, "not reported")
        self.assertEqual(doi, "")
        self.assertEqual(st, "unresolved")

    def test_unresolved_with_real_metadata_doi(self):
        entry = self.dm["10.1007_978-3-031-29290-3_4"]
        doi, st = resolve_doi(entry, "10.1109/TPWRD.2023.1234567")
        self.assertEqual(doi, "10.1109/TPWRD.2023.1234567")
        self.assertEqual(st, "unresolved_meta")  # recorded, not authoritative


class TestLoadMetadata(unittest.TestCase):
    def test_parses_citation_real_values(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"citation": {"title": "A Real Title",
                                        "authors": ["A", "B"], "year": 2020,
                                        "doi": "10.1/x", "journal": "IEEE TII"}}, f)
            m = load_metadata_json(p)
            self.assertEqual(m["title"], "A Real Title")
            self.assertEqual(m["authors"], ["A", "B"])
            self.assertEqual(m["year"], "2020")

    def test_not_reported_cleaned_to_empty(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "m.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump({"citation": {"title": "X", "doi": "not reported",
                                        "authors": "not reported", "year": "not reported"}}, f)
            m = load_metadata_json(p)
            self.assertEqual(m["doi"], "")
            self.assertEqual(m["authors"], "")
            self.assertEqual(m["year"], "")


class TestBuildIdentity(unittest.TestCase):
    def test_full_identity_shape(self):
        with tempfile.TemporaryDirectory() as td:
            dm = os.path.join(td, "doi_map.json")
            _write_doi_map(dm)
            meta_dir = os.path.join(td, "meta")
            os.makedirs(meta_dir)
            # one metadata JSON for the clean paper
            with open(os.path.join(meta_dir, f"{paper_id_from_pdf_name(CLEAN_PDF)}_metadata_gemini-flash-lite.json"),
                      "w", encoding="utf-8") as f:
                json.dump({"citation": {"title": "FDIA Detection",
                                        "authors": ["Jorjani"], "year": 2020,
                                        "doi": "10.1109/TII.2020.2999571"}}, f)
            ids = build_identity(dm, meta_dir, ["10.1109_tii.2020.2999571"])
            rec = ids["10.1109_tii.2020.2999571"]
            self.assertEqual(rec["doi"], "10.1109/TII.2020.2999571")
            self.assertEqual(rec["status"], "clean")
            self.assertEqual(rec["title"], "FDIA Detection")
            self.assertEqual(rec["authors"], ["Jorjani"])
            self.assertEqual(rec["year"], "2020")

    def test_fmatter_and_unresolved_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            dm = os.path.join(td, "doi_map.json")
            _write_doi_map(dm)
            meta_dir = os.path.join(td, "meta")
            os.makedirs(meta_dir)
            ids = build_identity(
                dm, meta_dir,
                ["10.1002_9781394215416", "10.1007_978-3-031-29290-3_4",
                 "10.1109_pesgm41954.2020.9281941"],
            )
            self.assertEqual(ids["10.1002_9781394215416"]["status"], "fmatter_trap")
            self.assertEqual(ids["10.1002_9781394215416"]["doi"], "")
            self.assertEqual(ids["10.1007_978-3-031-29290-3_4"]["status"], "unresolved")
            self.assertEqual(ids["10.1109_pesgm41954.2020.9281941"]["status"], "unresolved")
            # unresolved entries with no metadata JSON fall back to doi_map's title
            self.assertEqual(ids["10.1007_978-3-031-29290-3_4"]["title"], "DSE Protection")


if __name__ == "__main__":
    unittest.main()
