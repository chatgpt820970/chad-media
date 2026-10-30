import unittest

from growth.demos import _placeholder_caption
from growth.utils.content_filter import (
    devotional_reason, is_love, label_reason, neutral_caption, promo_title_reason, source_reasons, strip_emoji,
)
from growth.utils.media import fit_graph, parse_scene_times, parse_silence_starts, pick_clean_end
from growth.utils.quality import media_reasons
from growth.utils.ytdlp import Candidate


def cand(title="cute couple moment", uploader="lovereels", account="", topic="love", url="https://youtube.com/shorts/x", desc=""):
    return Candidate("abc", title, url, "indian", topic, description=desc, uploader=uploader, account=account)


class Labels(unittest.TestCase):
    def test_labels_blocked(self):
        for up in ("T-Series", "Zee Music Company", "Z Music", "Saregama Music", "Sony Music India", "Tips Official",
                   "Speed Records", "YRF", "Eros Now Music", "Venus", "Shemaroo", "Aditya Music", "Lahari Music",
                   "Times Music", "Zee Studios"):
            self.assertTrue(label_reason(cand(uploader=up)), up)
        self.assertTrue(label_reason(cand(uploader="", account="https://www.youtube.com/@TSeries/shorts")))
        self.assertIsNone(label_reason(cand(uploader="desi couple edits")))

    def test_promo_titles(self):
        for t in ("Mangal Rang Official Trailer", "Teaser out now", "Book tickets now", "In cinemas Friday",
                  "Romantic Jukebox", "Full Album 2024", "Tum Hi Ho Lyrical"):
            self.assertTrue(promo_title_reason(cand(title=t)), t)
        self.assertIsNone(promo_title_reason(cand(title="couple dance at wedding")))


class Devotional(unittest.TestCase):
    def test_hits(self):
        for t in ("Jai Shri Ram", "Ravana Tandav", "Ramayana scene", "Mahadev status", "Hanuman Chalisa", "Krishna bhajan"):
            self.assertTrue(devotional_reason(cand(title=t)), t)

    def test_word_boundaries(self):
        for t in ("dramatic romance", "Ramesh and Priya love story", "ramp walk couple", "shiver of love", "programmer love", "kissing in the rain"):
            self.assertIsNone(devotional_reason(cand(title=t)), t)

    def test_love_required(self):
        self.assertTrue(is_love(cand(topic="paparazzi", title="Pyaar ho gaya")))
        self.assertFalse(is_love(cand(topic="paparazzi", title="airport look")))
        self.assertIn("not love/romantic", source_reasons(cand(topic="comedy", title="funny prank"), require_love=True))
        self.assertEqual(source_reasons(cand()), [])


class Captions(unittest.TestCase):
    def test_neutral_caption(self):
        c = cand(title="Tum Hi Ho 😍❤️ Aashiqui 2 #viral")
        text = _placeholder_caption({"platform": "instagram"}, c)
        self.assertNotIn("Tum Hi Ho", text)
        self.assertEqual(text, strip_emoji(text))
        tags = [w for w in text.split() if w.startswith("#")]
        self.assertTrue(1 <= len(tags) <= 2)
        self.assertIn("#shorts", neutral_caption(c, "youtube"))

    def test_strip_emoji(self):
        self.assertEqual(strip_emoji("so cute 😍❤️\n#love 🔥"), "so cute\n#love")


class Media(unittest.TestCase):
    def test_corner_logo_rejects(self):
        reasons = media_reasons(1080, 1920, 20.0, "", ["top-right"], False, False, 720, reject_corner=True)
        self.assertTrue(any("corner logo" in r for r in reasons))
        self.assertEqual(media_reasons(1080, 1920, 20.0, "", [], False, False, 720), [])

    def test_clean_end(self):
        self.assertEqual(pick_clean_end(20.0, [18.4], [17.5]), 18.4)
        self.assertEqual(pick_clean_end(20.0, [], [17.5]), 17.5)
        self.assertEqual(pick_clean_end(20.0, [10.0], []), 20.0)
        self.assertEqual(parse_silence_starts("[silencedetect] silence_start: 1.25\nsilence_start: 2.5", 10), [11.25, 12.5])
        self.assertEqual(parse_scene_times("n:0 pts:1 pts_time:0.84 x", 5), [5.84])

    def test_fit_graph_no_subject_crop(self):
        graph = fit_graph("setpts=PTS/1.030")
        fg = graph.split("[fg]scale=", 1)[1].split("[fit]", 1)[0]
        self.assertNotIn("crop", fg)
        self.assertIn("boxblur", graph)


if __name__ == "__main__":
    unittest.main()


class FollowUp(unittest.TestCase):
    def test_ram_names_not_devotional(self):
        for t in ("Ram Charan romantic scene", "Ram Kapoor and Sakshi couple", "Ram and Priya love"):
            self.assertIsNone(devotional_reason(cand(title=t)), t)
        for t in ("Jai Shri Ram", "Siya Ram status", "Ram ji ki kripa", "Ram bhajan", "Ram Navami special"):
            self.assertTrue(devotional_reason(cand(title=t)), t)

    def test_broad_love(self):
        for t in ("Tum hi ho", "dil se", "heart touching song", "shaadi vibes", "Kesariya cover"):
            self.assertTrue(is_love(cand(topic="bollywood", title=t)), t)
        self.assertTrue(is_love(cand(topic="love", title="untitled clip")))

    def test_corner_hint_only_unless_gemini_off(self):
        self.assertEqual(media_reasons(1080, 1920, 20.0, "", ["top-left"], False, False, 720), [])
        self.assertTrue(media_reasons(1080, 1920, 20.0, "", ["top-left"], False, False, 720, reject_corner=True))
