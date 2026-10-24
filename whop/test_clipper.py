"""Platform mapping: X (@chadriyo) and YouTube Shorts (Denzen) only hit campaigns that pay them."""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from whop.clipper import (
    FEED_JSON_PATH,
    MANIFEST_PATH,
    CampaignPlatformError,
    banner_overlay_spec,
    bind_clip_to_campaign,
    build_daily_manifest,
    build_platform_caption,
    campaigns_for_platform,
    check_campaign_pool_guard,
    duration_fits,
    dishpilot_clip_filter,
    ffmpeg_banner_filter,
    load_campaigns,
    overlay_plan,
    select_campaign_for_platform,
    verify_manifest_data,
)

X_PAYING_IDS = {
    "dishpilot-clipping",
    "finsta-ugc-clipping",
    "darkroom-clipping",
    "nova-x-clipping",
    "cravin-x-clipping",
}
INDIAN_CATEGORIES = {"indian_actor_reels", "indian_movie_scenes"}
LOAN_SPAM = r"(?i)fastloan|fast\s*loan|loan|instant\s+cash|\$\s*5,?000|\$\s*200"


class PlatformCampaignMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_campaigns()
        self.guard = self.data["pool_guard_rules"]
        self.flp = next(
            campaign
            for campaign in self.data["active_campaigns"]
            if campaign["id"] == "fast-loan-pilot"
        )

    def test_fastloanpilot_rejected_for_x_and_selected_for_youtube_shorts(self) -> None:
        self.assertEqual(self.flp["product_id"], "prod_DyuwRHNCz3vpv")
        self.assertEqual(self.flp["experience_id"], "exp_cqVzB3tgaTR5tB")
        self.assertEqual(
            self.flp["submission_url"],
            "https://whop.com/joined/fast-loan-pilot-logo-clipping/exp_cqVzB3tgaTR5tB/app/",
        )
        self.assertEqual(self.flp["accepted_platforms"], ["youtube_shorts", "instagram_reels", "tiktok"])
        self.assertNotIn("x", self.flp["accepted_platforms"])
        self.assertEqual(self.flp["rules"]["min_duration_sec"], 6)
        self.assertEqual(self.flp["rules"]["max_duration_sec"], 15)
        self.assertEqual(self.flp["rate_per_1k_views_usd"], 2.0)
        self.assertEqual(self.flp["remaining_pool_usd"], 10000.0)

        with self.assertRaises(CampaignPlatformError) as caught:
            bind_clip_to_campaign(self.flp, "x")
        self.assertIn("@chadriyo", str(caught.exception))
        self.assertIn("fast-loan-pilot", str(caught.exception))

        flp_only = {
            "pool_guard_rules": self.guard,
            "active_campaigns": [self.flp],
            "platform_routing": {},
        }
        with self.assertRaises(CampaignPlatformError) as caught:
            select_campaign_for_platform(flp_only, "x")
        self.assertIn("@chadriyo", str(caught.exception))

        youtube_only = select_campaign_for_platform(flp_only, "youtube_shorts")
        self.assertEqual(youtube_only["id"], "fast-loan-pilot")

        youtube = select_campaign_for_platform(self.data, "youtube_shorts")
        self.assertEqual(youtube["id"], "fast-loan-pilot")
        self.assertIn("youtube_shorts", youtube["accepted_platforms"])
        self.assertNotIn("x", youtube["accepted_platforms"])
        self.assertEqual(youtube["rules"]["logo_overlay_required"], True)

    def test_select_x_campaign_accepts_x_and_passes_pool_guard(self) -> None:
        chosen = select_campaign_for_platform(self.data, "x")
        self.assertIn(chosen["id"], X_PAYING_IDS)
        self.assertIn("x", chosen["accepted_platforms"])
        self.assertNotEqual(chosen["id"], "fast-loan-pilot")
        self.assertNotEqual(chosen["id"], "whop-ai-shane-gillis")
        ok, reason = check_campaign_pool_guard(chosen, self.guard)
        self.assertTrue(ok, reason)
        self.assertEqual(chosen["rate_per_1k_views_usd"], 2.0)

        paying = campaigns_for_platform(self.data, "x")
        paying_ids = [campaign["id"] for campaign in paying]
        self.assertEqual(
            paying_ids,
            [
                "dishpilot-clipping",
                "finsta-ugc-clipping",
                "darkroom-clipping",
                "nova-x-clipping",
                "cravin-x-clipping",
            ],
        )
        best = max(paying, key=lambda campaign: campaign["remaining_pool_usd"])
        self.assertEqual(chosen["id"], best["id"])
        for campaign in paying:
            self.assertIn("x", campaign["accepted_platforms"])
            passed, detail = check_campaign_pool_guard(campaign, self.guard)
            self.assertTrue(passed, detail)

    def test_pool_guard_blocks_high_denial_shane_gillis(self) -> None:
        shane = next(
            campaign
            for campaign in self.data["active_campaigns"]
            if campaign["id"] == "whop-ai-shane-gillis"
        )
        self.assertEqual(shane["denial_rate"], 0.998)
        ok, reason = check_campaign_pool_guard(shane, self.guard)
        self.assertFalse(ok)
        self.assertIn("99.8%", reason)
        self.assertIn("denial", reason)

        bait = {
            "id": "whop-ai-shane-gillis",
            "status": "open",
            "denial_rate": 0.998,
            "total_pool_usd": 1000.0,
            "remaining_pool_usd": 970.0,
            "paid_out_percent": 3.0,
            "spots_remaining": 97,
            "unlimited_budget": False,
            "accepted_platforms": ["x", "youtube_shorts"],
            "submission_url": "https://whop.com/joined/example/app/",
            "experience_id": "exp_example",
        }
        ok, reason = check_campaign_pool_guard(bait, self.guard)
        self.assertFalse(ok)
        self.assertIn("99.8%", reason)

        unlimited_bait = dict(bait, unlimited_budget=True)
        ok, reason = check_campaign_pool_guard(unlimited_bait, self.guard)
        self.assertFalse(ok)
        self.assertIn("denial", reason)

        x_choice = select_campaign_for_platform(self.data, "x")
        self.assertNotEqual(x_choice["id"], "whop-ai-shane-gillis")

    def test_daily_manifest_pays_every_platform_view(self) -> None:
        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        self.assertEqual(verify_manifest_data(manifest, self.data), [])
        self.assertEqual(len(manifest["slots"]), 10)

        x_ids = {slot["x_campaign"] for slot in manifest["slots"]}
        self.assertEqual(x_ids, X_PAYING_IDS)
        self.assertEqual(manifest["slots"][0]["x_campaign"], "dishpilot-clipping")
        self.assertEqual(manifest["slots"][1]["x_campaign"], "finsta-ugc-clipping")
        self.assertEqual(manifest["slots"][2]["x_campaign"], "darkroom-clipping")
        self.assertEqual(manifest["slots"][3]["x_campaign"], "nova-x-clipping")
        self.assertEqual(manifest["slots"][3]["x_rate_per_1k_views_usd"], 5.0)
        self.assertEqual(manifest["slots"][4]["x_campaign"], "cravin-x-clipping")

        flp_cta = self.flp["rules"]["mandatory_caption"]
        locked_x = {"funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert"}
        for slot in manifest["slots"]:
            self.assertIn(slot["youtube_campaign"], ("fast-loan-pilot", "mr-pa-clipping"))
            self.assertEqual(slot["youtube_handle"], "Denzen")
            self.assertEqual(slot["youtube_username"], "@denzen0")
            self.assertIn(slot["hook"], slot["youtube_caption"])
            self.assertIn(slot["x_campaign"], X_PAYING_IDS)
            self.assertEqual(slot["x_handle"], "@chadriyo")
            self.assertIn(slot["x_rate_per_1k_views_usd"], (2.0, 5.0))
            self.assertIn(slot["x_category"], locked_x)
            self.assertTrue(slot["youtube_duration_ok"])
            self.assertEqual(slot["youtube_duration_sec"], 12.5)
            self.assertRegex(slot["youtube_mp4_url"], r"/yt-flp-[123]\.mp4$")
            self.assertTrue(slot["x_duration_ok"])
            self.assertGreaterEqual(slot["x_duration_sec"], 20)
            self.assertLessEqual(slot["x_duration_sec"], 35)
            self.assertRegex(slot["x_mp4_url"], r"/x-clip-[123]\.mp4$")
            self.assertNotIn("http://", slot["x_caption"].lower())
            self.assertNotIn("https://", slot["x_caption"].lower())
            self.assertNotIn("whop.com", slot["x_caption"].lower())
            self.assertNotRegex(slot["x_caption"], LOAN_SPAM)
            self.assertIn(slot["hook"], slot["x_caption"])
            self.assertNotEqual(slot["x_campaign"], "fast-loan-pilot")
            self.assertEqual(slot["overlay"]["mode"], "platform_specific_variants")
            self.assertTrue(slot["x_submission_url"].startswith("https://"))
            for side in ("x", "youtube_shorts"):
                badge = slot["overlay"][side]
                self.assertLessEqual(badge["logo_count"], 1)
                self.assertIn(badge["logo_placement"], ("corner", "none"))
                self.assertEqual(badge["logo_subtext"], "")
            if slot["youtube_campaign"] == "fast-loan-pilot":
                self.assertIn(flp_cta, slot["youtube_caption"])
                self.assertEqual(slot["youtube_rate_per_1k_views_usd"], 2.0)
                self.assertEqual(slot["youtube_remaining_pool_usd"], 10000.0)
                self.assertEqual(slot["overlay"]["youtube_shorts"]["logo_count"], 1)
                self.assertEqual(slot["overlay"]["youtube_shorts"]["logo_placement"], "corner")
                self.assertNotIn(slot["youtube_category"], INDIAN_CATEGORIES)
            else:
                self.assertEqual(slot["youtube_campaign"], "mr-pa-clipping")
                self.assertIn("#mrpa", slot["youtube_caption"].lower())
                self.assertNotRegex(slot["youtube_caption"], LOAN_SPAM)
                self.assertEqual(slot["youtube_rate_per_1k_views_usd"], 1.25)
                self.assertEqual(slot["youtube_remaining_pool_usd"], 8507.0)
                self.assertEqual(slot["overlay"]["youtube_shorts"]["logo_count"], 0)
                self.assertIn(slot["youtube_category"], INDIAN_CATEGORIES)
            if slot["x_campaign"] == "dishpilot-clipping":
                tags = re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["x_caption"])
                self.assertGreaterEqual(len(tags), 1)
                self.assertLessEqual(len(tags), 2)
                self.assertNotIn("FastLoanPilot", slot["x_caption"])

        on_disk = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["slots"], manifest["slots"])
        self.assertEqual(verify_manifest_data(on_disk, self.data), [])
        feed = json.loads(FEED_JSON_PATH.read_text(encoding="utf-8"))
        self.assertEqual(feed["items"], manifest["slots"])
        self.assertTrue(all(item["youtube_duration_ok"] for item in feed["items"]))
        self.assertTrue(all(str(item["x_mp4_url"]).endswith((".mp4",)) for item in feed["items"]))

    def test_nova_and_cravin_accept_x(self) -> None:
        nova = next(campaign for campaign in self.data["active_campaigns"] if campaign["id"] == "nova-x-clipping")
        cravin = next(campaign for campaign in self.data["active_campaigns"] if campaign["id"] == "cravin-x-clipping")
        self.assertEqual(nova["title"], "Nova Clipping ($5 / 1K Views on X)")
        self.assertEqual(nova["whop_product_id"], "prod_3juzE1FqAXBsj")
        self.assertEqual(nova["whop_experience_id"], "exp_zB6vOQWkg5RKa6")
        self.assertEqual(
            nova["submission_url"],
            "https://whop.com/joined/nova-content/exp_zB6vOQWkg5RKa6/app/",
        )
        self.assertEqual(nova["cpm_usd"], 5.0)
        self.assertEqual(nova["pool_remaining_usd"], 1000.0)
        self.assertEqual(nova["denial_rate"], 0.0)
        self.assertEqual(
            nova["accepted_platforms"],
            ["x", "tiktok", "youtube_shorts", "instagram_reels"],
        )
        self.assertEqual(nova["min_duration_sec"], 10)
        self.assertEqual(nova["max_duration_sec"], 60)
        self.assertEqual(nova["required_caption"], "Check out Nova ✨ #Nova #Viral #Clips")
        nova_ok, nova_reason = check_campaign_pool_guard(nova, self.guard)
        self.assertTrue(nova_ok, nova_reason)
        bind_clip_to_campaign(nova, "x")

        self.assertEqual(cravin["title"], "Cravin Clipping Program ($2 / 1K Views on X)")
        self.assertEqual(cravin["whop_product_id"], "prod_636gGffOtD5Vc")
        self.assertEqual(cravin["whop_experience_id"], "exp_v8e0kiUai6qGnf")
        self.assertEqual(
            cravin["submission_url"],
            "https://whop.com/joined/cravin-clipping/exp_v8e0kiUai6qGnf/app/",
        )
        self.assertEqual(cravin["cpm_usd"], 2.0)
        self.assertEqual(cravin["pool_remaining_usd"], 1000.0)
        self.assertEqual(cravin["denial_rate"], 0.0)
        self.assertEqual(cravin["min_duration_sec"], 10)
        self.assertEqual(cravin["max_duration_sec"], 60)
        self.assertEqual(cravin["required_caption"], "Cravin hits different 🔥 #Cravin #FoodTok #Viral")
        cravin_ok, cravin_reason = check_campaign_pool_guard(cravin, self.guard)
        self.assertTrue(cravin_ok, cravin_reason)
        bind_clip_to_campaign(cravin, "x")

    def test_x_caption_strips_links_when_blocked(self) -> None:
        campaign = {
            "id": "example-x",
            "accepted_platforms": ["x"],
            "rules": {
                "mandatory_caption": "Watch the cut https://example.com/deal #Example",
                "hashtags": ["#Example", "#viral"],
            },
        }
        caption = build_platform_caption("Wait for it", campaign, "x", links_blocked=True)
        self.assertIn("Wait for it", caption)
        self.assertIn("#Example", caption)
        self.assertIn("#viral", caption)
        self.assertNotIn("http", caption.lower())
        self.assertNotIn("example.com/deal", caption)

        kept = build_platform_caption("Wait for it", campaign, "x", links_blocked=False)
        self.assertIn("https://example.com/deal", kept)

    def test_overlay_banner_follows_the_campaign_brand(self) -> None:
        dishpilot = next(
            campaign
            for campaign in self.data["active_campaigns"]
            if campaign["id"] == "dishpilot-clipping"
        )
        plan = overlay_plan(self.flp, dishpilot)
        self.assertEqual(plan["mode"], "platform_specific_variants")
        self.assertEqual(plan["x"]["logo_text"], "DISHPILOT")
        self.assertEqual(plan["x"]["logo_subtext"], "")
        self.assertEqual(plan["x"]["logo_count"], 1)
        self.assertEqual(plan["x"]["logo_placement"], "corner")
        self.assertEqual(plan["youtube_shorts"]["logo_path"], "whop/logos/fastloanpilot.png")
        self.assertEqual(plan["youtube_shorts"]["logo_count"], 1)
        self.assertEqual(plan["youtube_shorts"]["logo_placement"], "corner")
        self.assertTrue(plan["youtube_shorts"]["logo_overlay_required"])

        same = {
            "logo_path": "",
            "rules": {"logo_text": "SAME", "logo_subtext": "Sub", "logo_overlay_required": True},
        }
        self.assertEqual(overlay_plan(same, same)["mode"], "dual_compatible")
        self.assertEqual(overlay_plan(same, same)["x"]["logo_count"], 1)
        self.assertEqual(overlay_plan(same, same)["x"]["logo_subtext"], "")

        graph, label = ffmpeg_banner_filter(banner_overlay_spec(dishpilot), "[vbase]", "Sans")
        self.assertIn("text='Dishpilot'", graph)
        self.assertIn("fontsize=24:fontcolor=white@0.42:shadowcolor=black@0.28:shadowx=1:shadowy=1", graph)
        self.assertNotIn("Dishpilot.com", graph)
        self.assertNotIn("box=", graph)
        self.assertNotIn("drawbox", graph)
        self.assertIn("y=h-text_h-28", graph)
        self.assertEqual(graph.count("drawtext"), 1)
        self.assertEqual(label, "[vbadge]")
        nova_graph, _nova_label = ffmpeg_banner_filter(
            {"logo_text": "NOVA", "logo_count": 1, "logo_placement": "corner"},
            "[vbase]",
            "Sans",
        )
        self.assertIn("w-text_w-40", nova_graph)
        self.assertIn("box=1", nova_graph)

        self.assertFalse(duration_fits(self.flp, 34.7)[0])
        self.assertTrue(duration_fits(self.flp, 12)[0])
        self.assertTrue(duration_fits(dishpilot, 34.7)[0])
        self.assertFalse(duration_fits(dishpilot, 70)[0])

    def test_single_corner_logo_never_two_center_logos(self) -> None:
        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        for slot in manifest["slots"]:
            for side in ("x", "youtube_shorts"):
                badge = slot["overlay"][side]
                self.assertLessEqual(badge["logo_count"], 1)
                self.assertIn(badge["logo_placement"], ("corner", "none"))
                self.assertNotEqual(badge["logo_placement"], "center")
                self.assertFalse(badge["logo_subtext"])
        graph, _label = ffmpeg_banner_filter(
            {"logo_text": "DISHPILOT", "logo_subtext": "SECOND", "logo_count": 1, "logo_placement": "corner"},
            "[vbase]",
            "Sans",
        )
        self.assertEqual(graph.count("drawtext"), 1)
        self.assertIn("text='Dishpilot'", graph)
        self.assertIn("fontcolor=white@0.42", graph)
        self.assertNotIn("box=", graph)
        self.assertNotIn("SECOND", graph)
        cleaned = dishpilot_clip_filter(
            "60,000 PEOPLE IN SYNC",
            fps=30,
            width=1080,
            height=1920,
            clean_bottom_strip=True,
        )
        self.assertIn("crop=1080:1720:0:0", cleaned)
        self.assertIn("text='Dishpilot'", cleaned)
        self.assertNotIn("drawbox", cleaned)
        self.assertIn("60\\,000", cleaned)

    def test_dishpilot_x_captions_have_zero_loan_text(self) -> None:
        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        dishpilot_slots = [slot for slot in manifest["slots"] if slot["x_campaign"] == "dishpilot-clipping"]
        self.assertGreaterEqual(len(dishpilot_slots), 1)
        for slot in dishpilot_slots:
            self.assertEqual(slot["x_handle"], "@chadriyo")
            self.assertIn(slot["hook"], slot["x_caption"])
            tags = re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["x_caption"])
            self.assertGreaterEqual(len(tags), 1)
            self.assertLessEqual(len(tags), 2)
            self.assertNotRegex(slot["x_caption"], LOAN_SPAM)
            self.assertNotIn("FastLoanPilot", slot["x_caption"])
            self.assertNotIn("http", slot["x_caption"].lower())

    def test_youtube_shorts_are_at_least_80_percent_indian(self) -> None:
        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        indian = [
            slot
            for slot in manifest["slots"]
            if slot["youtube_category"] in INDIAN_CATEGORIES
        ]
        self.assertGreaterEqual(len(indian), 8)
        self.assertEqual(len(manifest["slots"]), 10)
        self.assertGreaterEqual(len(indian) / len(manifest["slots"]), 0.8)
        self.assertTrue(all(slot["youtube_handle"] == "Denzen" for slot in indian))
        self.assertTrue(all(slot["youtube_username"] == "@denzen0" for slot in indian))
        global_slots = [slot for slot in manifest["slots"] if slot not in indian]
        self.assertEqual(len(global_slots), 2)
        self.assertTrue(all(slot["youtube_campaign"] == "fast-loan-pilot" for slot in global_slots))
        locked = {"funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert"}
        self.assertTrue(all(slot["x_category"] in locked for slot in manifest["slots"]))
        self.assertEqual({slot["x_category"] for slot in manifest["slots"]}, locked)

    def test_schedule_x_slots_3_to_10_use_dishpilot_files(self) -> None:
        schedule = json.loads(Path("whop/schedule_10x10.json").read_text(encoding="utf-8"))
        slots = schedule["x_slots"]
        self.assertEqual(len(slots), 10)
        self.assertEqual(len(schedule["youtube_slots"]), 10)
        expected = {
            3: ("whop/v/x-dishpilot-3.mp4", "THIS ANIMATION IS INSANE"),
            4: ("whop/v/x-dishpilot-4.mp4", "WAIT FOR THE DROP"),
            5: ("whop/v/x-dishpilot-5.mp4", "HIS REACTION AT THE END"),
            6: ("whop/v/x-dishpilot-6.mp4", "60,000 PEOPLE IN SYNC"),
            7: ("whop/v/x-dishpilot-7.mp4", "WHEN THE BEAT DROPS"),
            8: ("whop/v/x-dishpilot-8.mp4", "WAIT FOR THE REVEAL"),
            9: ("whop/v/x-dishpilot-9.mp4", "NYC DOORMAN PLOT TWIST"),
            10: ("whop/v/x-dishpilot-10.mp4", "NOBODY EXPECTED THIS"),
        }
        for slot in slots:
            number = slot["slot"]
            if number < 3:
                continue
            path, hook = expected[number]
            self.assertEqual(slot["campaign"], "dishpilot-clipping")
            self.assertEqual(slot["handle"], "@chadriyo")
            self.assertEqual(slot["badge"], "Dishpilot")
            self.assertEqual(slot["logo_text"], "Dishpilot")
            self.assertEqual(slot["logo_count"], 1)
            self.assertEqual(slot["logo_placement"], "corner")
            self.assertEqual(slot["hook"], hook)
            self.assertEqual(slot["mp4"], path)
            self.assertTrue(slot["mp4_url"].endswith(path.split("/")[-1]))
            self.assertIn(hook, slot["caption"])
            self.assertIn("#Dishpilot", slot["caption"])
            self.assertLessEqual(len(re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["caption"])), 2)
            self.assertNotRegex(slot["caption"], LOAN_SPAM)
            self.assertTrue(Path(path).is_file())


if __name__ == "__main__":
    unittest.main()
