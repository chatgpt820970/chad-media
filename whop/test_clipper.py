"""Platform mapping: X (@chadriyo) and YouTube Shorts (Denzen) only hit campaigns that pay them."""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from whop.clipper import (
    BUFFER_ORGANIZATION_ID,
    CAMPAIGN_MARK,
    DAILY_30_INDIAN_SLOTS,
    DAILY_30_PER_PLATFORM,
    DAILY_30_TIMES_IST,
    DAILY_CHANNEL_IDS,
    DAILY_CLIP_CATALOG,
    SUPPORTED_PLATFORMS,
    FEED_JSON_PATH,
    INDIAN_CATEGORIES as CLIPPER_INDIAN_CATEGORIES,
    INGEST_CRF,
    INGEST_SHARPEN,
    PRIORITY_CATEGORIES,
    YTDLP_FORMAT,
    MANIFEST_PATH,
    PLATFORM_ELIGIBLE_CAMPAIGNS,
    CampaignPlatformError,
    banner_overlay_spec,
    assert_feed_item_campaign_identity,
    bind_clip_to_campaign,
    buffer_create_post_body,
    build_daily_30,
    build_daily_40,
    build_daily_40_schedule,
    build_daily_manifest,
    build_ingest_items,
    build_platform_caption,
    campaigns_for_platform,
    check_community_guidelines_safe,
    check_campaign_pool_guard,
    community_guidelines_violations,
    daily_feed_is_current,
    download_attempt_plan,
    duration_fits,
    extract_mp4,
    ingest_ffmpeg_command,
    dishpilot_clip_filter,
    ffmpeg_banner_filter,
    load_campaigns,
    overlay_plan,
    parse_source_url,
    priority_rank,
    resolve_clean_mp4_url,
    select_campaign_for_platform,
    single_campaign_drawtext,
    verify_manifest_data,
    verify_slot_campaign_identity,
    _catalog_clip,
)
from whop import whop_clipper as runner
from whop.whop_clipper import schedule_unscheduled_items, should_publish_daily

X_PAYING_IDS = {"dishpilot-clipping"}
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

    def test_fastloanpilot_is_closed_and_not_selected(self) -> None:
        self.assertEqual(self.flp["status"], "closed")
        self.assertEqual(self.flp["product_id"], "prod_DyuwRHNCz3vpv")
        self.assertEqual(self.flp["experience_id"], "exp_cqVzB3tgaTR5tB")
        self.assertNotIn("x", self.flp["accepted_platforms"])

        with self.assertRaises(CampaignPlatformError) as caught:
            bind_clip_to_campaign(self.flp, "x")
        self.assertIn("@chadriyo", str(caught.exception))
        self.assertIn("fast-loan-pilot", str(caught.exception))

        closed_ok, closed_reason = check_campaign_pool_guard(self.flp, self.guard)
        self.assertFalse(closed_ok)
        self.assertIn("closed", closed_reason)

        youtube = select_campaign_for_platform(self.data, "youtube_shorts")
        self.assertEqual(youtube["id"], "dishpilot-clipping")
        self.assertIn("youtube_shorts", youtube["accepted_platforms"])
        self.assertEqual(youtube["rules"]["logo_text"], "dishpilot")
        routed = [campaign["id"] for campaign in campaigns_for_platform(self.data, "youtube_shorts")]
        self.assertEqual(
            routed,
            ["dishpilot-clipping", "rascals-theme-pages", "mr-pa-clipping"],
        )
        self.assertNotIn("fast-loan-pilot", routed)

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
        self.assertEqual(paying_ids, ["dishpilot-clipping"])
        best = max(paying, key=lambda campaign: campaign["remaining_pool_usd"])
        self.assertEqual(chosen["id"], best["id"])
        for campaign in paying:
            self.assertIn("x", campaign["accepted_platforms"])
            passed, detail = check_campaign_pool_guard(campaign, self.guard)
            self.assertTrue(passed, detail)

    def test_explicit_platform_eligibility_matrix_and_zero_mix_up(self) -> None:
        self.assertEqual(
            PLATFORM_ELIGIBLE_CAMPAIGNS,
            {
                "dishpilot-clipping": ("x", "youtube", "instagram", "tiktok"),
                "rascals-theme-pages": ("instagram", "tiktok", "youtube"),
                "mr-pa-clipping": ("youtube", "instagram", "tiktok"),
            },
        )
        by_id = {row["id"]: row for row in self.data["active_campaigns"]}
        with self.assertRaises(CampaignPlatformError):
            bind_clip_to_campaign(by_id["rascals-theme-pages"], "x")
        with self.assertRaises(CampaignPlatformError):
            bind_clip_to_campaign(by_id["mr-pa-clipping"], "x")
        self.assertEqual(
            [row["id"] for row in campaigns_for_platform(self.data, "x")],
            ["dishpilot-clipping"],
        )
        self.assertEqual(
            {row["id"] for row in campaigns_for_platform(self.data, "youtube_shorts")},
            set(PLATFORM_ELIGIBLE_CAMPAIGNS),
        )
        self.assertEqual(
            {row["id"] for row in campaigns_for_platform(self.data, "instagram_reels")},
            set(PLATFORM_ELIGIBLE_CAMPAIGNS),
        )
        self.assertEqual(
            {row["id"] for row in campaigns_for_platform(self.data, "tiktok")},
            set(PLATFORM_ELIGIBLE_CAMPAIGNS),
        )

        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        slot9 = dict(manifest["slots"][8])
        slot9["overlay"] = dict(slot9["overlay"])
        slot9["overlay"]["youtube_shorts"] = dict(
            slot9["overlay"]["youtube_shorts"],
            logo_text="dishpilot",
        )
        slot9["youtube_caption"] = f"{slot9['hook']}\n\n#Dishpilot #Shorts"
        broken = dict(manifest, slots=[*manifest["slots"][:8], slot9, manifest["slots"][9]])
        errors = verify_manifest_data(broken, self.data)
        self.assertTrue(any("youtube_caption" in error for error in errors), errors)
        self.assertTrue(any("overlay.youtube_shorts.logo_text" in error for error in errors), errors)

        rascals_on_x = dict(manifest["slots"][0], x_campaign="rascals-theme-pages")
        rascals_on_x["x_caption"] = f"{rascals_on_x['hook']}\n\n#Rascals"
        rascals_on_x["overlay"] = dict(rascals_on_x["overlay"])
        rascals_on_x["overlay"]["x"] = dict(
            rascals_on_x["overlay"]["x"],
            logo_text="rascals",
        )
        errors = verify_slot_campaign_identity(rascals_on_x, self.data)
        self.assertTrue(any("PLATFORM_ELIGIBLE_CAMPAIGNS" in error for error in errors), errors)

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
        self.assertEqual(
            manifest["platform_eligible_campaigns"],
            {
                campaign_id: list(platforms)
                for campaign_id, platforms in PLATFORM_ELIGIBLE_CAMPAIGNS.items()
            },
        )

        x_ids = {slot["x_campaign"] for slot in manifest["slots"]}
        self.assertEqual(x_ids, X_PAYING_IDS)
        self.assertTrue(all(slot["x_campaign"] == "dishpilot-clipping" for slot in manifest["slots"]))
        self.assertEqual(manifest["slots"][0]["instagram_campaign"], "rascals-theme-pages")
        self.assertEqual(manifest["slots"][0]["instagram_rate_per_1k_views_usd"], 3.0)
        self.assertEqual(manifest["slots"][1]["instagram_campaign"], "dishpilot-clipping")

        locked_x = {"funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert"}
        for slot in manifest["slots"]:
            self.assertIn(slot["youtube_campaign"], ("dishpilot-clipping", "mr-pa-clipping"))
            self.assertEqual(slot["youtube_handle"], "Denzen")
            self.assertEqual(slot["youtube_username"], "@denzen0")
            self.assertIn(slot["hook"], slot["youtube_caption"])
            expected_yt_tags = (
                ["#MrPa", "#Shorts"]
                if slot["youtube_campaign"] == "mr-pa-clipping"
                else ["#Dishpilot", "#Shorts"]
            )
            self.assertEqual(re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["youtube_caption"]), expected_yt_tags)
            self.assertNotRegex(slot["youtube_caption"], LOAN_SPAM)
            self.assertIn(slot["x_campaign"], X_PAYING_IDS)
            self.assertEqual(slot["x_handle"], "@chadriyo")
            self.assertEqual(slot["x_rate_per_1k_views_usd"], 2.0)
            self.assertEqual(slot["x_badge"], "dishpilot")
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
            self.assertNotEqual(slot["x_campaign"], "nova-x-clipping")
            self.assertTrue(slot["x_submission_url"].startswith("https://"))
            self.assertEqual(slot["overlay"]["x"]["logo_text"], "dishpilot")
            yt_mark = "mr. pa" if slot["youtube_campaign"] == "mr-pa-clipping" else "dishpilot"
            self.assertEqual(slot["overlay"]["youtube_shorts"]["logo_text"], yt_mark)
            self.assertEqual(slot["overlay"]["mode"], "platform_specific_variants")
            self.assertEqual(slot["overlay"]["instagram_reels"]["logo_text"], {"dishpilot-clipping": "dishpilot", "rascals-theme-pages": "rascals", "mr-pa-clipping": "mr. pa"}[slot["instagram_campaign"]])
            self.assertEqual(
                slot["overlay"]["tiktok"]["logo_text"],
                CAMPAIGN_MARK[slot["tiktok_campaign"]][0],
            )
            self.assertNotIn("·", slot["overlay"]["instagram_reels"]["logo_text"])
            for side in ("x", "youtube_shorts", "instagram_reels", "tiktok"):
                badge = slot["overlay"][side]
                self.assertEqual(badge["logo_count"], 1)
                self.assertEqual(badge["logo_placement"], "corner")
                self.assertEqual(badge["logo_subtext"], "")
            if slot["youtube_category"] in INDIAN_CATEGORIES:
                self.assertEqual(slot["youtube_campaign"], "dishpilot-clipping")
                self.assertEqual(slot["youtube_rate_per_1k_views_usd"], 2.0)
            else:
                self.assertEqual(slot["youtube_campaign"], "mr-pa-clipping")
                self.assertEqual(slot["youtube_rate_per_1k_views_usd"], 1.25)
                self.assertEqual(slot["youtube_remaining_pool_usd"], 8507.0)
            tags = re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["x_caption"])
            self.assertEqual(tags, ["#Dishpilot"])
            self.assertNotIn("FastLoanPilot", slot["x_caption"])
            self.assertEqual(slot["instagram_handle"], "@benreelr")
            self.assertEqual(
                re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["instagram_caption"]),
                {"dishpilot-clipping": ["#Dishpilot", "#Reels"], "rascals-theme-pages": ["#Rascals", "#Reels"], "mr-pa-clipping": ["#MrPa", "#Reels"]}[slot["instagram_campaign"]],
            )
            self.assertIn(slot["instagram_campaign"], ("rascals-theme-pages", "dishpilot-clipping"))
            self.assertTrue(slot["instagram_duration_ok"])
            self.assertEqual(slot["instagram_duration_sec"], 15.0)
            self.assertEqual(slot["tiktok_handle"], "@bi67325")
            self.assertTrue(slot["tiktok_duration_ok"])
            self.assertEqual(slot["tiktok_duration_sec"], 15.0)
            self.assertEqual(
                re.findall(r"#[A-Za-z][A-Za-z0-9_]*", slot["tiktok_caption"]),
                [CAMPAIGN_MARK[slot["tiktok_campaign"]][1]],
            )
            self.assertEqual(verify_slot_campaign_identity(slot, self.data), [])

        on_disk = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["slots"], manifest["slots"])
        self.assertEqual(verify_manifest_data(on_disk, self.data), [])
        feed = json.loads(FEED_JSON_PATH.read_text(encoding="utf-8"))
        self.assertEqual(feed["items"], manifest["slots"])
        self.assertTrue(all(item["youtube_duration_ok"] for item in feed["items"]))
        self.assertTrue(all(str(item["x_mp4_url"]).endswith((".mp4",)) for item in feed["items"]))

    def test_nova_is_closed_because_its_app_only_returns_ok(self) -> None:
        nova = next(campaign for campaign in self.data["active_campaigns"] if campaign["id"] == "nova-x-clipping")
        cravin = next(campaign for campaign in self.data["active_campaigns"] if campaign["id"] == "cravin-x-clipping")
        self.assertEqual(nova["status"], "closed")
        self.assertEqual(nova["whop_app_id"], "app_JsryI0fVLVpeYi")
        self.assertEqual(nova["whop_product_id"], "prod_3juzE1FqAXBsj")
        nova_ok, nova_reason = check_campaign_pool_guard(nova, self.guard)
        self.assertFalse(nova_ok)
        self.assertIn("closed", nova_reason)
        routed = [campaign["id"] for campaign in campaigns_for_platform(self.data, "x")]
        self.assertNotIn("nova-x-clipping", routed)
        self.assertNotIn("cravin-x-clipping", routed)

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
        rascals = next(
            campaign
            for campaign in self.data["active_campaigns"]
            if campaign["id"] == "rascals-theme-pages"
        )
        plan = overlay_plan(rascals, dishpilot)
        self.assertEqual(plan["mode"], "platform_specific_variants")
        self.assertEqual(plan["x"]["logo_text"], "dishpilot")
        self.assertEqual(plan["x"]["logo_subtext"], "")
        self.assertEqual(plan["x"]["logo_count"], 1)
        self.assertEqual(plan["x"]["logo_placement"], "corner")
        self.assertEqual(plan["youtube_shorts"]["logo_text"], "rascals")
        self.assertEqual(plan["youtube_shorts"]["logo_count"], 1)
        self.assertEqual(plan["youtube_shorts"]["logo_placement"], "corner")

        same = {
            "logo_path": "",
            "rules": {"logo_text": "SAME", "logo_subtext": "Sub", "logo_overlay_required": True},
        }
        self.assertEqual(overlay_plan(same, same)["mode"], "dual_compatible")
        self.assertEqual(overlay_plan(same, same)["x"]["logo_count"], 1)
        self.assertEqual(overlay_plan(same, same)["x"]["logo_subtext"], "")

        graph, label = ffmpeg_banner_filter(banner_overlay_spec(dishpilot), "[vbase]", "Sans")
        self.assertIn("text='dishpilot'", graph)
        self.assertIn("fontcolor=white@0.58", graph)
        self.assertIn("shadowx=1:shadowy=1", graph)
        self.assertIn("x=(w-text_w)/2:y=h-text_h-180", graph)
        self.assertNotIn("Dishpilot.com", graph)
        self.assertNotIn("box=", graph)
        self.assertNotIn("drawbox", graph)
        self.assertEqual(graph.count("drawtext"), 1)
        self.assertEqual(label, "[vbadge]")
        reels_graph, _reels_label = ffmpeg_banner_filter(banner_overlay_spec(rascals), "[vbase]", "Sans")
        self.assertIn("text='rascals'", reels_graph)
        self.assertIn("x=(w-text_w)/2:y=h-text_h-180", reels_graph)
        self.assertNotIn("box=", reels_graph)
        self.assertEqual(reels_graph.count("drawtext"), 1)

        self.assertTrue(duration_fits(dishpilot, 12.5)[0])
        self.assertTrue(duration_fits(dishpilot, 30)[0])
        self.assertFalse(duration_fits(dishpilot, 70)[0])
        self.assertTrue(duration_fits(rascals, 15)[0])

    def test_single_corner_logo_never_two_center_logos(self) -> None:
        manifest = build_daily_manifest(self.data, generated_at="2026-10-24T00:00:00Z")
        for slot in manifest["slots"]:
            for side in ("x", "youtube_shorts", "instagram_reels"):
                badge = slot["overlay"][side]
                self.assertEqual(badge["logo_count"], 1)
                self.assertEqual(badge["logo_placement"], "corner")
                self.assertNotEqual(badge["logo_placement"], "center")
                self.assertFalse(badge["logo_subtext"])
        graph, _label = ffmpeg_banner_filter(
            {"logo_text": "dishpilot", "logo_subtext": "SECOND", "logo_count": 1, "logo_placement": "corner"},
            "[vbase]",
            "Sans",
        )
        self.assertEqual(graph.count("drawtext"), 1)
        self.assertIn("text='dishpilot'", graph)
        self.assertIn("fontcolor=white@0.58", graph)
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
        self.assertIn("text='dishpilot'", cleaned)
        self.assertIn("x=(w-text_w)/2:y=h-text_h-180", cleaned)
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
            self.assertEqual(tags, ["#Dishpilot"])
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
        self.assertTrue(all(slot["youtube_campaign"] == "mr-pa-clipping" for slot in global_slots))
        self.assertTrue(all(slot["youtube_campaign"] == "dishpilot-clipping" for slot in indian))
        locked = {"funny", "ai", "knowledge", "movie", "anime", "celebrity", "concert"}
        self.assertTrue(all(slot["x_category"] in locked for slot in manifest["slots"]))
        self.assertEqual({slot["x_category"] for slot in manifest["slots"]}, locked)

    def test_schedule_x_slots_3_to_10_use_dishpilot_files(self) -> None:
        schedule = json.loads(Path("whop/schedule_10x10.json").read_text(encoding="utf-8"))
        slots = schedule["x_slots"]
        self.assertEqual(len(slots), 10)
        self.assertEqual(len(schedule["youtube_slots"]), 10)
        expected = {
            3: ("whop/v/x-user-1.mp4", "THIS ANIMATION IS INSANE"),
            4: ("whop/v/x-user-2.mp4", "WAIT FOR THE DROP"),
            5: ("whop/v/x-user-3.mp4", "HIS REACTION AT THE END"),
            6: ("whop/v/x-user-4.mp4", "60,000 PEOPLE IN SYNC"),
            7: ("whop/v/x-user-5.mp4", "WHEN THE BEAT DROPS"),
            8: ("whop/v/x-user-6.mp4", "WAIT FOR THE REVEAL"),
            9: ("whop/v/x-user-7.mp4", "NYC DOORMAN PLOT TWIST"),
            10: ("whop/v/x-user-8.mp4", "NOBODY EXPECTED THIS"),
        }
        for slot in slots:
            number = slot["slot"]
            if number < 3:
                continue
            path, hook = expected[number]
            self.assertEqual(slot["campaign"], "dishpilot-clipping")
            self.assertEqual(slot["handle"], "@chadriyo")
            self.assertEqual(slot["badge"], "dishpilot")
            self.assertEqual(slot["logo_text"], "dishpilot")
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


class DailyThirtyPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_campaigns()
        self.day = date(2026, 10, 24)
        self.feed, self.seen = build_daily_30(self.data, {"ids": [], "uses": {}}, today=self.day)
        self.items = self.feed["items"]

    def _platform(self, platform: str) -> list[dict]:
        return [item for item in self.items if item["platform"] == platform]

    def test_build_daily_30_selects_thirty_pool_guarded_clips(self) -> None:
        self.assertEqual(self.feed["date"], "2026-10-24")
        expected = DAILY_30_PER_PLATFORM * len(SUPPORTED_PLATFORMS)
        self.assertEqual(expected, 40)
        self.assertEqual(self.feed["count"], expected)
        self.assertEqual(len(self.items), expected)
        self.assertEqual(len(self._platform("x")), 10)
        self.assertEqual(len(self._platform("youtube_shorts")), 10)
        self.assertEqual(len(self._platform("instagram_reels")), 10)
        self.assertEqual(len(self._platform("tiktok")), 10)
        self.assertEqual(len({item["clip_id"] for item in self.items}), expected)
        self.assertEqual(len(self.seen["ids"]), expected)

        youtube = self._platform("youtube_shorts")
        indian = [item for item in youtube if item["category"] in CLIPPER_INDIAN_CATEGORIES]
        self.assertGreaterEqual(len(indian), DAILY_30_INDIAN_SLOTS)
        self.assertGreaterEqual(len(indian) / len(youtube), 0.8)
        self.assertTrue(all(item["campaign_id"] == "dishpilot-clipping" for item in indian))
        global_slots = [item for item in youtube if item not in indian]
        self.assertEqual(len(global_slots), 2)
        self.assertTrue(all(item["campaign_id"] == "dishpilot-clipping" for item in global_slots))

        rotation = ("rascals-theme-pages", "dishpilot-clipping")
        for item in self.items:
            self.assertIn(item["slot"], range(1, 11))
            self.assertEqual(item["channel_id"], DAILY_CHANNEL_IDS[item["platform"]])
            self.assertEqual(item["watermark_position"]["placement"], "top-left")
            self.assertEqual(item["watermark_position"]["expr"], "x=(w-text_w)/2:y=h-text_h-180")
            self.assertIn("x=(w-text_w)/2:y=h-text_h-180", json.dumps(item["watermark_position"]))
            self.assertTrue(item["video_url"].startswith("https://"))
            self.assertTrue(item["video_url"].endswith(".mp4"))
            self.assertEqual(item["buffer_status"], "pending")
            campaign = next(
                row for row in self.data["active_campaigns"] if row["id"] == item["campaign_id"]
            )
            ok, reason = check_campaign_pool_guard(campaign, self.data["pool_guard_rules"])
            self.assertTrue(ok, reason)
            self.assertIn(item["platform"], campaign["accepted_platforms"])
            self.assertNotIn("fast-loan-pilot", item["campaign_id"])
            self.assertNotIn("nova-x-clipping", item["campaign_id"])
            blob = json.dumps(item)
            self.assertNotIn("FastLoanPilot", blob)
            self.assertNotRegex(blob, LOAN_SPAM)
            self.assertIn(item["title"], item["caption"])

        x_items = self._platform("x")
        self.assertTrue(all(item["campaign_id"] == "dishpilot-clipping" for item in x_items))
        self.assertTrue(all(item["watermark_text"] == "dishpilot" for item in x_items))
        self.assertTrue(all(item["watermark_text"] == "dishpilot" for item in indian))
        self.assertTrue(all(item["watermark_text"] == "dishpilot" for item in global_slots))
        reels = self._platform("instagram_reels")
        for item in reels:
            self.assertEqual(item["watermark_text"], {"dishpilot-clipping": "dishpilot", "rascals-theme-pages": "rascals"}[item["campaign_id"]])
        locks = {clip["id"]: clip["campaign_lock"] for clip in DAILY_CLIP_CATALOG if clip.get("campaign_lock")}
        self.assertEqual(
            [item["campaign_id"] for item in reels],
            [locks.get(item["clip_id"], rotation[(item["slot"] - 1) % 2]) for item in reels],
        )
        for item in x_items:
            self.assertEqual(re.findall(r"#[A-Za-z][A-Za-z0-9_]*", item["caption"]), ["#Dishpilot"])
        for item in youtube:
            self.assertEqual(
                re.findall(r"#[A-Za-z][A-Za-z0-9_]*", item["caption"]), ["#Dishpilot", "#Shorts"]
            )
        tiktok = self._platform("tiktok")
        self.assertEqual(
            [item["campaign_id"] for item in tiktok],
            [
                locks.get(item["clip_id"], "dishpilot-clipping" if item["slot"] % 2 else "rascals-theme-pages")
                for item in tiktok
            ],
        )
        self.assertEqual(tiktok[0]["channel_id"], DAILY_CHANNEL_IDS["tiktok"])
        for item in self.items:
            tags = re.findall(r"#[A-Za-z][A-Za-z0-9_]*", item["caption"])
            campaign_tags = [tag for tag in tags if tag in ("#Dishpilot", "#Rascals", "#MrPa")]
            self.assertEqual(len(campaign_tags), 1)
            self.assertIn(item["watermark_text"], ("dishpilot", "rascals"))
            self.assertIn(campaign_tags[0], ("#Dishpilot", "#Rascals"))
            self.assertNotIn("·", item["watermark_text"])
            self.assertNotIn("rascals · dishpilot", json.dumps(item))
        for item in reels:
            self.assertEqual(
                re.findall(r"#[A-Za-z][A-Za-z0-9_]*", item["caption"]),
                {"dishpilot-clipping": ["#Dishpilot", "#Reels"], "rascals-theme-pages": ["#Rascals", "#Reels"]}[item["campaign_id"]],
            )

        self.assertEqual(
            [item["scheduled_time_ist"] for item in x_items],
            list(DAILY_30_TIMES_IST),
        )
        stamps = [
            datetime.strptime(item["due_at_utc"], "%Y-%m-%dT%H:%M:%SZ")
            for item in x_items
        ]
        deltas = [(later - earlier).total_seconds() for earlier, later in zip(stamps, stamps[1:])]
        self.assertEqual(deltas, [7200] * 9)
        self.assertTrue(all(item["due_at_utc"].startswith("2026-10-24T") for item in self.items))
        self.assertTrue(all(item["due_at_utc"].endswith("Z") for item in self.items))

    def test_daily_40_schedule_has_ten_slots_per_platform(self) -> None:
        schedule = build_daily_40_schedule(self.feed, self.data)
        self.assertEqual(schedule["total_posts"], 40)
        self.assertEqual(set(schedule["slots"]), {"x", "youtube", "instagram", "tiktok"})
        for key, rows in schedule["slots"].items():
            self.assertEqual([row["slot"] for row in rows], list(range(1, 11)), key)
        expected_channels = {
            "x": "6ac41eb06a5c39ccb625fdf1",
            "youtube": "6ac4da806a5c39ccb62eab64",
            "instagram": "6ac93d936a5c39ccb66624d7",
            "tiktok": "6add07e4778b240e2fb35110",
        }
        self.assertEqual(schedule["channels"], expected_channels)
        for key, rows in schedule["slots"].items():
            self.assertTrue(all(row["channel_id"] == expected_channels[key] for row in rows))
            self.assertTrue(all(row["watermark_text"] in ("dishpilot", "rascals") for row in rows))
        self.assertEqual(build_daily_40(self.data, {"ids": [], "uses": {}}, today=self.day)[0], self.feed)

        extra = dict(self.items[0], clip_id="ingest-extra-x", source="ingest")
        with_extra = dict(self.feed, items=[*self.items, extra])
        self.assertEqual(build_daily_40_schedule(with_extra, self.data)["total_posts"], 40)
        self.assertEqual(len(build_daily_40_schedule(with_extra, self.data)["ingested"]), 1)
        self.assertTrue(daily_feed_is_current(with_extra, self.day))
        no_tiktok = dict(self.feed, items=[item for item in self.items if item["platform"] != "tiktok"])
        self.assertFalse(daily_feed_is_current(no_tiktok, self.day))
        with self.assertRaises(CampaignPlatformError):
            build_daily_40_schedule(no_tiktok, self.data)
        mr_pa = dict(self.items[0], watermark_text="mr. pa")
        with self.assertRaises(CampaignPlatformError):
            build_daily_40_schedule(dict(self.feed, items=[mr_pa, *self.items[1:]]), self.data)

        with tempfile.TemporaryDirectory() as tmp:
            out40 = Path(tmp) / "daily_40_ready.json"
            out30 = Path(tmp) / "daily_30_ready.json"
            outfeed = Path(tmp) / "feed_daily_40_ready.json"
            with mock.patch.object(runner, "DAILY_40_SCHEDULE_PATH", out40), mock.patch.object(
                runner, "DAILY_30_SCHEDULE_PATH", out30
            ), mock.patch.object(runner, "DAILY_40_FEED_PATH", outfeed):
                paths = runner.write_daily_schedules(self.feed, self.data)
            self.assertEqual(paths, [out40, out30, outfeed])
            self.assertEqual(json.loads(outfeed.read_text(encoding="utf-8"))["count"], 40)
            written = json.loads(out40.read_text(encoding="utf-8"))
            self.assertEqual(written["total_posts"], 40)
            self.assertEqual(json.loads(out30.read_text(encoding="utf-8")), written)
            self.assertEqual({key: len(rows) for key, rows in written["slots"].items()}, {"x": 10, "youtube": 10, "instagram": 10, "tiktok": 10})

    def test_burned_in_marks_lock_clips_to_their_campaign(self) -> None:
        locks = {clip["id"]: clip["campaign_lock"] for clip in DAILY_CLIP_CATALOG if clip.get("campaign_lock")}
        self.assertEqual(locks["spiderman-4k-dishpilot"], "dishpilot-clipping")
        self.assertEqual({locks[f"x-user-{n}"] for n in (1, 2, 3, 4, 5)}, {"dishpilot-clipping"})
        self.assertEqual({locks[f"x-user-{n}"] for n in (6, 7, 8, 9)}, {"rascals-theme-pages"})
        day = date(2026, 10, 25)
        seen = {"ids": [], "uses": {}}
        for offset in range(8):
            feed, seen = build_daily_30(self.data, seen, today=day + timedelta(days=offset))
            for item in feed["items"]:
                if item["clip_id"] in locks:
                    self.assertEqual(item["campaign_id"], locks[item["clip_id"]])
                    self.assertTrue(item["pre_stamped"])
                    if locks[item["clip_id"]] == "rascals-theme-pages":
                        self.assertIn(item["platform"], ("instagram_reels", "tiktok"))
                else:
                    self.assertFalse(item["pre_stamped"])
        spider = next(item for item in self.items if item["clip_id"] == "spiderman-4k-dishpilot")
        self.assertEqual(spider["watermark_text"], "dishpilot")
        self.assertTrue(spider["video_url"].endswith("spiderman-4k-dishpilot.mp4"))

    def test_daily_render_stamps_one_mark_and_never_sends_unhosted_clips(self) -> None:
        items = [dict(item) for item in self.items]
        feed = dict(self.feed, items=items)
        commands: list[list[str]] = []
        broken = next(item for item in items if not item["pre_stamped"])
        broken_id = broken["clip_id"]

        def fake_download(url: str, dest: Path) -> None:
            if broken_id in str(dest):
                raise OSError("down")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")

        def fake_fallback(url: str, dest: Path) -> None:
            raise RuntimeError("yt-dlp failed")

        gh_calls: list[list[str]] = []

        def fake_gh(command, **_kwargs):
            gh_calls.append(list(command))
            code = 1 if command[1:3] == ["release", "view"] else 0
            out = json.dumps([{"tagName": "daily-2026-10-20"}, {"tagName": "daily-2026-10-24"}, {"tagName": "v1"}])
            return mock.Mock(returncode=code, stdout=out if command[2] == "list" else "")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(runner, "REPO_ROOT", root), mock.patch.object(
                runner, "WORK_DIR", root / "work"
            ), mock.patch.object(runner, "DAILY_RENDER_DIR", root / "work" / "daily_render"):
                rendered = runner.render_daily_items(
                    feed,
                    self.day,
                    download_file=fake_download,
                    fallback_download=fake_fallback,
                    run_ffmpeg=commands.append,
                )
                self.assertTrue(all(item["render_status"] == "rendered_local" for item, _path in rendered))
                hosted = runner.upload_daily_renders(
                    rendered, self.day, runner=fake_gh, env={"GITHUB_REPOSITORY": "chatgpt820970/chad-media"}
                )
                removed = runner.prune_daily_releases(self.day, runner=fake_gh)
        self.assertEqual(hosted, len(rendered))
        self.assertEqual(removed, ["daily-2026-10-20"])
        self.assertEqual(gh_calls[0][:3], ["gh", "release", "view"])
        self.assertTrue(any(call[:3] == ["gh", "release", "create"] for call in gh_calls))
        self.assertTrue(any(call[:3] == ["gh", "release", "upload"] and "--clobber" in call for call in gh_calls))
        self.assertTrue(any(call[:3] == ["gh", "release", "delete"] and "daily-2026-10-20" in call for call in gh_calls))
        self.assertFalse(any("daily-2026-10-24" in call for call in gh_calls if call[2] == "delete"))

        stamped = [item for item in items if item.get("render_status") == "rendered"]
        self.assertEqual(len(stamped), len(rendered))
        self.assertEqual(len(commands), len(rendered))
        self.assertEqual(broken["render_status"], "failed")
        for item in stamped:
            self.assertTrue(
                item["video_url"].startswith(
                    "https://github.com/chatgpt820970/chad-media/releases/download/daily-2026-10-24/"
                )
            )
            self.assertTrue(item["source_video_url"])
        for command in commands:
            joined = " ".join(command)
            self.assertEqual(command[command.index("-crf") + 1], "16")
            self.assertIn("unsharp=3:3:0.6:3:3:0.0", joined)
            self.assertEqual(joined.count("drawtext"), 1)
            self.assertIn("x=(w-text_w)/2:y=h-text_h-180", joined)
            self.assertNotIn("mr. pa", joined)
        for item in items:
            if item["pre_stamped"]:
                self.assertNotIn("render_status", item)
                self.assertNotIn("source_video_url", item)

        local = dict(stamped[0], render_status="rendered_local")
        posted: list[dict] = []
        scheduled = schedule_unscheduled_items(
            {"items": [broken, local, stamped[0]]},
            "token",
            poster=lambda body: posted.append(body) or (True, "post"),
        )
        self.assertEqual(scheduled, 1)
        self.assertEqual(len(posted), 1)
        self.assertEqual(broken["buffer_status"], "pending")

    def test_seen_ids_do_not_repeat_until_the_catalog_is_used(self) -> None:
        indian_ids = [
            clip["id"] for clip in DAILY_CLIP_CATALOG if clip["category"] in CLIPPER_INDIAN_CATEGORIES
        ]
        day1_indian = [
            item["clip_id"]
            for item in self._platform("youtube_shorts")
            if item["category"] in CLIPPER_INDIAN_CATEGORIES
        ]
        self.assertEqual(day1_indian, indian_ids[:DAILY_30_INDIAN_SLOTS])
        unused = indian_ids[DAILY_30_INDIAN_SLOTS:]
        self.assertGreaterEqual(len(unused), 1)

        feed2, seen2 = build_daily_30(self.data, self.seen, today=date(2026, 10, 25))
        day2_items = feed2["items"]
        self.assertEqual(len({item["clip_id"] for item in day2_items}), DAILY_30_PER_PLATFORM * len(SUPPORTED_PLATFORMS))
        day1_ids = {item["clip_id"] for item in self.items}
        day2_ids = {item["clip_id"] for item in day2_items}
        self.assertTrue(unused)
        self.assertTrue(set(unused).isdisjoint(day1_ids))
        day2_indian = [
            item["clip_id"]
            for item in day2_items
            if item["platform"] == "youtube_shorts" and item["category"] in CLIPPER_INDIAN_CATEGORIES
        ]
        self.assertEqual(day2_indian[: len(unused)], unused)
        self.assertEqual(day2_indian[len(unused) :], indian_ids[: DAILY_30_INDIAN_SLOTS - len(unused)])
        self.assertEqual(len(seen2["ids"]), len(day1_ids | day2_ids))
        self.assertTrue(daily_feed_is_current(self.feed, self.day))
        self.assertFalse(daily_feed_is_current(self.feed, date(2026, 10, 25)))

    def test_buffer_body_and_publish_gate(self) -> None:
        sample = self._platform("instagram_reels")[0]
        before_slot = datetime(2026, 10, 24, 0, 0, tzinfo=timezone.utc)
        body = buffer_create_post_body(sample, now=before_slot)
        self.assertEqual(body["channelId"], DAILY_CHANNEL_IDS["instagram_reels"])
        self.assertEqual(body["schedulingType"], "automatic")
        self.assertEqual(body["mode"], "customScheduled")
        self.assertTrue(body["dueAt"].endswith("+00:00"))
        self.assertEqual(body["assets"][0]["video"]["url"], sample["video_url"])
        self.assertEqual(body["metadata"]["instagram"]["type"], "reel")
        self.assertNotIn("token", json.dumps(body).lower())
        self.assertNotIn("FastLoanPilot", json.dumps(body))

        late = buffer_create_post_body(sample, now=datetime(2026, 10, 25, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(late["mode"], "addToQueue")
        self.assertNotIn("dueAt", late)
        self.assertEqual(late["schedulingType"], "automatic")
        for platform in SUPPORTED_PLATFORMS:
            posted = buffer_create_post_body(self._platform(platform)[0], now=before_slot)
            self.assertEqual(posted["channelId"], DAILY_CHANNEL_IDS[platform])
            self.assertEqual(posted["schedulingType"], "automatic")

        youtube = buffer_create_post_body(self._platform("youtube_shorts")[0], now=before_slot)
        self.assertEqual(youtube["metadata"]["youtube"]["categoryId"], "24")
        self.assertNotIn("metadata", buffer_create_post_body(self._platform("x")[0], now=before_slot))

        self.assertFalse(should_publish_daily({}))
        self.assertFalse(
            should_publish_daily(
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "pull_request",
                    "GITHUB_REF_NAME": "main",
                }
            )
        )
        self.assertFalse(
            should_publish_daily(
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "schedule",
                    "GITHUB_REF_NAME": "main",
                    "WHOP_SKIP_PUBLISH": "1",
                }
            )
        )
        self.assertFalse(
            should_publish_daily(
                {
                    "WHOP_PUBLISH_DAILY": "1",
                    "GITHUB_REF_NAME": "cursor/daily-30-whop-pipeline-6d20",
                }
            )
        )
        self.assertTrue(
            should_publish_daily(
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "schedule",
                    "GITHUB_REF_NAME": "main",
                }
            )
        )
        self.assertTrue(
            should_publish_daily(
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_EVENT_NAME": "workflow_dispatch",
                    "GITHUB_REF_NAME": "main",
                }
            )
        )

    def test_workflow_schedules_the_daily_build_after_tests(self) -> None:
        text = Path(".github/workflows/whop-clipper.yml").read_text(encoding="utf-8")
        self.assertIn('cron: "15 0 * * *"', text)
        self.assertIn('cron: "15 1 * * *"', text)
        self.assertIn("workflow_dispatch:", text)
        self.assertIn("contents: write", text)
        self.assertIn("python -m unittest whop.test_clipper -v", text)
        self.assertIn("python whop/clipper.py verify-manifest", text)
        self.assertIn("python whop/whop_clipper.py build-daily-40", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertLess(text.index("unittest whop.test_clipper"), text.index("verify-manifest"))
        self.assertLess(text.index("verify-manifest"), text.index("build-daily-40"))
        self.assertIn("github.event_name == 'schedule' || github.event_name == 'workflow_dispatch'", text)
        self.assertIn("source_urls:", text)
        self.assertIn("ingest-urls", text)
        self.assertIn("6add07e4778b240e2fb35110", json.dumps(self.data))
        self.assertEqual(BUFFER_ORGANIZATION_ID, "6ac41da09a0b5aca7c671500")


class IngestUrlPipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_campaigns()
        self.day = date(2026, 10, 24)
        self.feed, _seen = build_daily_30(self.data, {"ids": [], "uses": {}}, today=self.day)
        self.items = self.feed["items"]

    def _platform(self, platform: str) -> list[dict]:
        return [item for item in self.items if item["platform"] == platform]

    def test_classifies_x_and_tiktok_links(self) -> None:
        status = parse_source_url("https://x.com/chadriyo/status/2110725157686063105")
        self.assertEqual(status["kind"], "x")
        self.assertEqual(status["id"], "2110725157686063105")
        twitter = parse_source_url("https://twitter.com/someone/status/99")
        self.assertEqual(twitter["kind"], "x")
        tiktok = parse_source_url("https://www.tiktok.com/@bi67325/video/1234567890123456789")
        self.assertEqual(tiktok["kind"], "tiktok")
        self.assertEqual(tiktok["id"], "1234567890123456789")
        short = parse_source_url("https://vm.tiktok.com/ZMabc123/")
        self.assertEqual(short["kind"], "tiktok")
        self.assertEqual(short["id"], "ZMabc123")

    def test_four_platform_community_guidelines_safety_filter(self) -> None:
        allowed = (
            "Attractive glamorous couple in a romantic movie scene",
            "4K anime edit",
            "Top 4K movie scene",
        )
        for title in allowed:
            with self.subTest(title=title):
                self.assertTrue(
                    check_community_guidelines_safe(
                        title=title,
                        hook="Wait for the reveal",
                        tags=("#Anime4K", "#Movie4K"),
                        source_metadata={"nsfw": False, "rating": "general"},
                    )
                )
        self.assertTrue(
            check_community_guidelines_safe(
                {
                    "title": "Glamorous romantic scene",
                    "caption": "4K movie edit #Movie4K",
                    "category": "movie_4k",
                }
            )
        )

        blocked = (
            "NSFW edit",
            "18+ uncensored",
            "explicit nudity",
            "graphic violence compilation",
            "gory beheading scene",
            "hate speech",
            "terrorist propaganda",
            "self-harm tutorial",
            "fake giveaway phishing",
        )
        for title in blocked:
            with self.subTest(title=title):
                self.assertFalse(check_community_guidelines_safe(title=title))
                self.assertTrue(community_guidelines_violations(title=title))

        self.assertFalse(
            check_community_guidelines_safe(
                title="Ordinary title",
                source_metadata={"moderation": {"nsfw": True}},
            )
        )
        self.assertFalse(
            check_community_guidelines_safe(
                title="Ordinary title",
                source_metadata={"safety": {"graphic_violence_score": 0.2}},
            )
        )
        self.assertTrue(
            check_community_guidelines_safe(
                title="Romantic scene",
                source_metadata={"nsfw": False, "graphic_violence_score": 0},
            )
        )

    def test_unsafe_catalog_feed_and_buffer_posts_fail_closed(self) -> None:
        with self.assertRaisesRegex(CampaignPlatformError, "Community Guidelines"):
            _catalog_clip(
                ("unsafe", "movie_4k", "Explicit nudity scene", "https://example.com/unsafe.mp4")
            )
        with self.assertRaisesRegex(CampaignPlatformError, "Community Guidelines"):
            build_ingest_items(
                self.data,
                source_url="https://x.com/a/status/42",
                source_id="unsafe-42",
                video_url="https://example.com/unsafe.mp4",
                campaign_alias="dishpilot",
                platforms="x",
                hook="Watch this",
                today=self.day,
                category="movie_4k",
                source_metadata={"moderation": {"adult_content": True}},
            )

        unsafe = dict(self.items[0])
        unsafe["title"] = "NSFW 18+"
        unsafe["caption"] = "NSFW 18+ #Dishpilot"
        unsafe_feed = {
            "date": self.day.isoformat(),
            "items": [unsafe, *self.items[1:]],
        }
        self.assertFalse(daily_feed_is_current(unsafe_feed, self.day))
        with self.assertRaisesRegex(CampaignPlatformError, "Community Guidelines"):
            buffer_create_post_body(unsafe)
        calls = []
        with self.assertRaisesRegex(CampaignPlatformError, "Community Guidelines"):
            schedule_unscheduled_items(
                {"items": [unsafe]},
                "token",
                poster=lambda body: calls.append(body) or (True, "post"),
            )
        self.assertEqual(calls, [])

    def test_download_chain_prefers_clean_apis(self) -> None:
        x_plan = download_attempt_plan(parse_source_url("https://x.com/a/status/42"))
        self.assertEqual([step["via"] for step in x_plan], ["vxtwitter", "fixupx", "ytdlp"])
        tiktok_plan = download_attempt_plan(parse_source_url("https://www.tiktok.com/@a/video/7"))
        self.assertEqual(tiktok_plan[0]["via"], "tikwm")
        self.assertIn("tikwm.com/api/", tiktok_plan[0]["url"])
        self.assertEqual(tiktok_plan[-1]["via"], "ytdlp")
        found = resolve_clean_mp4_url(
            "https://x.com/a/status/42",
            lambda _url: {
                "media_extended": [
                    {"type": "video", "url": "https://video.twimg.com/low.mp4", "bitrate": 100},
                    {"type": "video", "url": "https://video.twimg.com/high.mp4", "bitrate": 900},
                ]
            },
        )
        self.assertEqual(found, "https://video.twimg.com/high.mp4")
        hd = extract_mp4(
            "tikwm",
            {"data": {"play": "https://cdn.example/a.mp4", "hdplay": "https://cdn.example/hd.mp4"}},
        )
        self.assertEqual(hd, "https://cdn.example/hd.mp4")

    def test_one_campaign_watermark_queues_paid_platforms(self) -> None:
        with self.assertRaises(CampaignPlatformError):
            single_campaign_drawtext("Sans", "rascals · dishpilot")
        draw = single_campaign_drawtext("Sans", "dishpilot")
        self.assertIn("text='dishpilot'", draw)
        self.assertIn("fontcolor=white@0.78", draw)
        self.assertIn("box=1:boxcolor=black@0.28:boxborderw=8", draw)
        self.assertIn("x=(w-text_w)/2:y=h-text_h-180", draw)
        self.assertEqual(draw.count("drawtext"), 1)

        commands = [
            ingest_ffmpeg_command(Path("in.mp4"), Path(f"out-{platform}.mp4"), platform, "rascals", "Sans")
            for platform in SUPPORTED_PLATFORMS
        ]
        filters = [command[command.index("-vf") + 1] for command in commands]
        self.assertEqual(len(set(filters)), 4)
        for command in commands:
            joined = " ".join(command)
            self.assertIn("-crf", command)
            self.assertEqual(command[command.index("-crf") + 1], "16")
            self.assertEqual(INGEST_CRF, "16")
            self.assertIn("flags=lanczos", joined)
            self.assertIn("unsharp=3:3:0.6:3:3:0.0", joined)
            self.assertEqual(INGEST_SHARPEN, "unsharp=3:3:0.6:3:3:0.0")
            self.assertIn("1080:1920", joined)
            self.assertIn("text='rascals'", joined)
            self.assertNotIn("·", joined)
            self.assertNotIn("dishpilot", joined)

        items = build_ingest_items(
            self.data,
            source_url="https://x.com/a/status/42",
            source_id="42",
            video_url="https://chatgpt820970.github.io/chad-media/whop/v/ingest-42-x.mp4",
            campaign_alias="dishpilot",
            platforms="all",
            hook="Wait for the drop",
            today=date(2026, 10, 24),
        )
        self.assertEqual({item["platform"] for item in items}, set(SUPPORTED_PLATFORMS))
        self.assertTrue(all(item["watermark_text"] == "dishpilot" for item in items))
        self.assertTrue(all(item["campaign_id"] == "dishpilot-clipping" for item in items))
        self.assertTrue(all(item["channel_id"] == DAILY_CHANNEL_IDS[item["platform"]] for item in items))
        self.assertTrue(all(item["source"] == "ingest" for item in items))

        rascals = build_ingest_items(
            self.data,
            source_url="https://www.tiktok.com/@a/video/7",
            source_id="7",
            video_url="https://example.com/r.mp4",
            campaign_alias="rascals",
            platforms="x,instagram_reels,youtube_shorts,tiktok",
            hook="Wait for the drop",
            today=date(2026, 10, 24),
        )
        self.assertEqual(
            {item["platform"] for item in rascals},
            {"youtube_shorts", "instagram_reels", "tiktok"},
        )
        self.assertTrue(all(item["watermark_text"] == "rascals" for item in rascals))
        self.assertTrue(all("#Rascals" in item["caption"] and "#Dishpilot" not in item["caption"] for item in rascals))

        mrpa = build_ingest_items(
            self.data,
            source_url="https://x.com/a/status/42",
            source_id="42b",
            video_url="https://example.com/m.mp4",
            campaign_alias="mrpa",
            platforms="all",
            hook="Wait for the drop",
            today=date(2026, 10, 24),
        )
        self.assertEqual(
            {item["platform"] for item in mrpa},
            {"youtube_shorts", "instagram_reels", "tiktok"},
        )
        self.assertTrue(all(item["watermark_text"] == "mr. pa" for item in mrpa))
        self.assertTrue(all("#MrPa" in item["caption"] and "#Dishpilot" not in item["caption"] for item in mrpa))
        crossed = dict(items[0], watermark_text="rascals")
        with self.assertRaises(CampaignPlatformError):
            assert_feed_item_campaign_identity(crossed, self.data)

    def test_highest_resolution_stream_wins(self) -> None:
        picked = extract_mp4(
            "vxtwitter",
            {
                "media_extended": [
                    {"url": "https://video.twimg.com/a/vid/avc1/1280x720/lo.mp4", "bitrate": 2_000_000},
                    {"url": "https://video.twimg.com/a/vid/avc1/3840x2160/uhd.mp4", "bitrate": 800_000},
                    {"url": "https://video.twimg.com/a/vid/avc1/1920x1080/hd.mp4", "bitrate": 9_000_000},
                ]
            },
        )
        self.assertEqual(picked, "https://video.twimg.com/a/vid/avc1/3840x2160/uhd.mp4")
        sized = extract_mp4(
            "vxtwitter",
            {
                "media_extended": [
                    {"url": "https://cdn.example/a30.mp4", "size": {"width": 1920, "height": 1080}, "fps": 30},
                    {"url": "https://cdn.example/a60.mp4", "size": {"width": 1920, "height": 1080}, "fps": 60},
                ]
            },
        )
        self.assertEqual(sized, "https://cdn.example/a60.mp4")
        fx = extract_mp4(
            "fixupx",
            {
                "tweet": {
                    "media": {
                        "videos": [
                            {"url": "https://cdn.example/f720.mp4", "width": 720, "height": 1280},
                            {"url": "https://cdn.example/f1080.mp4", "width": 1080, "height": 1920},
                        ]
                    }
                }
            },
        )
        self.assertEqual(fx, "https://cdn.example/f1080.mp4")
        self.assertEqual(YTDLP_FORMAT, "bestvideo+bestaudio/best")
        tiktok_plan = download_attempt_plan(parse_source_url("https://www.tiktok.com/@a/video/7"))
        self.assertIn("hd=1", tiktok_plan[0]["url"])

    def test_priority_categories_lead_the_daily_catalog(self) -> None:
        self.assertEqual(PRIORITY_CATEGORIES[0], "romantic_couples")
        self.assertLess(priority_rank("romantic_couples"), priority_rank("glamorous_girls"))
        self.assertLess(priority_rank("glamorous_girls"), priority_rank("anime_4k"))
        self.assertLess(priority_rank("anime_4k"), priority_rank("movie_4k"))
        self.assertLess(priority_rank("movie_4k"), priority_rank("funny"))
        categories = {clip["category"] for clip in DAILY_CLIP_CATALOG}
        self.assertTrue({"romantic_couples", "glamorous_girls", "anime_4k", "movie_4k"} <= categories)
        for platform in ("x", "instagram_reels", "tiktok"):
            ranks = [priority_rank(item["category"]) for item in self._platform(platform)]
            self.assertEqual(ranks, sorted(ranks), platform)
            self.assertTrue(self._platform(platform)[0]["priority"])
        self.assertEqual(self._platform("x")[0]["category"], "romantic_couples")
        tagged = build_ingest_items(
            self.data,
            source_url="https://x.com/a/status/42",
            source_id="42c",
            video_url="https://example.com/c.mp4",
            campaign_alias="dishpilot",
            platforms="all",
            hook="Wait for the drop",
            today=self.day,
            category="anime_4k",
        )
        self.assertTrue(all(item["category"] == "anime_4k" and item["priority"] for item in tagged))
        with self.assertRaises(CampaignPlatformError):
            build_ingest_items(
                self.data,
                source_url="https://x.com/a/status/42",
                source_id="42d",
                video_url="https://example.com/d.mp4",
                campaign_alias="dishpilot",
                platforms="x",
                hook="Wait",
                today=self.day,
                category="nonsense",
            )

    def test_every_daily_item_carries_one_campaign_mark_and_channel(self) -> None:
        for item in self.items:
            self.assertIn(item["watermark_text"], {"dishpilot", "rascals", "mr. pa"})
            self.assertEqual(item["watermark_position"]["x"], "w*0.025")
            self.assertEqual(item["watermark_position"]["y"], "h*0.03")
            self.assertEqual(item["channel_id"], DAILY_CHANNEL_IDS[item["platform"]])


if __name__ == "__main__":
    unittest.main()
