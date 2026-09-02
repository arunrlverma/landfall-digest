"""Offline tests for the --edition path of build_digest.py (no network, no key)."""
import json
import os
import pathlib
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ROOT = HERE.parents[1]

import build_digest as b  # noqa: E402

KEYS = ["christianity", "catholic", "judaism", "islam", "hinduism", "buddhism",
        "taoism", "stoicism", "mythology", "restoration"]


class EditionTests(unittest.TestCase):
    def setUp(self):
        # load_edition rebinds the module matchers; restore the shared ones after each test.
        self.shared = (b.STRONG_RE, b.WEAK_RE, b.VETO_RE, b.EDITION)

    def tearDown(self):
        b.STRONG_RE, b.WEAK_RE, b.VETO_RE, b.EDITION = self.shared

    def test_shared_prompt_lists_every_tradition(self):
        prompt = b.system_prompt()
        for tradition in ("Christian", "Catholic", "Latter-day Saint", "Mythic", "Stoic", "Secular"):
            self.assertIn(tradition, prompt)
        self.assertNotIn("{traditions}", prompt)
        self.assertIn('{"rating": 0-10', prompt)   # the JSON example survived substitution

    def test_every_edition_file_loads(self):
        for key in KEYS:
            edition = b.load_edition(key, root=str(ROOT / "editions"))
            self.assertTrue(edition["traditions"], key)
            self.assertLessEqual(set(edition["traditions"]), set(b.TRADITIONS), key)
            prompt = b.system_prompt()
            self.assertIn("tradition one of: " + ", ".join(edition["traditions"]) + ".", prompt)
            self.assertIn(f"uses the {key} edition", prompt)

    def test_edition_terms_extend_the_shared_lists_as_whole_words(self):
        b.load_edition("restoration", root=str(ROOT / "editions"))
        self.assertTrue(b.STRONG_RE.search("our ward council"))
        self.assertFalse(b.STRONG_RE.search("the warden"))
        self.assertTrue(b.STRONG_RE.search("alma the younger"))
        self.assertFalse(b.STRONG_RE.search("an almanac"))
        self.assertTrue(b.STRONG_RE.search("a sermon on scripture"))   # shared term still fires
        self.assertTrue(b.VETO_RE.search("the broadway musical"))
        self.assertTrue(b.VETO_RE.search("true crime"))                 # shared veto still fires

    def test_unknown_tradition_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bogus.json"
            path.write_text(json.dumps({"traditions": ["Klingon"], "strong": [], "weak": [], "veto": []}))
            with self.assertRaises(SystemExit):
                b.load_edition("bogus", root=tmp)

    def test_bad_key_is_rejected(self):
        with self.assertRaises(SystemExit):
            b.load_edition("../etc", root=str(ROOT / "editions"))


if __name__ == "__main__":
    unittest.main()
