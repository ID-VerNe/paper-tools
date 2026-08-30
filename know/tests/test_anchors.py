"""RED tests for anchors.py — must fail (anchors.py stub raises NotImplementedError)."""
import unittest
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from anchors import (
    paragraphize,
    anchor_id,
    rematch,
    classify_fragmented,
    coerce_quote,
    MATCHED,
    BROKEN,
    UNMATCHED,
)


class TestCoerceQuote(unittest.TestCase):
    def test_none_stays_none(self):
        self.assertIsNone(coerce_quote(None))

    def test_string_passes_through(self):
        self.assertEqual(coerce_quote("verbatim"), "verbatim")

    def test_list_joined_with_ellipsis(self):
        # a multi-valued field becomes a fragmented quote; all fragments must
        # anchor, so one bad item drops the claim (no fabrication)
        joined = coerce_quote(["first fragment", "second fragment"])
        self.assertIn("...", joined)
        self.assertIn("first fragment", joined)
        self.assertIn("second fragment", joined)

    def test_empty_list_becomes_none(self):
        self.assertIsNone(coerce_quote([]))
        self.assertIsNone(coerce_quote(["", None]))

    def test_non_string_scalar_str(self):
        self.assertEqual(coerce_quote(42), "42")
        self.assertEqual(coerce_quote(True), "True")

    def test_empty_string_becomes_none(self):
        self.assertIsNone(coerce_quote(""))
        self.assertIsNone(coerce_quote("   "))


class TestParagraphize(unittest.TestCase):
    def test_skips_blank_lines(self):
        text = "para one\n\n\npara two"
        result = paragraphize(text)
        self.assertEqual(len(result), 2)

    def test_skips_headings(self):
        text = "# Title\n## II. INTRODUCTION\nbody line\n### A. Sub\nmore body"
        result = paragraphize(text)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].startswith("body line"))

    def test_normalizes_whitespace_unicode(self):
        text = "hello   world\tfoo"
        result = paragraphize(text)
        self.assertEqual(result[0], "hello world foo")

    def test_strips_carriage_returns(self):
        text = "line one\r\nline two\r"
        result = paragraphize(text)
        # \r stripped, \n splits — two paragraphs
        self.assertEqual(len(result), 2)
        self.assertNotIn("\r", result[0])


class TestAnchorId(unittest.TestCase):
    def test_format_prefix_sha1_8(self):
        aid = anchor_id("some paragraph text")
        self.assertTrue(aid.startswith("p"))
        # sha1 hex 8 chars after the prefix
        self.assertEqual(len(aid), 9)  # 'p' + 8

    def test_deterministic_same_input(self):
        a = anchor_id("identical text")
        b = anchor_id("identical text")
        self.assertEqual(a, b)

    def test_different_input_different_id(self):
        a = anchor_id("first paragraph")
        b = anchor_id("second paragraph")
        self.assertNotEqual(a, b)

    def test_whitespace_normalized_before_hash(self):
        # leading/trailing/internal whitespace should not change the id
        a = anchor_id("hello world")
        b = anchor_id("  hello   world  ")
        self.assertEqual(a, b)

    def test_unicode_nfk_normalized(self):
        # NFKC folds compatibility chars; fullwidth 'A' (U+FF21) -> 'A'
        a = anchor_id("ABC")
        b = anchor_id("ＡＢＣ")
        self.assertEqual(a, b)


class TestRematch(unittest.TestCase):
    def test_exact_substring_is_matched(self):
        paras = ["the attacker injects false data into the grid"]
        result = rematch("attacker injects false data", paras)
        self.assertEqual(result[0], MATCHED)
        self.assertAlmostEqual(result[1], 1.0)

    def test_no_overlap_is_unmatched(self):
        paras = ["completely different text about apples"]
        result = rematch("the attacker injects false data", paras)
        self.assertEqual(result[0], UNMATCHED)

    def test_high_ratio_is_matched(self):
        paras = ["the attacker injects false data into the grid measurements now"]
        result = rematch("attacker injects false data into the grid measurements", paras)
        self.assertEqual(result[0], MATCHED)

    def test_medium_ratio_is_broken(self):
        # ratio between 0.60 and 0.85 -> BROKEN
        paras = ["the attacker injects false data into the grid measurements now"]
        result = rematch("zzz unrelated but somewhat similar xyz", paras)
        if 0.60 <= result[1] < 0.85:
            self.assertEqual(result[0], BROKEN)


class TestRematchThresholds(unittest.TestCase):
    """Pin the 0.60/0.85 boundaries so a threshold mutant is killed.

    These are the anchor-contract thresholds from Phase 0.5; a mutant
    that moves either boundary (e.g. 0.85->0.50) must be caught.
    """
    def test_just_above_matched_threshold_is_matched(self):
        # a quote with ratio just above 0.85 against the paragraph
        paras = ["the attacker injects false data into the grid measurements now today"]
        # construct a quote that yields ~0.86 ratio
        quote = "attacker injects false data into the grid measurements now"
        st, r = rematch(quote, paras)
        self.assertEqual(st, MATCHED, f"ratio {r:.2f} should be MATCHED")

    def test_just_below_matched_threshold_is_broken(self):
        # a quote whose best ratio lands between 0.60 and 0.85 -> BROKEN
        paras = ["the attacker injects false data into the grid measurements now today"]
        # a quote with partial overlap but enough divergence to land in 0.60-0.85
        quote = "attacker injects completely different data into unrelated measurements today"
        st, r = rematch(quote, paras)
        if 0.60 <= r < 0.85:
            self.assertEqual(st, BROKEN, f"ratio {r:.2f} should be BROKEN")
        elif r < 0.60:
            self.assertEqual(st, UNMATCHED)
        else:
            self.fail(f"test fixture landed at {r:.2f}, tune it into 0.60-0.85 band")

    def test_low_ratio_is_unmatched(self):
        paras = ["the attacker injects false data into the grid measurements"]
        quote = "zzzz totally unrelated gibberish content xyz abc"
        st, r = rematch(quote, paras)
        self.assertEqual(st, UNMATCHED)
        self.assertLess(r, 0.60)

    def test_mid_offset_window_match_requires_fine_step(self):
        """A high-ratio quote aligned at a mid-paragraph offset (not a
        multiple of nq) must still match. This kills a coarse-step mutant
        (nq//4 -> nq) which would skip the aligned window.
        """
        # para: 35-char prefix + target + suffix; quote = target with tail typo
        # so it's NOT an exact substring and the best window is at offset 35.
        prefix = "x" * 35
        target = "the quick brown fox jumps over the lazy dog now"
        suffix = "  y y y y y y y y y y"
        para = prefix + target + suffix
        quote = target[:-4] + "XXXX"  # tail typo -> not exact substring
        st, r = rematch(quote, [para])
        self.assertEqual(st, MATCHED, f"mid-offset quote ratio {r:.2f} should be MATCHED")


class TestClassifyFragmented(unittest.TestCase):
    def test_single_fragment_delegates_to_rematch(self):
        paras = ["the attacker injects false data into the grid"]
        result = classify_fragmented("attacker injects false data", paras)
        self.assertEqual(result[0], MATCHED)

    def test_multiple_fragments_all_match(self):
        paras = [
            "the SE serves as the main interface of EMS with the power grid",
            "state estimator is vulnerable to cyber attack",
        ]
        quote = "the SE serves as the main interface of EMS with the power grid... state estimator is vulnerable"
        result = classify_fragmented(quote, paras)
        self.assertEqual(result[0], MATCHED)

    def test_worst_fragment_binds(self):
        # one fragment matches, one does not -> worst = unmatched
        paras = ["the SE serves as the main interface of EMS with the power grid"]
        quote = "the SE serves as the main interface of EMS with the power grid... totally nonexistent gibberish here"
        result = classify_fragmented(quote, paras)
        self.assertNotEqual(result[0], MATCHED)

    def test_empty_quote_is_unmatched(self):
        paras = ["some text"]
        result = classify_fragmented("", paras)
        self.assertEqual(result[0], UNMATCHED)


if __name__ == "__main__":
    unittest.main()
