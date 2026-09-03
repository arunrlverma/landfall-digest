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

# Verbatim from the published Stoicism digest of 2026-09-02, the run that put
# Catholic apologetics on a Stoic reading list. Each one reached the shortlist
# on the SHARED spiritual vocabulary alone — "god", "purpose", "priest" — with
# nothing of Stoicism in it, and was then labelled Interfaith because the
# narrowed label set left the scorer nothing more honest to say.
APOLOGETICS = (
    "#1202 - Three Coincidences That Prove God Exists Trent Horn examines three "
    "major coincidences that make atheism increasingly difficult to accept: the "
    "fine-tuning of the universe, the strange reality of objective moral facts, "
    "and the massive number of miracle claims throughout history."
).lower()
COME_FOLLOW_ME = (
    "THE GOOD LIFE What does it actually mean to live the good life? Dave and Grace "
    "dive into Proverbs and Ecclesiastes to discover the patterns of living that "
    "lead to a life filled with wisdom, happiness, purpose, faith, and connection."
).lower()
JESUIT_INTERVIEW = (
    "Work In Progress (ep. 141) Mischke spends the hour with a Jesuit priest. "
    "Father James Martin's book, \"Work in Progress\", is the topic of conversation."
).lower()


class EditionTests(unittest.TestCase):
    def setUp(self):
        # load_edition rebinds the module matchers; restore the shared ones after each test.
        self.shared = (b.STRONG_RE, b.WEAK_RE, b.VETO_RE, b.EDITION,
                       b.EDITION_STRONG_RE, b.EDITION_WEAK_RE)

    def tearDown(self):
        (b.STRONG_RE, b.WEAK_RE, b.VETO_RE, b.EDITION,
         b.EDITION_STRONG_RE, b.EDITION_WEAK_RE) = self.shared

    def test_shared_prompt_lists_every_tradition(self):
        prompt = b.system_prompt()
        for tradition in ("Christian", "Catholic", "Latter-day Saint", "Mythic", "Stoic", "Secular"):
            self.assertIn(tradition, prompt)
        self.assertNotIn("{traditions}", prompt)
        self.assertNotIn("{listener}", prompt)
        self.assertIn('{"rating": 0-10', prompt)   # the JSON example survived substitution
        self.assertIn("open to every tradition and to none", prompt)

    def test_every_edition_file_loads(self):
        for key in KEYS:
            edition = b.load_edition(key, root=str(ROOT / "editions"))
            self.assertTrue(edition["traditions"], key)
            self.assertLessEqual(set(edition["traditions"]), set(b.TRADITIONS), key)
            prompt = b.system_prompt()
            self.assertNotIn("{listener}", prompt)
            self.assertIn(f"the {key} shelf", prompt)
            self.assertIn(", ".join(edition["traditions"]), prompt)

    def test_edition_prompt_still_offers_every_label(self):
        """The regression that put apologetics on the Stoicism shelf.

        The label set used to be narrowed to the shelf's own traditions, so an
        off-shelf episode had no honest label and took the nearest catch-all.
        The scorer must be able to say "Catholic" on the Stoicism shelf; the
        tradition gate then drops the pick."""
        b.load_edition("stoicism", root=str(ROOT / "editions"))
        prompt = b.system_prompt()
        self.assertIn("tradition one of: " + ", ".join(b.TRADITIONS) + ".", prompt)
        for other in ("Catholic", "Christian", "Muslim", "Latter-day Saint"):
            self.assertIn(other, prompt)

    def test_edition_terms_extend_the_shared_lists_as_whole_words(self):
        b.load_edition("restoration", root=str(ROOT / "editions"))
        self.assertTrue(b.STRONG_RE.search("our ward council"))
        self.assertFalse(b.STRONG_RE.search("the warden"))
        self.assertTrue(b.STRONG_RE.search("alma the younger"))
        self.assertFalse(b.STRONG_RE.search("an almanac"))
        self.assertTrue(b.STRONG_RE.search("a sermon on scripture"))   # shared term still fires
        self.assertTrue(b.VETO_RE.search("the broadway musical"))
        self.assertTrue(b.VETO_RE.search("true crime"))                 # shared veto still fires

    def test_edition_terms_are_the_relevance_gate(self):
        """Shared spiritual vocabulary no longer claims an episode for a shelf."""
        b.load_edition("stoicism", root=str(ROOT / "editions"))
        for text in (APOLOGETICS, COME_FOLLOW_ME, JESUIT_INTERVIEW):
            self.assertTrue(b.STRONG_RE.search(text) or b.WEAK_RE.search(text),
                            "the shared lists did admit these — that was the bug")
            self.assertFalse(b.on_topic(*b.edition_hits(text)), text[:40])

    def test_the_stoicism_shelf_still_admits_stoicism(self):
        b.load_edition("stoicism", root=str(ROOT / "editions"))
        for text in ("seneca on the shortness of life",
                     "epictetus and what is up to us",
                     "marcus aurelius, philosopher on the throne",
                     "a philosopher on grief and mortality",
                     "camus, absurdity and whether life has meaning",
                     "the stoic answer to anger"):
            self.assertTrue(b.on_topic(*b.edition_hits(text)), text)

    def test_one_soft_term_is_not_enough_but_three_are(self):
        b.load_edition("stoicism", root=str(ROOT / "editions"))
        self.assertFalse(b.on_topic(*b.edition_hits("a sermon about justice")))
        self.assertTrue(b.on_topic(*b.edition_hits(
            "on justice, courage and self-control")))

    def test_every_shelf_admits_its_own_subject(self):
        """Each edition's own list must be able to name the edition — the shared
        list is not allowed to do it for them any more."""
        home = {
            "christianity": "a sermon on the gospel of john and the bible",
            "catholic": "the rosary, the mass and the eucharist",
            "judaism": "this week's parsha and a page of talmud",
            "islam": "tafsir of the surah, hadith and the sunnah",
            "hinduism": "the bhagavad gita, karma and the upanishads",
            "buddhism": "a dharma talk on the sutta and zazen",
            "taoism": "the tao te ching, wu wei and zhuangzi",
            "stoicism": "marcus aurelius, seneca and stoic philosophy",
            "mythology": "norse mythology, the edda and ragnarok",
            "restoration": "come, follow me: the book of mormon and general conference",
        }
        for key, text in home.items():
            b.load_edition(key, root=str(ROOT / "editions"))
            self.assertTrue(b.on_topic(*b.edition_hits(text)), key)

    def test_no_shelf_admits_another_shelf_by_its_own_terms(self):
        """Not a veto on other faiths — those episodes are welcome in the app,
        on their own shelf. This asserts only that a shelf's terms do not claim
        another tradition's teaching episode."""
        foreign = {
            "stoicism": ["the rosary, the mass and the eucharist",
                         "this week's parsha and a page of talmud",
                         "tafsir of the surah, hadith and the sunnah"],
            "judaism": ["a sermon on the gospel of john and the atonement"],
            "islam": ["the rosary, the mass and the eucharist"],
            "hinduism": ["a sermon on the gospel of john and the atonement"],
            "restoration": ["tafsir of the surah, hadith and the sunnah"],
            "mythology": ["a sermon on the gospel of john and the atonement"],
        }
        for key, texts in foreign.items():
            b.load_edition(key, root=str(ROOT / "editions"))
            for text in texts:
                self.assertFalse(b.on_topic(*b.edition_hits(text)), f"{key}: {text}")

    def test_a_bare_surname_does_not_claim_a_shelf(self):
        """"ward" and "stake" put two true-crime episodes about a pastor's wife
        on the Restoration shelf: one matched a surname, the other "at stake"."""
        b.load_edition("restoration", root=str(ROOT / "editions"))
        self.assertFalse(b.on_topic(*b.edition_hits(
            "pastor's wife mica miller: enhanced 911 whispers heard, says ward")))
        self.assertFalse(b.on_topic(*b.edition_hits(
            "inside the u.s. deal to get venezuela's oil: what is at stake")))
        self.assertTrue(b.on_topic(*b.edition_hits(
            "our ward council and the stake conference")))

    def test_every_edition_names_its_own_charts(self):
        """Apple publishes a chart per subgenre and that is where most editions
        live; without them the Judaism and Islam shelves have no pool at all."""
        for key in KEYS:
            edition = b.load_edition(key, root=str(ROOT / "editions"))
            self.assertTrue(edition["charts"], key)
            self.assertTrue(all(isinstance(g, int) for g in edition["charts"]), key)

    def test_charts_must_be_genre_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bad.json"
            path.write_text(json.dumps({"traditions": ["Stoic"], "strong": ["stoic"],
                                        "weak": [], "veto": [], "charts": ["1443"]}))
            with self.assertRaises(SystemExit):
                b.load_edition("bad", root=tmp)

    def test_unknown_tradition_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "bogus.json"
            path.write_text(json.dumps({"traditions": ["Klingon"], "strong": [], "weak": [], "veto": []}))
            with self.assertRaises(SystemExit):
                b.load_edition("bogus", root=tmp)

    def test_an_edition_without_terms_of_its_own_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "empty.json"
            path.write_text(json.dumps({"traditions": ["Stoic"], "strong": [], "weak": [], "veto": []}))
            with self.assertRaises(SystemExit):
                b.load_edition("empty", root=tmp)

    def test_bad_key_is_rejected(self):
        with self.assertRaises(SystemExit):
            b.load_edition("../etc", root=str(ROOT / "editions"))


if __name__ == "__main__":
    unittest.main()
