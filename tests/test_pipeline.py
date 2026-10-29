"""Dry-run rules: inactive campaigns, warning-only views line, slots, safety, captions."""

from __future__ import annotations

import copy
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from clipper.buffer_api import ErrorBudget, create_post, should_stop_platform
from clipper.fetchsrc import (
    _needs_cookies,
    download_command,
    download_saved,
    drive_bytes_from_headers,
    dropbox_direct,
    youtube_attempts,
)
from clipper.submissions import empty_pending, record_sent_posts
from clipper.engine import (
    build_caption,
    min_slot_gap_minutes,
    quiet_gap_minutes,
    rejection,
    rotated_hashtags,
    upcoming_slots,
    views_to_first_payout,
)
from clipper.loadconfig import check_bundle, load_bundle, posting_platforms
from clipper.render import ffmpeg_available, logo_filter, render_color
from clipper.run import run_pipeline
from clipper.safety import classify
from clipper.watermark import resolve_watermark, synthetic_frame

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 27, 0, 0, tzinfo=timezone.utc)


_X_PLATFORMS = {"boxabl": ["x"], "t-bird": ["x"]}


def _bundle() -> dict:
    """Live config with X turned back on so the X code paths stay covered.

    Production config has X off (removed from the Whop pipeline Oct 29 2026);
    see test_x_removed_from_live_config.
    """
    bundle = load_bundle(ROOT)
    bundle["channels"]["platforms"]["x"]["enabled"] = True
    for row in bundle["campaigns"]["campaigns"]:
        if row.get("id") in _X_PLATFORMS and "x" not in (row.get("platforms") or []):
            row["platforms"] = ["x"] + list(row.get("platforms") or [])
    return bundle


class XRemovedTests(unittest.TestCase):
    def test_x_removed_from_live_config(self):
        from clipper.loadconfig import posting_platforms
        live = load_bundle(ROOT)
        self.assertNotIn("x", posting_platforms(live))
        for row in live["campaigns"]["campaigns"]:
            self.assertNotIn("x", row.get("platforms") or [])


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
        self.assertEqual(active, ["boxabl", "dhan-aziz", "sui-basecamp"])
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

    def test_instagram_is_disabled_and_the_other_platforms_stay_on(self):
        bundle = _bundle()
        self.assertFalse(bundle["channels"]["platforms"]["instagram"]["enabled"])
        self.assertEqual(posting_platforms(bundle), ["x", "youtube", "tiktok"])
        self.assertEqual(
            bundle["channels"]["platforms"]["x"]["buffer_channel_id"],
            "6ac41eb06a5c39ccb625fdf1",
        )
        self.assertEqual(
            bundle["channels"]["platforms"]["youtube"]["buffer_channel_id"],
            "6ac4da806a5c39ccb62eab64",
        )
        self.assertEqual(
            bundle["channels"]["platforms"]["instagram"]["buffer_channel_id"],
            "6ac93d936a5c39ccb66624d7",
        )
        self.assertEqual(
            bundle["channels"]["platforms"]["tiktok"]["buffer_channel_id"],
            "6add07e4778b240e2fb35110",
        )
        turned_on = copy.deepcopy(bundle)
        turned_on["channels"]["platforms"]["instagram"]["enabled"] = True
        self.assertEqual(posting_platforms(turned_on), ["x", "youtube", "instagram", "tiktok"])
        missing = copy.deepcopy(bundle)
        del missing["channels"]["platforms"]["x"]["enabled"]
        self.assertIn("x", posting_platforms(missing))

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
        self.assertEqual(report["active_campaigns"], ["sui-basecamp", "dhan-aziz", "boxabl"])
        self.assertIn("content plan", report["posting_note"])
        self.assertIn("awaiting_approval", report["posting_note"])
        self.assertIn("Instagram is disabled", report["posting_note"])
        self.assertEqual(report["posts_per_day"], {"x": 10, "youtube": 10, "instagram": 0, "tiktok": 10})
        sui = [row for row in report["packages"] if row.get("campaign_id") == "sui-basecamp" and row["status"] == "scheduled"]
        self.assertEqual(sorted({row["platform"] for row in sui}), ["tiktok", "youtube"])
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
        self.assertEqual({row["platform"] for row in drafts}, {"youtube"})
        self.assertFalse(any(
            row["platform"] == "instagram" and row["status"] in {"scheduled", "reminder", "awaiting_approval"}
            for row in report["packages"]
        ))
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
        self.assertEqual(sorted(row["platform"] for row in posted), ["youtube"])
        for row in posted:
            self.assertIn("@DHANAZIZ", row["caption"])
            self.assertFalse(row["logo_applied"])
            self.assertEqual(row["filter_graph"], "copy")
            self.assertEqual(row["variant"], {"crop_x": 0.0, "start_sec": 0.0})
            self.assertNotEqual(row["buffer"]["metadata"]["youtube"]["privacy"], "private")
        bundle["channels"]["platforms"]["instagram"]["enabled"] = True
        restored = run_pipeline(bundle, now=NOW)
        restored_posts = [
            row for row in restored["packages"]
            if row.get("campaign_id") == "dhan-aziz" and row["status"] == "scheduled"
        ]
        self.assertEqual(sorted(row["platform"] for row in restored_posts), ["instagram", "youtube"])

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
        bundle = _activate(_bundle(), "jackelyne")
        bundle["channels"]["platforms"]["instagram"]["enabled"] = True
        report = run_pipeline(bundle, now=NOW, env={})
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
            self.assertGreaterEqual(min_slot_gap_minutes(spec["slots"]), 90)
            self.assertEqual(min_slot_gap_minutes(spec["slots"]), 135)
        self.assertEqual(schedule["youtube"]["slots"], schedule["instagram"]["slots"])
        self.assertEqual(schedule["x"]["slots"], schedule["youtube"]["slots"])
        self.assertEqual(schedule["tiktok"]["slots"], schedule["youtube"]["slots"])
        slots = upcoming_slots(NOW, schedule["youtube"]["slots"], "Asia/Kolkata", 10)
        self.assertEqual(len(slots), 10)
        self.assertEqual(slots[0].astimezone(__import__("zoneinfo").ZoneInfo("Asia/Kolkata")).strftime("%H:%M"), "07:00")

    def test_slots_closer_than_90_minutes_fail_check(self):
        bundle = _bundle()
        bundle["pipeline"]["schedule"]["x"]["slots"] = [f"{hour:02d}:00" for hour in range(10)]
        errors = check_bundle(bundle)
        self.assertTrue(any("90" in error and error.startswith("x ") for error in errors))


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

    def test_drive_warning_page_size(self):
        html = '<span class="uc-name-size"><a href="/open?id=abc">Adeniyi - Clip 1.mp4</a> (156M)</span>'
        self.assertEqual(drive_bytes_from_headers("text/html", "2416", html), 156 * 1024 * 1024)
        self.assertEqual(drive_bytes_from_headers("text/html", "10", "(1.5GB)"), int(1.5 * 1024**3))
        self.assertEqual(drive_bytes_from_headers("video/mp4", "52029715", ""), 52029715)
        self.assertIsNone(drive_bytes_from_headers("text/html", "2416", "<html>no size</html>"))

    def test_ytdlp_limit_and_youtube_clients(self):
        self.assertTrue(download_saved(0, True))
        self.assertTrue(download_saved(101, True))
        self.assertFalse(download_saved(101, False))
        self.assertFalse(download_saved(1, True))
        attempts = youtube_attempts("ytsearch1:cricket match highlight India", "out.mp4", None)
        self.assertEqual(len(attempts), 3)
        self.assertIn("youtube:player_client=tv_downgraded", attempts[0])
        self.assertNotIn("--cookies", attempts[0])
        self.assertNotIn("visionos", " ".join(attempts[0]))
        self.assertTrue(attempts[0][-1].startswith("ytsearch1:"))
        with_cookies = youtube_attempts("https://www.youtube.com/watch?v=abc", "out.mp4", "/tmp/yt.txt")
        self.assertEqual(len(with_cookies), 3)
        self.assertIn("youtube:player_client=web", with_cookies[0])
        self.assertNotIn("visionos", " ".join(with_cookies[0]))
        self.assertIn("--cookies", with_cookies[1])
        self.assertIn("/tmp/yt.txt", with_cookies[1])
        self.assertNotIn("secret-cookie-value", " ".join(with_cookies[1]))
        self.assertTrue(_needs_cookies("Sign in to confirm you’re not a bot"))
        self.assertFalse(_needs_cookies("HTTP Error 404"))


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

    def test_sent_post_starts_the_clock_immediately(self):
        pending = empty_pending("https://example.invalid/pending.json", 30)
        campaigns = _bundle()["campaigns"]["campaigns"]
        sent = {
            "id": "buf-sui",
            "status": "sent",
            "sentAt": "2026-10-27T00:00:00.000Z",
            "dueAt": "2026-10-26T12:00:00.000Z",
            "externalLink": "https://www.youtube.com/shorts/example",
            "text": "#Ad\nSui BaseCamp live\nx.com/SuiNetwork",
        }
        scheduled = {
            "id": "buf-later",
            "status": "scheduled",
            "dueAt": "2026-10-27T02:00:00.000Z",
            "externalLink": "",
            "text": "x.com/SuiNetwork",
        }
        plan = {
            "id": "buf-plan",
            "status": "sent",
            "sentAt": "2026-10-27T00:05:00.000Z",
            "externalLink": "https://www.youtube.com/shorts/plan",
            "text": "4K anime sakuga",
        }
        added = record_sent_posts(pending, [sent, scheduled, plan, sent], "youtube", campaigns, 30)
        self.assertEqual(added, 1)
        item = pending["items"][0]
        self.assertEqual(item["posted_at"], "2026-10-27T00:00:00.000Z")
        self.assertEqual(item["deadline_at"], "2026-10-27T00:30:00.000Z")
        self.assertEqual(item["campaign_id"], "sui-basecamp")
        self.assertEqual(item["status"], "pending")


class LivePostTests(unittest.TestCase):
    def _dirs(self):
        import tempfile

        root = Path(tempfile.mkdtemp())
        media = root / "fetch"
        media.mkdir()
        return root, media, root / "out"

    def _file(self, folder: Path, name: str) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / name
        path.write_bytes(b"\x00" * 32)
        return path

    def _manifest(self, media: Path, rows: list[dict]) -> None:
        (media / "manifest.json").write_text(
            json.dumps({"sui": {}, "content_plan": rows}) + "\n",
            encoding="utf-8",
        )

    def _run(self, bundle, media, out, rows, poster, now=NOW, ledger=None, alerts=None, env=None, fetcher=None):
        from clipper.livepost import execute_live

        self._manifest(media, rows)
        calls = []

        def uploader(path, name):
            calls.append((Path(path).name, name))
            return f"https://github.com/chatgpt820970/chad-media/releases/download/clip-media/{name}"

        code = execute_live(
            bundle,
            now=now,
            env=env or {"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=uploader,
            poster=poster,
            fetcher=fetcher if fetcher is not None else (lambda exclude: None),
            ledger_path=ledger or (media.parent / "live.json"),
            pending_path=media.parent / "pending.json",
            alerts_path=alerts or (media.parent / "alerts.json"),
        )
        return code, calls

    def test_due_slots_follow_the_ist_clocks(self):
        from clipper.buffer_api import due_stamp
        from clipper.livepost import due_slots

        schedule = _bundle()["pipeline"]["schedule"]
        for plat in ("x", "youtube", "tiktok"):
            self.assertEqual(
                [due_stamp(slot) for slot in due_slots(NOW, schedule[plat]["slots"], "Asia/Kolkata")],
                ["2026-10-27T01:30:00.000Z"],
            )
        later = datetime(2026, 10, 27, 6, 0, tzinfo=timezone.utc)
        for plat in ("x", "youtube", "tiktok"):
            self.assertEqual(
                [due_stamp(slot) for slot in due_slots(later, schedule[plat]["slots"], "Asia/Kolkata")],
                ["2026-10-27T06:00:00.000Z"],
            )

    def test_live_posts_enabled_platforms_and_skips_instagram(self):
        from clipper.run import _channel_id

        bundle = _bundle()
        root, media, out = self._dirs()
        files = {
            "x": self._file(media, "anime-x.mp4"),
            "youtube": self._file(media, "cricket-yt.mp4"),
            "tiktok": self._file(media, "anime-tt.mp4"),
        }
        rows = [
            {"platform": name, "status": "downloaded", "path": str(path)}
            for name, path in files.items()
        ]
        seen = []

        def poster(payload):
            seen.append(payload)
            return {"id": f"buf-{len(seen)}", "status": "scheduled", "text": payload["text"]}

        code, _calls = self._run(bundle, media, out, rows, poster)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 3)
        channels = {item["channelId"] for item in seen}
        self.assertNotIn(bundle["channels"]["platforms"]["instagram"]["buffer_channel_id"], channels)
        youtube = next(item for item in seen if item["channelId"].endswith("2eab64"))
        self.assertEqual(youtube["mode"], "customScheduled")
        self.assertEqual(youtube["dueAt"], "2026-10-27T01:30:00.000Z")
        self.assertIn("#shorts", youtube["text"])
        self.assertEqual(youtube["metadata"]["youtube"]["categoryId"], "24")
        self.assertNotIn("example.invalid", json.dumps(seen))
        self.assertTrue(all(not item["text"].startswith("#Ad") for item in seen))
        self.assertTrue(all("x.com/SuiNetwork" not in item["text"] for item in seen))
        tiktok = next(item for item in seen if "tiktok" in item.get("metadata", {}))
        self.assertTrue(tiktok["metadata"]["tiktok"]["title"])
        self.assertEqual(
            _channel_id(bundle["channels"], {"account_mode": "shared"}, "x", {"BUFFER_CHANNEL_X": ""}),
            "6ac41eb06a5c39ccb625fdf1",
        )
        ledger = json.loads((root / "live.json").read_text())
        self.assertEqual(len(ledger["sources"]), 3)
        self.assertTrue(all(item["platform"] != "instagram" for item in ledger["posted"]))

    def test_same_source_is_not_posted_twice_on_one_platform(self):
        from clipper.livepost import prefetch_exclude_ids, source_key

        bundle = _bundle()
        root, media, out = self._dirs()
        first = self._file(media, "clip-a.mp4")
        second = self._file(media, "clip-b.mp4")
        rows = [
            {"platform": "youtube", "status": "downloaded", "path": str(first)},
            {"platform": "youtube", "status": "downloaded", "path": str(second)},
        ]
        seen = []

        def poster(payload):
            seen.append(payload["text"])
            return {"id": f"buf-{len(seen)}", "status": "scheduled", "text": payload["text"]}

        code, _calls = self._run(bundle, media, out, rows, poster)
        self.assertEqual(code, 0)
        ledger_path = root / "live.json"
        ledger = json.loads(ledger_path.read_text())
        ledger["posted"] = []
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        code, _calls = self._run(bundle, media, out, rows, poster, ledger=ledger_path)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        ledger = json.loads(ledger_path.read_text())
        self.assertIn(source_key("youtube", "clip-a"), ledger["sources"])
        self.assertIn(source_key("youtube", "clip-b"), ledger["sources"])
        ledger["posted"] = []
        ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
        code, _calls = self._run(bundle, media, out, rows, poster, ledger=ledger_path)
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 2)
        self.assertEqual(
            prefetch_exclude_ids(
                {"sources": ["youtube:fileA", "tiktok:fileA", "youtube:fileB"]},
                ["youtube", "tiktok"],
            ),
            {"fileA"},
        )

    def test_earliest_slot_wins_and_a_ledger_hit_is_skipped(self):
        from clipper.buffer_api import due_stamp
        from clipper.livepost import due_slots, empty_ledger, slot_key

        bundle = _bundle()
        bundle["pipeline"]["schedule"]["x"]["slots"] = ["05:45", "07:00", "12:00", "13:00", "14:00", "15:00", "16:00", "17:00", "18:00", "19:00"]
        root, media, out = self._dirs()
        path = self._file(media, "anime-x.mp4")
        rows = [{"platform": "x", "status": "downloaded", "path": str(path)}]
        # Only X has a file. YouTube and TikTok skip. Give X two in-window slots.
        seen = []

        def poster(payload):
            seen.append(payload["dueAt"])
            return {"id": "buf", "status": "scheduled", "text": payload["text"]}

        code, _calls = self._run(bundle, media, out, rows, poster)
        self.assertEqual(code, 0)
        self.assertEqual(seen, ["2026-10-27T00:15:00.000Z"])
        slots = due_slots(NOW, bundle["pipeline"]["schedule"]["x"]["slots"], "Asia/Kolkata")
        self.assertEqual([due_stamp(slot) for slot in slots], ["2026-10-27T00:15:00.000Z", "2026-10-27T01:30:00.000Z"])
        ledger_path = root / "live.json"
        code, _calls = self._run(bundle, media, out, rows, poster, ledger=ledger_path)
        self.assertEqual(seen, ["2026-10-27T00:15:00.000Z"])
        stored = json.loads(ledger_path.read_text())
        self.assertTrue(any(row["key"] == slot_key("x", slots[0]) for row in stored["posted"]))
        self.assertEqual(empty_ledger()["sources"], [])

    def test_explicit_title_is_skipped_without_pausing(self):
        bundle = _bundle()
        root, media, out = self._dirs()
        bad = self._file(media, "explicit-clip.mp4")
        good = self._file(media, "cricket-highlight.mp4")
        rows = [
            {"platform": "youtube", "status": "downloaded", "path": str(bad)},
            {"platform": "youtube", "status": "downloaded", "path": str(good)},
        ]
        seen = []

        def poster(payload):
            seen.append(payload)
            return {"id": "buf", "status": "scheduled", "text": payload["text"]}

        code, _calls = self._run(bundle, media, out, rows, poster, ledger=root / "live.json")
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        ledger = json.loads((root / "live.json").read_text())
        self.assertIn("youtube:cricket-highlight", ledger["sources"])
        self.assertNotIn("youtube:explicit-clip", ledger["sources"])
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})
        self.assertTrue(any(row["kind"] == "safety" for row in alerts["alerts"]))
        self.assertIn("youtube:explicit-clip", alerts["blocked_sources"])
        code, _calls = self._run(bundle, media, out, rows, poster, alerts=root / "alerts.json", ledger=root / "live.json")
        self.assertEqual(len(seen), 1)

    def test_buffer_error_pauses_the_platform_and_a_removed_post_does_too(self):
        from clipper.buffer_api import BufferError
        from clipper.strikes import record_post_statuses

        bundle = _bundle()
        root, media, out = self._dirs()
        path = self._file(media, "anime-x.mp4")
        rows = [{"platform": "x", "status": "downloaded", "path": str(path)}]

        def poster(payload):
            raise BufferError("429 Too Many Requests")

        code, _calls = self._run(bundle, media, out, rows, poster)
        self.assertEqual(code, 1)
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertIn("x", alerts["paused"])
        self.assertEqual(alerts["paused"]["x"]["kind"], "buffer_error")
        self.assertTrue(alerts["alerts"])
        code, _calls = self._run(bundle, media, out, rows, poster, alerts=root / "alerts.json")
        self.assertEqual(code, 0)
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(len([row for row in alerts["alerts"] if row["kind"] == "buffer_error"]), 1)

        state = {"version": 1, "paused": {}, "alerts": [], "blocked_sources": []}
        notes = record_post_statuses(
            state,
            "tiktok",
            [{"id": "gone", "status": "removed"}, {"id": "ok", "status": "sent"}],
            NOW,
        )
        self.assertEqual(len(notes), 1)
        self.assertIn("gone", notes[0])
        self.assertIn("tiktok", state["paused"])
        self.assertEqual(record_post_statuses(state, "tiktok", [{"id": "gone", "status": "removed"}], NOW), [])

    def test_example_invalid_never_reaches_the_poster(self):
        bundle = _bundle()
        _root, media, out = self._dirs()
        path = self._file(media, "anime-x.mp4")
        rows = [{"platform": "x", "status": "downloaded", "path": str(path)}]
        seen = []

        def poster(payload):
            seen.append(payload)
            return {"id": "buf", "status": "scheduled", "text": payload["text"]}

        def uploader(path, name):
            return "https://example.invalid/clip.mp4"

        from clipper.livepost import execute_live

        self._manifest(media, rows)
        code = execute_live(
            bundle,
            now=NOW,
            env={"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=uploader,
            poster=poster,
            fetcher=lambda exclude: None,
            ledger_path=media.parent / "live.json",
            pending_path=media.parent / "pending.json",
            alerts_path=media.parent / "alerts.json",
        )
        self.assertEqual(code, 1)
        self.assertEqual(seen, [])
        alerts = json.loads((media.parent / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})

    def test_past_slot_is_share_now_and_graphql_stays_current(self):
        from clipper.buffer_api import CREATE_POST, build_post_input
        from clipper.livepost import live_payload

        future = {
            "platform": "youtube",
            "status": "scheduled",
            "caption": "Sui BaseCamp live\n#shorts",
            "due_at": "2026-10-27T01:30:00.000Z",
        }
        payload = live_payload(future, "channel", "https://github.com/chatgpt820970/chad-media/releases/download/clip-media/a.mp4", NOW)
        self.assertEqual(payload["mode"], "customScheduled")
        self.assertEqual(payload["schedulingType"], "automatic")
        past = dict(future)
        past["due_at"] = "2026-10-26T23:50:00.000Z"
        payload = live_payload(past, "channel", "https://github.com/chatgpt820970/chad-media/releases/download/clip-media/a.mp4", NOW)
        self.assertEqual(payload["mode"], "shareNow")
        self.assertNotIn("dueAt", payload)
        tiktok = build_post_input(
            {"platform": "tiktok", "status": "scheduled", "caption": "First line\n#movies", "due_at": future["due_at"]},
            "channel",
            "https://github.com/chatgpt820970/chad-media/releases/download/clip-media/a.mp4",
        )
        self.assertEqual(tiktok["metadata"]["tiktok"]["title"], "First line")
        instagram = build_post_input(
            {"platform": "instagram", "status": "scheduled", "caption": "Reel line", "due_at": future["due_at"]},
            "channel",
            "https://github.com/chatgpt820970/chad-media/releases/download/clip-media/a.mp4",
        )
        self.assertEqual(instagram["metadata"]["instagram"]["type"], "reel")
        self.assertTrue(instagram["metadata"]["instagram"]["shouldShareToFeed"])
        self.assertIn("createPost", CREATE_POST)
        self.assertEqual(_bundle()["pipeline"]["buffer_api_url"], "https://api.buffer.com")
        limits = {"optional_hashtags": ["#movies", "#anime", "#clips"], "max_hashtags": 1, "require_hashtags": []}
        varied = {tuple(rotated_hashtags(limits, salt)) for salt in ("a", "b", "c", "d", "e", "f", "g")}
        self.assertGreater(len(varied), 1)

    def test_live_and_monitor_stay_on_unless_the_variable_is_false(self):
        import os
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO

        from clipper.cli import main

        previous_live = os.environ.pop("CLIPPER_LIVE", None)
        previous_token = os.environ.pop("BUFFER_API_TOKEN", None)
        previous_monitor = os.environ.pop("CLIPPER_MONITOR", None)
        try:
            err = StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["run", "--live"]), 2)
            self.assertIn("BUFFER_API_TOKEN", err.getvalue())
            self.assertNotIn("CLIPPER_LIVE is false", err.getvalue())
            os.environ["CLIPPER_LIVE"] = "false"
            err = StringIO()
            with redirect_stderr(err):
                self.assertEqual(main(["run", "--live"]), 2)
            self.assertIn("CLIPPER_LIVE is false", err.getvalue())
            os.environ.pop("CLIPPER_LIVE", None)
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["monitor"]), 0)
            self.assertNotIn("Deadline check skipped", out.getvalue())
            os.environ["CLIPPER_MONITOR"] = "false"
            out = StringIO()
            with redirect_stdout(out):
                self.assertEqual(main(["monitor"]), 0)
            self.assertIn("Deadline check skipped", out.getvalue())
        finally:
            if previous_live is None:
                os.environ.pop("CLIPPER_LIVE", None)
            else:
                os.environ["CLIPPER_LIVE"] = previous_live
            if previous_token is None:
                os.environ.pop("BUFFER_API_TOKEN", None)
            else:
                os.environ["BUFFER_API_TOKEN"] = previous_token
            if previous_monitor is None:
                os.environ.pop("CLIPPER_MONITOR", None)
            else:
                os.environ["CLIPPER_MONITOR"] = previous_monitor

    def test_missing_cookies_do_not_pause_and_sent_posts_keep_the_url(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg is not installed")
        from clipper.livepost import execute_live
        from clipper.render import render_color

        bundle = _bundle()
        root, media, out = self._dirs()
        source = media / "evan.mp4"
        render_color(source, 160, 284, 3.0, "clean")
        manifest = {
            "sui": {"file_id": "drive-evan", "path": str(source), "status": "downloaded"},
            "content_plan": [
                {"platform": "x", "status": "unavailable", "reason": "no public clip and no cookie file"},
                {"platform": "youtube", "status": "needs_cookies"},
                {"platform": "tiktok", "status": "skipped", "reason": "no inbox URL and no cookie file"},
            ],
        }
        (media / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        seen = []

        def poster(payload):
            seen.append(payload)
            suffix = payload["channelId"][-6:]
            return {
                "id": f"buf-{suffix}",
                "status": "sent",
                "sentAt": "2026-10-27T00:00:10.000Z",
                "externalLink": f"https://www.youtube.com/shorts/{suffix}",
                "text": payload["text"],
            }

        def uploader(path, name):
            return f"https://github.com/chatgpt820970/chad-media/releases/download/clip-media/{name}"

        kwargs = dict(
            now=NOW,
            env={"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=uploader,
            poster=poster,
            fetcher=lambda exclude: None,
            ledger_path=root / "live.json",
            pending_path=root / "pending.json",
            alerts_path=root / "alerts.json",
        )
        code = execute_live(bundle, **kwargs)
        self.assertEqual(code, 0)
        channels = {item["channelId"] for item in seen}
        self.assertEqual(
            channels,
            {"6ac4da806a5c39ccb62eab64", "6add07e4778b240e2fb35110"},
        )
        self.assertTrue(all(item["text"].count("example.invalid") == 0 for item in seen))
        self.assertTrue(all("x.com/SuiNetwork" in item["text"] for item in seen))
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})
        pending = json.loads((root / "pending.json").read_text())
        self.assertEqual(len(pending["items"]), 2)
        for item in pending["items"]:
            self.assertTrue(item["post_url"].startswith("https://"))
            self.assertNotIn("example.invalid", item["post_url"])
            self.assertEqual(item["posted_at"], "2026-10-27T00:00:10.000Z")
        ledger = json.loads((root / "live.json").read_text())
        self.assertEqual(set(ledger["sources"]), {"youtube:drive-evan", "tiktok:drive-evan"})
        again = execute_live(bundle, **kwargs)
        self.assertEqual(again, 0)
        self.assertEqual(len(seen), 2)

    def test_approved_dhan_file_beats_the_content_plan_and_sui_beats_dhan(self):
        from clipper.livepost import _gather_candidates, empty_ledger
        from clipper.strikes import empty_alerts

        bundle = _bundle()
        bundle["approvals"]["approved"] = ["fixture-dhan-yt"]
        root, media, out = self._dirs()
        sui = self._file(media, "evan.mp4")
        dhan = self._file(media / "dhan", "fixture-dhan-yt.mp4")
        plan = self._file(media, "cricket-yt.mp4")
        manifest = {
            "sui": {"file_id": "drive-evan", "path": str(sui), "status": "downloaded"},
            "content_plan": [{"platform": "youtube", "status": "downloaded", "path": str(plan)}],
        }
        (media / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        gathered = _gather_candidates(
            "youtube", bundle, manifest, media, empty_ledger(), empty_alerts(), lambda exclude: None
        )
        self.assertEqual([item[0] for item in gathered], ["sui", "dhan", "content_plan"])
        self.assertEqual(gathered[0][2], "drive-evan")
        ledger = empty_ledger()
        ledger["sources"].append("youtube:drive-evan")
        gathered = _gather_candidates(
            "youtube", bundle, manifest, media, ledger, empty_alerts(), lambda exclude: None
        )
        self.assertEqual([item[0] for item in gathered], ["dhan", "content_plan"])
        self.assertEqual(gathered[0][1], dhan)
        unapproved = _bundle()
        gathered = _gather_candidates(
            "youtube", unapproved, manifest, media, empty_ledger(), empty_alerts(), lambda exclude: None
        )
        self.assertEqual([item[0] for item in gathered], ["sui", "content_plan"])
        reel = self._file(media, "ig-abc.mp4")
        shared = {
            "sui": {"file_id": "drive-evan", "path": str(sui), "status": "downloaded"},
            "content_plan": [
                {"platform": "youtube", "status": "downloaded", "path": str(reel)},
                {"platform": "tiktok", "status": "downloaded", "path": str(reel)},
            ],
        }
        posted = empty_ledger()
        posted["sources"].append("youtube:ig-abc")
        yt_shared = _gather_candidates(
            "youtube", bundle, shared, media, posted, empty_alerts(), lambda exclude: None
        )
        self.assertNotIn("content_plan", [item[0] for item in yt_shared])
        self.assertIn("sui", [item[0] for item in yt_shared])
        tt_shared = _gather_candidates(
            "tiktok", bundle, shared, media, posted, empty_alerts(), lambda exclude: None
        )
        self.assertEqual([item[0] for item in tt_shared], ["sui"])
        sui_only = empty_ledger()
        sui_only["sources"].append("youtube:drive-evan")
        tt_sui = _gather_candidates(
            "tiktok", bundle, shared, media, sui_only, empty_alerts(), lambda exclude: None
        )
        self.assertEqual([item[0] for item in tt_sui], ["sui", "content_plan"])
        del out, root

    def test_cookies_expired_does_not_pause(self):
        from clipper.strikes import empty_alerts, is_paused, record_cookies_expired

        state = empty_alerts()
        self.assertTrue(record_cookies_expired(state, "instagram", "cookie download rejected: login required"))
        self.assertFalse(record_cookies_expired(state, "instagram", "cookie download rejected: login required"))
        self.assertTrue(record_cookies_expired(state, "youtube", "cookie download rejected: not a bot"))
        self.assertEqual(state["paused"], {})
        self.assertFalse(is_paused(state, "instagram"))
        self.assertFalse(is_paused(state, "youtube"))
        self.assertEqual([row["kind"] for row in state["alerts"]], ["cookies_expired", "cookies_expired"])
        self.assertEqual(state["alerts"][0]["platform"], "instagram")

    def test_sui_render_uses_a_real_caption_when_ffmpeg_exists(self):
        if not ffmpeg_available():
            self.skipTest("ffmpeg is not installed")
        from clipper.livepost import execute_live
        from clipper.render import render_color

        bundle = _bundle()
        root, media, out = self._dirs()
        source = media / "evan.mp4"
        render_color(source, 160, 284, 3.0, "clean")
        manifest = {"sui": {"file_id": "drive-evan", "path": str(source), "status": "downloaded"}, "content_plan": []}
        (media / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        seen = []

        def poster(payload):
            seen.append(payload)
            return {"id": "buf-sui", "status": "sent", "sentAt": "2026-10-27T00:00:00.000Z", "externalLink": "https://www.youtube.com/shorts/example", "text": payload["text"]}

        def uploader(path, name):
            self.assertTrue(str(name).startswith("live-"))
            return f"https://github.com/chatgpt820970/chad-media/releases/download/clip-media/{name}"

        code = execute_live(
            bundle,
            now=NOW,
            env={"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=uploader,
            poster=poster,
            fetcher=lambda exclude: None,
            ledger_path=root / "live.json",
            pending_path=root / "pending.json",
            alerts_path=root / "alerts.json",
        )
        self.assertEqual(code, 0)
        youtube = next(item for item in seen if item["channelId"].endswith("2eab64"))
        self.assertIn("x.com/SuiNetwork", youtube["text"])
        self.assertIn("#shorts", youtube["text"])
        self.assertLessEqual(youtube["text"].count("#"), 5)
        pending = json.loads((root / "pending.json").read_text())
        self.assertTrue(any(item["platform"] == "youtube" and item["posted_at"] == "2026-10-27T00:00:00.000Z" for item in pending["items"]))

    def test_media_url_failure_does_not_pause(self):
        from clipper.buffer_api import BufferError
        from clipper.livepost import execute_live
        from clipper.mediahost import MediaURLError
        from clipper.strikes import empty_alerts, record_media_url

        state = empty_alerts()
        self.assertTrue(record_media_url(state, "x", "content-type application/octet-stream", NOW))
        self.assertFalse(record_media_url(state, "x", "content-type application/octet-stream", NOW))
        self.assertEqual(state["paused"], {})
        self.assertEqual([row["kind"] for row in state["alerts"]], ["media_url"])

        bundle = _bundle()
        root, media, out = self._dirs()
        path = self._file(media, "anime-x.mp4")
        rows = [{"platform": "x", "status": "downloaded", "path": str(path)}]
        self._manifest(media, rows)
        seen = []

        def poster(payload):
            seen.append(payload)
            raise BufferError("Invalid post: Video could not be read from its URL.")

        def uploader(path, name):
            return f"https://github.com/chatgpt820970/chad-media/releases/download/clip-media/{name}"

        kwargs = dict(
            now=NOW,
            env={"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=uploader,
            poster=poster,
            fetcher=lambda exclude: None,
            ledger_path=root / "live.json",
            pending_path=root / "pending.json",
            alerts_path=root / "alerts.json",
            url_check=lambda url: None,
        )
        code = execute_live(bundle, **kwargs)
        self.assertEqual(code, 1)
        self.assertEqual(len(seen), 1)
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})
        self.assertEqual([row["kind"] for row in alerts["alerts"]], ["media_url"])
        ledger = json.loads((root / "live.json").read_text())
        self.assertEqual(ledger["posted"], [])
        self.assertEqual(ledger["sources"], [])
        again = execute_live(bundle, **kwargs)
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(len([row for row in alerts["alerts"] if row["kind"] == "media_url"]), 1)
        self.assertEqual(alerts["paused"], {})

        seen.clear()

        def blocked_check(url):
            raise MediaURLError("redirect 302")

        kwargs["url_check"] = blocked_check
        kwargs["poster"] = lambda payload: seen.append(payload)
        code = execute_live(bundle, **kwargs)
        self.assertEqual(code, 1)
        self.assertEqual(seen, [])
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})

    def test_topic_cooldown_skips_a_clip_and_still_lists_sui(self):
        from clipper.guard import blocked_reason
        from clipper.livepost import _gather_candidates, empty_ledger, execute_live
        from clipper.strikes import empty_alerts

        ledger = empty_ledger()
        self.assertIsNone(blocked_reason(ROOT, "cricket highlight", ledger, NOW))
        self.assertIn("dua lipa", blocked_reason(ROOT, "Dua Lipa stage clip", ledger, NOW) or "")
        ledger["topics"] = [{"topic": "dakota johnson", "posted_at": "2026-10-26T00:00:00.000Z"}]
        self.assertIn("dakota johnson", blocked_reason(ROOT, "Dakota Johnson interview", ledger, NOW) or "")
        stale = empty_ledger()
        stale["topics"] = [{"topic": "dakota johnson", "posted_at": "2026-10-19T00:00:00.000Z"}]
        self.assertIsNone(blocked_reason(ROOT, "Dakota Johnson interview", stale, NOW))

        bundle = _bundle()
        root, media, out = self._dirs()
        sui = self._file(media, "evan.mp4")
        blocked = self._file(media, "dua-lipa-stage.mp4")
        kept = self._file(media, "cricket-highlight.mp4")
        manifest = {
            "sui": {"file_id": "drive-evan", "path": str(sui), "status": "downloaded"},
            "content_plan": [
                {"platform": "youtube", "status": "downloaded", "path": str(blocked), "title": "Dua Lipa stage"},
                {"platform": "youtube", "status": "downloaded", "path": str(kept), "title": "India cricket highlights"},
            ],
        }
        gathered = _gather_candidates(
            "youtube", bundle, manifest, media, empty_ledger(), empty_alerts(), lambda exclude: None
        )
        self.assertEqual(gathered[0][0], "sui")
        self.assertIn("content_plan", [item[0] for item in gathered])
        (media / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        manifest["sui"] = {}
        (media / "sui-basecamp-short.mp4").unlink(missing_ok=True)
        sui.unlink()
        (media / "manifest.json").write_text(
            json.dumps({"sui": {}, "content_plan": manifest["content_plan"]}),
            encoding="utf-8",
        )
        seen = []

        def poster(payload):
            seen.append(payload["text"])
            return {"id": "buf-ok", "status": "scheduled", "text": payload["text"]}

        code = execute_live(
            bundle,
            now=NOW,
            env={"BUFFER_API_TOKEN": "test-token"},
            media_dir=media,
            output_dir=out,
            uploader=lambda path, name: f"https://github.com/chatgpt820970/chad-media/releases/download/clip-media/{name}",
            poster=poster,
            fetcher=lambda exclude: None,
            ledger_path=root / "live.json",
            pending_path=root / "pending.json",
            alerts_path=root / "alerts.json",
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        ledger = json.loads((root / "live.json").read_text())
        self.assertIn("youtube:cricket-highlight", ledger["sources"])
        self.assertNotIn("youtube:dua-lipa-stage", ledger["sources"])
        alerts = json.loads((root / "alerts.json").read_text())
        self.assertEqual(alerts["paused"], {})

    def test_x_caption_hook_and_overlay_graph(self):
        from clipper.render import x_overlay_graph

        bundle = _bundle()
        _root, media, out = self._dirs()
        path = self._file(media, "anime-x.mp4")
        rows = [{"platform": "x", "status": "downloaded", "path": str(path), "title": "anime fight"}]
        seen = []

        def poster(payload):
            seen.append(payload)
            return {"id": "buf-x", "status": "scheduled", "text": payload["text"]}

        code, calls = self._run(bundle, media, out, rows, poster)
        self.assertEqual(code, 0)
        text = next(item["text"] for item in seen if item["channelId"].endswith("fdf1"))
        assert "invidious" not in text.lower()
        self.assertNotIn("x.com/SuiNetwork", text)
        self.assertFalse(text.startswith("#Ad"))
        self.assertTrue(calls)
        graph = x_overlay_graph("Clean cut")
        self.assertIn("drawtext", graph)
        self.assertIn("[0:v]", graph)
        plain, uses_logo = logo_filter({"logo": {"required": False}}, 720, 1280, 0.0)
        self.assertFalse(uses_logo)
        self.assertNotIn("drawtext", plain)

    def test_heatmap_peak_ignores_the_opening(self):
        from clipper.heatmap import pick_heatmap_start, section_arg

        points = []
        for index in range(12):
            start = index * 5
            points.append({"start_time": start, "end_time": start + 5, "value": 100 if start == 0 else 1})
        points[8]["value"] = 40
        info = {"duration": 70, "heatmap": points}
        start, why = pick_heatmap_start(info, 12)
        self.assertGreater(start, 0)
        self.assertNotAlmostEqual(start, 0.0)
        self.assertIn("heatmap", why)
        self.assertLess(start, 45)
        self.assertEqual(section_arg(0), "*0-12")
        self.assertTrue(section_arg(start).startswith("*"))
        self.assertEqual(pick_heatmap_start({"duration": 8, "heatmap": points}, 12)[0], 0.0)


class PublicSourceTests(unittest.TestCase):
    def test_parsers_drop_sexual_titles_and_watermarked_formats(self):
        from clipper.publicsrc import (
            TIKTOK_FORMAT,
            format_is_watermarked,
            parse_invidious_search,
            parse_reddit_listing,
            parse_x_timeline,
            pick_h264_itag,
            pick_x_variant,
        )

        timeline = {
            "props": {
                "pageProps": {
                    "timeline": {
                        "entries": [
                            {
                                "content": {
                                    "tweet": {
                                        "id_str": "2115",
                                        "full_text": "An enchanting melody",
                                        "user": {"screen_name": "Crunchyroll"},
                                        "extended_entities": {
                                            "media": [
                                                {
                                                    "type": "video",
                                                    "video_info": {
                                                        "variants": [
                                                            {
                                                                "content_type": "application/x-mpegURL",
                                                                "url": "https://video.twimg.com/a.m3u8",
                                                            },
                                                            {
                                                                "bitrate": 832000,
                                                                "content_type": "video/mp4",
                                                                "url": "https://video.twimg.com/vid/avc1/640x360/a.mp4",
                                                            },
                                                            {
                                                                "bitrate": 2176000,
                                                                "content_type": "video/mp4",
                                                                "url": "https://video.twimg.com/vid/avc1/1280x720/b.mp4",
                                                            },
                                                            {
                                                                "bitrate": 10368000,
                                                                "content_type": "video/mp4",
                                                                "url": "https://video.twimg.com/vid/avc1/1920x1080/c.mp4",
                                                            },
                                                        ]
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                }
                            },
                            {
                                "content": {
                                    "tweet": {
                                        "id_str": "9",
                                        "full_text": "explicit sex tape",
                                        "user": {"screen_name": "nope"},
                                        "extended_entities": {
                                            "media": [
                                                {
                                                    "type": "video",
                                                    "video_info": {
                                                        "variants": [
                                                            {
                                                                "content_type": "video/mp4",
                                                                "bitrate": 1000,
                                                                "url": "https://video.twimg.com/vid/avc1/640x360/x.mp4",
                                                            }
                                                        ]
                                                    },
                                                }
                                            ]
                                        },
                                    }
                                }
                            },
                        ]
                    }
                }
            }
        }
        html = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(timeline) + "</script>"
        posts = parse_x_timeline(html)
        self.assertEqual([post["id"] for post in posts], ["2115"])
        chosen = pick_x_variant(posts[0]["variants"])
        self.assertEqual(chosen["height"], 720)
        self.assertIn("1280x720", chosen["url"])
        listing = {
            "data": {
                "children": [
                    {
                        "data": {
                            "id": "ok",
                            "title": "cricket last over",
                            "is_video": True,
                            "over_18": False,
                            "url": "https://v.redd.it/abc",
                            "permalink": "/r/Cricket/comments/ok/cricket/",
                            "media": {"reddit_video": {"fallback_url": "https://v.redd.it/abc/DASH_720.mp4"}},
                        }
                    },
                    {"data": {"id": "nsfw", "title": "fine", "over_18": True, "is_video": True, "url": "https://v.redd.it/no"}},
                    {"data": {"id": "sex", "title": "naked scene", "over_18": False, "is_video": True, "url": "https://v.redd.it/no2"}},
                    {"data": {"id": "link", "title": "article", "over_18": False, "is_video": False, "url": "https://example.com"}},
                ]
            }
        }
        reddit = parse_reddit_listing(listing)
        self.assertEqual([row["id"] for row in reddit], ["ok"])
        self.assertEqual(reddit[0]["watch"], "https://www.reddit.com/r/Cricket/comments/ok/cricket/")
        videos = parse_invidious_search(
            [
                {"type": "video", "videoId": "abc", "title": "India cricket highlights"},
                {"type": "video", "videoId": "bad", "title": "explicit scene"},
                {"type": "playlist", "videoId": "pl", "title": "mix"},
            ]
        )
        self.assertEqual(videos, [{"id": "abc", "title": "India cricket highlights"}])
        self.assertEqual(
            pick_h264_itag({"adaptiveFormats": [{"itag": 18, "type": "video/mp4"}, {"itag": 160, "type": 'video/mp4; codecs="avc1.4d401e"'}]}),
            160,
        )
        self.assertTrue(format_is_watermarked("watermarked"))
        self.assertFalse(format_is_watermarked("h264"))
        self.assertIn("format_note!*=watermark", TIKTOK_FORMAT)
        self.assertNotIn("!*?=", TIKTOK_FORMAT)

    def test_cookie_commands_stay_on_their_own_site_and_logs_drop_cookie_lines(self):
        import os
        import stat
        import tempfile

        from clipper.fetchsrc import cookie_file, redact_log, site_cookie_commands
        from script.write_cookies import write_cookies

        folder = Path(tempfile.mkdtemp())
        cookies = folder / "yt.txt"
        cookies.write_text("# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tsid\tabc\n", encoding="utf-8")
        self.assertEqual(cookie_file(str(cookies)), str(cookies))
        self.assertIsNone(cookie_file(""))
        youtube = site_cookie_commands("youtube", "cricket match highlight India", "out.mp4", str(cookies))
        self.assertTrue(youtube)
        self.assertIn("--cookies", youtube[0])
        self.assertIn("player_client=web", " ".join(youtube[0]))
        self.assertTrue(any(part.startswith("ytsearch1:") for part in youtube[0]))
        self.assertFalse(any("tiktok.com" in part for part in youtube[0]))
        tiktok = site_cookie_commands("tiktok", "anime fight", "out.mp4", str(cookies), ["anime", "hollywood"])
        self.assertEqual(len(tiktok), 2)
        self.assertIn("https://www.tiktok.com/tag/anime", tiktok[0])
        self.assertIn("--cookies", tiktok[0])
        self.assertIn("format_note!*=watermark", " ".join(tiktok[0]))
        self.assertFalse(any(part.startswith("ytsearch") for part in tiktok[0]))
        profiles = site_cookie_commands(
            "tiktok",
            "anime fight",
            "out.mp4",
            str(cookies),
            ["anime", "hollywood"],
            ["crunchyroll", "netflix"],
        )
        self.assertEqual(len(profiles), 3)
        self.assertIn("https://www.tiktok.com/@crunchyroll", profiles[0])
        self.assertIn("--playlist-end", profiles[0])
        self.assertNotIn("--no-playlist", profiles[0])
        self.assertIn("https://www.tiktok.com/tag/anime", profiles[2])
        redacted = redact_log("ERROR: sign in\nCookie: sid=abc\n#HttpOnly_.youtube.com\tTRUE")
        self.assertIn("sign in", redacted)
        self.assertNotIn("sid=abc", redacted)
        self.assertNotIn("#HttpOnly", redacted)
        env_file = folder / "env"
        env_file.write_text("", encoding="utf-8")
        secret = "netscape line one\nnetscape line two"
        written = write_cookies(
            {"YT_COOKIES": secret, "TIKTOK_COOKIES": "", "INSTAGRAM_COOKIES": "", "X_COOKIES": ""},
            folder,
            env_file,
        )
        self.assertEqual(written, ["YT_COOKIES_FILE"])
        saved = (folder / "yt_cookies.txt").read_text(encoding="utf-8")
        self.assertEqual(saved, secret + "\n")
        self.assertEqual(stat.S_IMODE((folder / "yt_cookies.txt").stat().st_mode) & 0o777, 0o600)
        self.assertIn(f"YT_COOKIES_FILE={folder / 'yt_cookies.txt'}", env_file.read_text(encoding="utf-8"))
        self.assertNotIn("TIKTOK_COOKIES_FILE", env_file.read_text(encoding="utf-8"))
        del os

    def test_expired_cookie_reason_keeps_the_log_out_of_the_alert(self):
        from clipper.fetchsrc import cookies_look_expired, expired_reason

        self.assertTrue(cookies_look_expired("ERROR: Sign in to confirm you're not a bot"))
        self.assertTrue(cookies_look_expired("Requested content is not available, rate-limit reached or login required"))
        self.assertFalse(cookies_look_expired("HTTP Error 403"))
        self.assertFalse(cookies_look_expired(""))
        reason = expired_reason("ERROR: login required\nCookie: sid=secret")
        self.assertEqual(reason, "cookie download rejected: login required")
        self.assertNotIn("secret", reason)

    def test_instagram_targets_rank_clean_reels_and_skip_handles_page(self):
        from clipper.igsource import instagram_targets, rank_reels, reel_shortcode

        bundle = {
            "sources": {
                "instagram_source": {
                    "youtube": {"hashtags": ["Bollywood"], "accounts": ["icc"]},
                    "x": {"hashtags": ["animeedit"], "accounts": []},
                }
            }
        }
        urls = instagram_targets(bundle, "youtube")
        self.assertEqual(
            urls,
            [
                "https://www.instagram.com/icc/",
                "https://www.instagram.com/explore/tags/bollywood/",
            ],
        )
        self.assertFalse(any("/reels/" in url for url in urls))
        self.assertEqual(reel_shortcode("https://www.instagram.com/reel/AbC123/", ""), "AbC123")
        ranked = rank_reels(
            {
                "entries": [
                    {"id": "low", "title": "cricket highlight", "view_count": 10, "url": "https://www.instagram.com/reel/low/"},
                    {"id": "high", "title": "bollywood scene", "view_count": 90, "url": "https://www.instagram.com/reel/high/"},
                    {"id": "bad", "title": "explicit scene", "view_count": 999, "url": "https://www.instagram.com/reel/bad/"},
                    None,
                    {"id": "same", "title": "anime edit", "view_count": 5, "url": "https://www.instagram.com/p/same/"},
                ]
            },
            seen={"high"},
        )
        self.assertEqual([row["id"] for row in ranked], ["low", "same"])


if __name__ == "__main__":
    unittest.main()
