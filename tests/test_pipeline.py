"""Dry-run rules: inactive campaigns, warning-only views line, slots, safety, captions."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from clipper.buffer_api import ErrorBudget, create_post, should_stop_platform
from clipper.fetchsrc import download_command, dropbox_direct
from clipper.engine import (
    build_caption,
    quiet_gap_minutes,
    rejection,
    upcoming_slots,
    views_to_first_payout,
)
from clipper.loadconfig import check_bundle, load_bundle
from clipper.render import ffmpeg_available, logo_filter, render_color
from clipper.run import run_pipeline
from clipper.safety import classify
from clipper.watermark import resolve_watermark, synthetic_frame

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)


def _bundle() -> dict:
    return load_bundle(ROOT)


def _activate(bundle: dict, *ids: str) -> dict:
    cloned = copy.deepcopy(bundle)
    wanted = set(ids)
    for row in cloned["campaigns"]["campaigns"]:
        row["active"] = row["id"] in wanted
    return cloned


class ConfigTests(unittest.TestCase):
    def test_seeded_campaigns_are_inactive_and_check_passes(self):
        bundle = _bundle()
        self.assertEqual(check_bundle(bundle), [])
        active = sorted(row["id"] for row in bundle["campaigns"]["campaigns"] if row.get("active"))
        self.assertEqual(active, ["dhan-aziz", "sui-basecamp"])
        for row in bundle["campaigns"]["campaigns"]:
            if row["id"] not in active:
                self.assertFalse(row["active"], row["id"])
            self.assertIsInstance(row["payout_timing"], str)
            self.assertIsInstance(row["approval_notes"], str)
            self.assertEqual(row["payout_timing"], "")
            self.assertEqual(row["approval_notes"], "")
            self.assertIn("min_payout", row)
            self.assertIn("max_payout", row)
            self.assertIn("min_views", row)

    def test_views_math_warns_only_above_the_line(self):
        bundle = _bundle()
        by_id = {row["id"]: row for row in bundle["campaigns"]["campaigns"]}
        self.assertEqual(views_to_first_payout(by_id["t-bird"]), 10000)
        self.assertEqual(views_to_first_payout(by_id["minogue"]), 1000)
        self.assertAlmostEqual(views_to_first_payout(by_id["jackelyne"]), 2000 / 1.5)
        self.assertEqual(views_to_first_payout(by_id["ricky-hilfiger"]), 1500)
        clip = {"content_type": "tbird_film", "platform": "x", "duration_sec": 20}
        self.assertEqual(rejection(by_id["t-bird"], clip), "inactive")
        turned_on = copy.deepcopy(by_id["t-bird"])
        turned_on["active"] = True
        self.assertIsNone(rejection(turned_on, clip))
        self.assertNotIn("view", rejection(turned_on, clip) or "")


class DryRunTests(unittest.TestCase):
    def test_active_campaigns_content_plan_and_dhan_drafts(self):
        report = run_pipeline(_bundle(), now=NOW)
        self.assertEqual(report["errors"], [])
        self.assertEqual(report["posting"], "enabled")
        self.assertEqual(report["active_campaigns"], ["sui-basecamp", "dhan-aziz"])
        self.assertIn("content plan", report["posting_note"])
        self.assertIn("awaiting_approval", report["posting_note"])
        sui = [row for row in report["packages"] if row.get("campaign_id") == "sui-basecamp" and row["status"] == "scheduled"]
        self.assertTrue(sui)
        self.assertTrue(all("x.com/SuiNetwork" in row["caption"] for row in sui))
        self.assertTrue(all(row["logo_applied"] is False for row in sui))
        youtube = next(row for row in sui if row["platform"] == "youtube")
        self.assertIn("#shorts", youtube["caption"])
        dhan_posts = [
            row for row in report["packages"]
            if row.get("campaign_id") == "dhan-aziz" and row["status"] in {"scheduled", "reminder"}
        ]
        self.assertEqual(dhan_posts, [])
        drafts = [row for row in report["packages"] if row["status"] == "awaiting_approval"]
        self.assertTrue(drafts)
        self.assertTrue(all(row["campaign_id"] == "dhan-aziz" for row in drafts))
        plan = [
            row for row in report["packages"]
            if row["status"] == "scheduled" and row.get("campaign_id") is None
        ]
        self.assertTrue(any(row["platform"] == "x" for row in plan))
        self.assertTrue(all(row["logo_applied"] is False for row in plan))
        self.assertTrue(all(not row["caption"].startswith("#Ad") for row in plan))
        self.assertTrue(any(row["status"] == "rejected_safety" for row in report["packages"]))
        joined = "\n".join(report["warnings"])
        self.assertIn("t-bird", joined)
        self.assertIn("10000", joined)
        self.assertNotIn("sui-basecamp", joined)
        self.assertNotIn("dhan-aziz", joined)
        for cid in ("t-bird", "minogue", "jackelyne", "a24-slayground", "crookbrown", "ricky-hilfiger", "rolling-stone", "oz-ali"):
            self.assertFalse(any(
                row.get("campaign_id") == cid and row["status"] in {"scheduled", "reminder"}
                for row in report["packages"]
            ))
        payloads = [row["buffer"] for row in report["packages"] if row.get("buffer")]
        self.assertTrue(payloads)
        self.assertTrue(all("example.invalid" in json.dumps(item) for item in payloads))

    def test_approved_dhan_posts_the_exact_file(self):
        bundle = _bundle()
        bundle["approvals"]["approved"] = ["fixture-dhan-ig", "fixture-dhan-yt"]
        report = run_pipeline(bundle, now=NOW)
        posted = [
            row for row in report["packages"]
            if row.get("campaign_id") == "dhan-aziz" and row["status"] == "scheduled"
        ]
        self.assertEqual(sorted(row["platform"] for row in posted), ["instagram", "youtube"])
        for row in posted:
            self.assertIn("@DHANAZIZ", row["caption"])
            self.assertFalse(row["logo_applied"])
            self.assertEqual(row["filter_graph"], "copy")
            self.assertEqual(row["variant"], {"crop_x": 0.0, "start_sec": 0.0})
            self.assertFalse(row["buffer"]["metadata"]["youtube"].get("privacy") == "private" if row["platform"] == "youtube" else False)

    def test_minogue_routes_when_activated(self):
        report = run_pipeline(_activate(_bundle(), "minogue"), now=NOW)
        self.assertEqual(report["posting"], "enabled")
        posted = [row for row in report["packages"] if row["status"] == "scheduled" and row["campaign_id"] == "minogue"]
        self.assertTrue(posted)
        self.assertIn("Mike Minogue", posted[0]["caption"])
        self.assertIn("#shorts", posted[0]["caption"])
        self.assertNotIn("T BIRD", posted[0]["caption"])
        self.assertNotIn("Jacky", posted[0]["caption"])
        self.assertNotIn("SLAYGROUND", posted[0]["caption"])
        self.assertTrue(any("t-bird" in warning for warning in report["warnings"]))
        short = next(row for row in report["packages"] if row["source_id"] == "fixture-minogue-short")
        self.assertEqual(short["status"], "too_short")

    def test_tbird_still_routes_when_activated_and_stays_a_warning(self):
        report = run_pipeline(_activate(_bundle(), "t-bird"), now=NOW)
        posted = [row for row in report["packages"] if row["status"] in {"scheduled", "reminder"} and row["campaign_id"] == "t-bird"]
        self.assertTrue(posted)
        self.assertTrue(any("10000" in warning for warning in report["warnings"]))
        self.assertNotIn("views", " ".join(row.get("reason") or "" for row in report["packages"]))
        youtube = next(row for row in posted if row["platform"] == "youtube")
        self.assertIn("#shorts", youtube["caption"])
        self.assertLessEqual(youtube["caption"].count("#"), 5)
        skipped = next(row for row in report["packages"] if row["source_id"] == "fixture-tbird-full")
        self.assertEqual(skipped["status"], "skipped_watermark")
        duplicates = [row for row in report["packages"] if row["status"] == "duplicate"]
        self.assertTrue(duplicates)

    def test_jackelyne_stays_blocked_without_a_fan_page_and_official_logo(self):
        report = run_pipeline(_activate(_bundle(), "jackelyne"), now=NOW, env={})
        row = next(item for item in report["packages"] if item["source_id"] == "fixture-jackelyne-ig")
        self.assertEqual(row["status"], "needs_account")
        self.assertIn("needs_official_logo", row["reason"])
        self.assertFalse(row.get("buffer"))

    def test_owner_notes_show_up_when_filled(self):
        bundle = _bundle()
        tbird = next(row for row in bundle["campaigns"]["campaigns"] if row["id"] == "t-bird")
        tbird["payout_timing"] = "Net-15 after approval"
        tbird["approval_notes"] = "Manager said wait for the new cut."
        report = run_pipeline(bundle, now=NOW)
        note = report["owner_notes"]["t-bird"]
        self.assertEqual(note["payout_timing"], "Net-15 after approval")
        self.assertIn("Manager said", note["approval_notes"])


class SlotTests(unittest.TestCase):
    def test_each_platform_has_its_own_ist_window(self):
        schedule = _bundle()["pipeline"]["schedule"]
        for platform in ("x", "youtube", "instagram", "tiktok"):
            spec = schedule[platform]
            self.assertEqual(spec["timezone"], "Asia/Kolkata")
            self.assertEqual(len(spec["slots"]), 10)
            gap = quiet_gap_minutes(spec["slots"])
            self.assertGreaterEqual(gap, 200)
            self.assertLessEqual(gap, 260)
        self.assertEqual(schedule["youtube"]["slots"], schedule["instagram"]["slots"])
        self.assertNotEqual(schedule["x"]["slots"], schedule["youtube"]["slots"])
        self.assertNotEqual(schedule["tiktok"]["slots"][0], schedule["x"]["slots"][0])
        slots = upcoming_slots(NOW, schedule["youtube"]["slots"], "Asia/Kolkata", 10)
        self.assertEqual(len(slots), 10)
        self.assertEqual(slots[0].astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "07:00")


class SafetyAndCaptionTests(unittest.TestCase):
    def test_safety_categories(self):
        self.assertEqual(classify("explicit sex tape"), "sexual")
        self.assertEqual(classify("a gory beheading"), "gore")
        self.assertEqual(classify("this is a hate crime"), "hate")
        self.assertEqual(classify("the blackout challenge"), "dangerous")
        self.assertEqual(classify("guaranteed profit"), "misleading")
        self.assertIsNone(classify("Bollywood romantic dialogue scene"))

    def test_hashtag_cap_and_hook_rotation(self):
        campaign = {
            "id": "sample",
            "caption": {
                "disclosure": "#Ad",
                "hooks": ["first hook line", "second hook line"],
                "required_phrases": ["Sample"],
                "hashtags": ["#one", "#two", "#three", "#four", "#five", "#six"],
                "platform_tags": {},
                "exclusive_tokens": [],
            },
        }
        limits = {"max_hashtags": 5, "require_hashtags": ["#shorts"], "char_limit": 500}
        used: set[str] = set()
        first = build_caption(campaign, {"source_id": "a"}, "youtube", 280, limits=limits, used=used)
        second = build_caption(campaign, {"source_id": "b"}, "youtube", 280, limits=limits, used=used)
        self.assertLessEqual(first.count("#"), 5)
        self.assertIn("#shorts", first)
        self.assertIn("Sample", first)
        self.assertNotEqual(first, second)

    def test_watermark_cover_and_skip(self):
        settings = _bundle()["pipeline"]["watermark"]
        clean = resolve_watermark(synthetic_frame("clean"), 180, 320, settings)
        covered = resolve_watermark(synthetic_frame("corner_logo"), 180, 320, settings)
        skipped = resolve_watermark(synthetic_frame("full_frame"), 180, 320, settings)
        self.assertEqual(clean["status"], "clean")
        self.assertEqual(covered["status"], "covered")
        self.assertEqual(skipped["status"], "skipped")

    def test_logo_graph_has_no_drawtext(self):
        campaign = _bundle()["campaigns"]["campaigns"]
        jack = next(row for row in campaign if row["id"] == "jackelyne")
        graph, uses_logo = logo_filter(jack, 720, 1280, 0.0)
        self.assertTrue(uses_logo)
        self.assertNotIn("drawtext", graph)
        self.assertIn("overlay", graph)

    def test_rate_limit_stops_without_a_retry_loop(self):
        self.assertTrue(should_stop_platform("429 Too Many Requests", 1, 2))
        budget = ErrorBudget(limit=2)
        self.assertFalse(budget.record("youtube", "temporary blip"))
        self.assertTrue(budget.record("youtube", "temporary blip"))
        self.assertIn("youtube", budget.halted)
        with self.assertRaises(Exception):
            create_post("https://api.buffer.com", "token", {"text": "https://example.invalid/clip.mp4"})


class DownloadTests(unittest.TestCase):
    def test_public_share_commands(self):
        folder = "https://www.dropbox.com/scl/fo/q7yh4nn5cmjco6tcn9qlp/AH5g6pAm_ok984WYOXul0O0?rlkey=xb4vtwrztelm5hugx8i1kd6tq&dl=0"
        direct = dropbox_direct(folder)
        self.assertIn("dl=1", direct)
        self.assertNotIn("dl=0", direct)
        command = download_command({"kind": "dropbox_folder", "url": folder}, "work/inbox/dhan-aziz.zip")
        self.assertEqual(command[0], "curl")
        self.assertIn("dl=1", command[-1])
        drive = download_command(
            {"kind": "google_drive_folder", "url": "https://drive.google.com/drive/folders/1tepyyEVFtGLi8Ukv8AddBDj2FcIb7d-i"},
            "work/inbox/sui-basecamp",
        )
        self.assertEqual(drive[0], "gdown")
        self.assertIn("--folder", drive)
        from clipper.run import planned_downloads
        report = run_pipeline(_bundle(), now=NOW)
        commands = planned_downloads(report, _bundle())
        blobs = [" ".join(item["command"]) for item in commands]
        self.assertTrue(any(item.startswith("gdown") for item in blobs))
        self.assertTrue(any("dl=1" in item for item in blobs))


class RenderTests(unittest.TestCase):
    def test_ffmpeg_fixture_if_present(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg is not installed")
        dest = ROOT / "work" / "test-color.mp4"
        render_color(dest, 160, 284, 0.4, "clean")
        self.assertTrue(dest.is_file())
        self.assertGreater(dest.stat().st_size, 100)


class SubmissionTests(unittest.TestCase):
    def test_mark_submitted_round_trip(self):
        from clipper.submissions import empty_pending, live_item, mark_submitted

        pending = empty_pending("https://example.invalid/pending.json", 30)
        package = {
            "campaign_id": "minogue",
            "platform": "youtube",
            "source_id": "clip-1",
            "content_rewards_campaign_id": "abc",
            "submit_page": "https://contentrewards.com/c/campaigns/abc",
        }
        item = live_item(package, "https://example.invalid/post/1", NOW, 30, buffer_post_id="buf")
        pending["items"] = [item]
        submitted = {"version": 1, "items": []}
        pending, submitted = mark_submitted(pending, submitted, item["id"], NOW)
        self.assertEqual(pending["items"], [])
        self.assertEqual(submitted["items"][0]["status"], "submitted")
        self.assertTrue(json.dumps(item))


if __name__ == "__main__":
    unittest.main()
