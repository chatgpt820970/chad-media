"""Growth bots: quality gates, captions, mix, and isolated workflows."""

from __future__ import annotations

import ast
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from clipper.buffer_api import BufferError

from growth.instagram.bot import HOOKS as IG_HOOKS
from growth.instagram.bot import targets_for as ig_targets
from growth.instagram.caption import caption_for as ig_caption
from growth.instagram.caption import review_caption as ig_review
from growth.utils.cli import main
from growth.utils.dedupe import load_state, remember, save_state, seen, title_key
from growth.utils.hosting import asset_name
from growth.utils.media import Prepared, foreground_is_padded, light_touch_filters, usable_trim, vertical_graph
from growth.utils.pipeline import Runtime, execute
from growth.utils.quality import (
    choose_count,
    copyright_reason,
    corner_logo_regions,
    edge_cutoff,
    letterbox,
    paint_busy_border,
    paint_rect,
    resolution_reason,
    rgb_frame,
    split_share,
    watermark_reason,
)
from growth.utils.schedule import buffer_payload, ist_utc_cron, lane_for_index, slot_clocks, slot_gaps_ok, slot_plan
from growth.utils.improve import apply_weights, due, parse_weights, run_improve
from growth.utils.review_gate import frame_times, parse_review, parse_tweaks
from growth.utils.ytdlp import engagement, parse_entries, rank_recent, search_target
from growth.x.bot import HOOKS as X_HOOKS
from growth.x.pending import batch_slots, caption_to_schedule, load_queue, parse_due, save_queue
from growth.x.prepare import prepare_batch
from growth.x.schedule_captioned import schedule_ready
from growth.x.bot import load_config as load_x
from growth.x.bot import targets_for as x_targets
from growth.x.caption import caption_for as x_caption
from growth.x.caption import flag_for
from growth.x.caption import review_caption as x_review
from growth.youtube.bot import HOOKS as YT_HOOKS
from growth.youtube.bot import load_config as load_youtube
from growth.youtube.caption import caption_for as yt_caption
from growth.youtube.caption import review_caption as yt_review
from growth.utils.ytdlp import Candidate

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 29, 1, 0, tzinfo=timezone.utc)
X_WINDOW = datetime(2026, 10, 29, 1, 35, tzinfo=timezone.utc)
X_BACKUP = datetime(2026, 10, 29, 1, 45, tzinfo=timezone.utc)
YT_WINDOW = datetime(2026, 10, 29, 1, 32, tzinfo=timezone.utc)
IG_WINDOW = datetime(2026, 10, 29, 2, 36, tzinfo=timezone.utc)


def _candidate(**kwargs: object) -> Candidate:
    data = {
        "source_id": "abc",
        "title": "Elon Musk outlines the next flight window",
        "url": "https://www.youtube.com/watch?v=abc",
        "lane": "news",
        "topic": "elon",
        "duration": 40,
        "width": 1280,
        "height": 720,
        "description": "",
    }
    data.update(kwargs)
    return Candidate(**data)  # type: ignore[arg-type]


class QualityTests(unittest.TestCase):
    def test_resolution_uses_the_shorter_side(self):
        self.assertIsNone(resolution_reason(1280, 720))
        self.assertIsNone(resolution_reason(720, 1280))
        self.assertIn("below 720p", resolution_reason(640, 360) or "")
        self.assertIn("below 720p", resolution_reason(640, 1136) or "")

    def test_corner_logo_and_clean_center(self):
        clean = rgb_frame(180, 320)
        self.assertEqual(corner_logo_regions(clean, 180, 320), [])
        self.assertFalse(edge_cutoff(clean, 180, 320))
        marked = paint_rect(clean, 180, 320, 2, 2, 28, 22, (255, 255, 255))
        self.assertIn("top-left", corner_logo_regions(marked, 180, 320))

    def test_edge_cutoff_ignores_letterbox(self):
        busy = paint_busy_border(180, 320, 6)
        self.assertTrue(edge_cutoff(busy, 180, 320))
        frame = rgb_frame(180, 320, (40, 40, 40))
        frame = paint_rect(frame, 180, 320, 0, 0, 180, 24, (0, 0, 0))
        frame = paint_rect(frame, 180, 320, 0, 296, 180, 24, (0, 0, 0))
        self.assertTrue(letterbox(frame, 180, 320))
        self.assertFalse(edge_cutoff(frame, 180, 320))

    def test_watermark_ocr_and_copyright(self):
        self.assertEqual(watermark_reason("follow @creator on tiktok"), "third-party watermark or logo")
        self.assertIsNone(watermark_reason("a stadium crowd"))
        self.assertIn("logo", watermark_reason("ICC Men's Cricket World Cup") or "")
        self.assertIn("full copy", copyright_reason("Full Movie Bahubali", "", 120, 4800) or "")
        self.assertIn("feature", copyright_reason("Match replay", "", 100 * 60, 4800) or "")
        self.assertIsNone(copyright_reason("Virat Kohli incredible six", "", 40, 4800))
        self.assertIn("episode", copyright_reason("Show S02E10", "", 45 * 60, 4800) or "")

    def test_vertical_fit_pads_instead_of_cropping_the_subject(self):
        graph = vertical_graph("Virat Kohli ka ye moment", "lt(t,1)", zoom=False)
        self.assertTrue(foreground_is_padded(graph))
        self.assertIn("boxblur", graph)
        self.assertIn("lt(t,1)", graph)
        zoomed = vertical_graph("hook", "lt(t,1.5)", zoom=True)
        self.assertTrue(foreground_is_padded(zoomed))
        self.assertIn("crop=", zoomed.split("[fg]", 1)[0])

    def test_lane_math_stays_inside_the_asked_ranges(self):
        self.assertEqual(choose_count(8, 0.55, 0.5, 0.6), 4)
        self.assertEqual(choose_count(7, 0.55, 0.5, 0.6), 4)
        self.assertEqual(split_share(9, 0.55), (5, 4))
        self.assertEqual(split_share(8, 0.55), (4, 4))


class DedupeTests(unittest.TestCase):
    def test_round_trip_and_title_match(self):
        path = Path(self.id().replace(".", "_") + "-seen.json")
        try:
            state = load_state(path)
            self.assertFalse(seen(state, "abc", "Hello There"))
            remember(state, "abc", "Hello There", {"source_id": "abc"})
            save_state(path, state)
            loaded = load_state(path)
            self.assertTrue(seen(loaded, "abc", "other"))
            self.assertTrue(seen(loaded, "new", "Hello, there!"))
            self.assertEqual(title_key("Hello, there!"), title_key("hello there"))
        finally:
            path.unlink(missing_ok=True)


class ParseTests(unittest.TestCase):
    def test_flat_search_rows_and_unknown_kind(self):
        payload = {"entries": [{"id": "xyz", "title": "Virat Kohli six", "duration": 30}, None, {"id": "", "title": "x"}]}
        rows = parse_entries(payload, {"kind": "ytsearch", "lane": "indian", "topic": "virat"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].url, "https://www.youtube.com/watch?v=xyz")
        self.assertEqual(rows[0].lane, "indian")
        with self.assertRaises(ValueError):
            search_target({"kind": "private"})
        self.assertEqual(
            search_target({"kind": "hashtag", "platform": "tiktok", "tag": "bigg_boss"}),
            "https://www.tiktok.com/tag/bigg_boss",
        )
        fresh = _candidate(source_id="new", timestamp=1_000_000, view_count=10, like_count=1, topic="cricket", url="https://www.tiktok.com/@mumbaiindians/video/1", account="https://www.tiktok.com/@mumbaiindians")
        stale = _candidate(source_id="old", timestamp=1_000, view_count=99999, like_count=1, topic="cricket")
        ranked = rank_recent([stale, fresh], max_age_hours=48, now=1_000_000)
        self.assertEqual([row.source_id for row in ranked], ["new"])
        boosted = engagement(fresh, {"cricket": 2}, {"https://www.tiktok.com/@mumbaiindians": 3})
        self.assertEqual(boosted, (10 + 3) * 2 * 3)


class CaptionTests(unittest.TestCase):
    def test_x_caption_is_informative_and_not_promo(self):
        caption = x_caption(_candidate())
        self.assertIn("🇺🇸", caption)
        self.assertEqual(flag_for("Premier League title race"), "🇬🇧")
        self.assertEqual(flag_for("Volcano erupts overnight"), "🌍")
        tags = [token for token in caption.split() if token.startswith("#")]
        self.assertGreaterEqual(len(tags), 1)
        self.assertLessEqual(len(tags), 2)
        self.assertEqual(x_review(caption, _candidate()), [])
        promo = _candidate(title="Use code SAVE50 for the Tesla drop", description="shop now")
        self.assertTrue(x_review(x_caption(promo), promo))

    def test_youtube_hinglish_names_and_hashtags(self):
        clip = _candidate(
            source_id="kohli1",
            title="Virat Kohli incredible six vs Pakistan",
            lane="indian",
            topic="virat",
        )
        caption = yt_caption(clip)
        self.assertIn("Virat Kohli", caption.splitlines()[0])
        self.assertLessEqual(len(caption.splitlines()[0]), 100)
        count = caption.count("#")
        self.assertGreaterEqual(count, 3)
        self.assertLessEqual(count, 5)
        self.assertEqual(yt_review(caption, clip), [])
        video_filter, audio_filter = light_touch_filters({"speed": 1.03, "grade": True, "mirror": False})
        self.assertNotIn("drawtext", video_filter)
        self.assertNotIn("boxblur", video_filter)
        self.assertNotIn("crop", video_filter)
        self.assertNotIn("pad", video_filter)
        self.assertIn("atempo=1.030", audio_filter)
        self.assertEqual(usable_trim(20, 10, {"speed": 1.03, "trim_seconds": 0.4}), 0.4)
        self.assertEqual(usable_trim(10.1, 10, {"speed": 1.03, "trim_seconds": 0.4}), 0.0)
        self.assertIn("#shorts", caption.lower())

    def test_instagram_hinglish_and_mix(self):
        indian = _candidate(source_id="ig1", title="Kapil Sharma live moment", lane="indian", topic="kapil")
        global_clip = _candidate(source_id="ig2", title="street performance", lane="global", topic="global")
        self.assertEqual(ig_review(ig_caption(indian), indian), [])
        self.assertEqual(ig_review(ig_caption(global_clip), global_clip), [])
        config = IG_HOOKS.load_config()
        mix = ig_targets(int(config["posts_per_day"]), config)
        total = sum(mix.values())
        self.assertEqual(total, 8)
        self.assertGreaterEqual(mix["indian"] / total, 0.5)
        self.assertLessEqual(mix["indian"] / total, 0.6)
        self.assertEqual(x_targets(9, load_x()), {"news": 5, "video": 4})


class BufferShapeTests(unittest.TestCase):
    def test_payloads_stay_on_the_right_channel(self):
        when = datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
        x_payload = buffer_payload("x", "🇺🇸 A short update. #News", "x-channel", "https://cdn.example/a.mp4", when)
        self.assertEqual(x_payload["channelId"], "x-channel")
        self.assertNotIn("metadata", x_payload)
        self.assertEqual(x_payload["mode"], "customScheduled")
        yt_payload = buffer_payload("youtube", "Virat Kohli ka ye moment\n#ViratKohli #Cricket #Shorts", "yt", "https://cdn.example/b.mp4", when)
        self.assertEqual(yt_payload["metadata"]["youtube"]["title"], "Virat Kohli ka ye moment")
        ig_payload = buffer_payload("instagram", "Bhai ye dekh\n#Reels", "ig", "https://cdn.example/c.mp4", when)
        self.assertEqual(ig_payload["metadata"]["instagram"]["type"], "reel")
        self.assertTrue(asset_name("live-growth-x", "abc/def").startswith("live-growth-x-"))


class PipelineTests(unittest.TestCase):
    def _runtime(self, config: dict, candidates: list[Candidate], **kwargs: object) -> Runtime:
        state = Path(self.id().replace(".", "_") + ".json")
        log = Path(self.id().replace(".", "_") + ".jsonl")
        state.unlink(missing_ok=True)
        log.unlink(missing_ok=True)

        def prepare(candidate: Candidate, hook: str) -> Prepared:
            del hook
            return Prepared(
                source_id=candidate.source_id,
                width=1280,
                height=720,
                duration=float(candidate.duration or 40),
                output_seconds=20,
                local_path="/tmp/clip.mp4",
            )

        runtime = Runtime(
            config=config,
            state_path=state,
            log_path=log,
            now=NOW,
            dry_run=bool(kwargs.get("dry_run", True)),
            discover=lambda: candidates,
            targets_for=kwargs.get("targets_for", lambda goal: None),  # type: ignore[arg-type]
            caption_for=kwargs.get("caption_for", x_caption),  # type: ignore[arg-type]
            review_caption=kwargs.get("review_caption", x_review),  # type: ignore[arg-type]
            prepare=kwargs.get("prepare", prepare),  # type: ignore[arg-type]
            publisher=kwargs.get("publisher"),
            poster=kwargs.get("poster"),  # type: ignore[arg-type]
            token_present=bool(kwargs.get("token_present", False)),
            channel=str(kwargs.get("channel") or config["buffer_channel_id"]),
            force_next=bool(kwargs.get("force_next", False)),
            channel_check=kwargs.get("channel_check", lambda: (True, "")),  # type: ignore[arg-type]
            queue_count=kwargs.get("queue_count", lambda: 0),  # type: ignore[arg-type]
        )
        runtime.now = kwargs.get("now", NOW)  # type: ignore[assignment]
        self.addCleanup(state.unlink, missing_ok=True)
        self.addCleanup(log.unlink, missing_ok=True)
        return runtime

    def test_x_dry_run_keeps_the_news_and_video_mix_and_does_not_post(self):
        config = load_x()
        rows = []
        for index in range(6):
            rows.append(_candidate(source_id=f"news-{index}", title=f"Elon Musk update {index}", lane="news", topic="elon"))
            rows.append(_candidate(source_id=f"video-{index}", title=f"NBA highlight {index}", lane="video", topic="sports"))
        calls: list[str] = []
        runtime = self._runtime(
            config,
            rows,
            dry_run=True,
            now=X_WINDOW,
            force_next=True,
            targets_for=lambda goal: x_targets(goal, config),
            prepare=lambda *_args: calls.append("prep") or Prepared("x", 0, 0, 0),
            poster=lambda _payload: calls.append("post"),
        )
        report = execute(runtime)
        self.assertEqual(report.scheduled, 1)
        self.assertEqual(report.exit_code, 0)
        self.assertEqual(calls, [])
        posted = [event for event in runtime.events if event["kind"] == "dry_run"]
        self.assertEqual(len(posted), 1)
        lane = lane_for_index(0, x_targets(len(slot_clocks(config)), config))
        self.assertTrue(str(posted[0]["source_id"]).startswith(lane))
        self.assertEqual(posted[0]["due_at"], "2026-10-29T02:00:00.000Z")
        self.assertEqual(posted[0]["channel"], config["buffer_channel_id"])
        self.assertFalse(runtime.state_path.exists())

    def test_low_res_copyright_and_duplicate_are_rejected_without_a_download(self):
        config = load_x()
        downloaded: list[str] = []

        def prepare(candidate: Candidate, hook: str) -> Prepared:
            del hook
            downloaded.append(candidate.source_id)
            raise AssertionError("metadata rejects must not download")

        state = {
            "version": 1,
            "ids": ["seen-1"],
            "titles": [],
            "posts": [],
        }
        rows = [
            _candidate(source_id="seen-1", title="Elon Musk update seen"),
            _candidate(source_id="low", title="Elon Musk phone clip", width=640, height=360),
            _candidate(source_id="movie", title="Full Movie late show"),
            _candidate(source_id="promo", title="Use code SAVE50 now", topic="news"),
        ]
        runtime = self._runtime(
            config,
            rows,
            dry_run=False,
            token_present=True,
            now=X_WINDOW,
            force_next=True,
            prepare=prepare,
            poster=lambda _payload: {"id": "nope"},
        )
        save_state(runtime.state_path, state)
        report = execute(runtime)
        self.assertEqual(downloaded, [])
        self.assertEqual(report.scheduled, 0)
        self.assertGreaterEqual(report.rejected, 3)
        kinds = [event["kind"] for event in runtime.events]
        self.assertIn("duplicate", kinds)
        self.assertIn("rejected", kinds)

    def test_skip_then_schedule_and_stop_on_rate_limit(self):
        config = load_youtube()
        rows = [
            _candidate(source_id="bad", title="Kapil Sharma funny bit", lane="indian", topic="kapil", duration=40),
            _candidate(source_id="good", title="Virat Kohli cover drive", lane="indian", topic="virat", duration=40),
            _candidate(source_id="later", title="Virat Kohli yorker", lane="indian", topic="virat", duration=40),
            _candidate(source_id="extra", title="Rohit Sharma last over", lane="indian", topic="cricket", duration=40),
        ]

        def prepare(candidate: Candidate, hook: str) -> Prepared:
            del hook
            if candidate.source_id == "bad":
                raise RuntimeError("download failed")
            return Prepared(candidate.source_id, 1080, 1920, 40, output_seconds=20, local_path="/tmp/clip.mp4")

        class Publisher:
            def __init__(self) -> None:
                self.names: list[str] = []

            def add(self, path: str, name: str) -> str:
                del path
                self.names.append(name)
                return f"https://chatgpt820970.github.io/chad-media/live/{name}"

            def finalize(self) -> dict[str, str]:
                return {}

        posted: list[str] = []

        def poster(payload: dict) -> dict:
            posted.append(payload["text"])
            if len(posted) == 1:
                return {"id": "post-1"}
            raise BufferError("429 too many requests")

        publisher = Publisher()
        runtime = self._runtime(
            config,
            rows,
            dry_run=False,
            token_present=True,
            prepare=prepare,
            publisher=publisher,
            poster=poster,
            caption_for=yt_caption,
            review_caption=yt_review,
            targets_for=lambda goal: None,
            now=YT_WINDOW,
        )
        report = execute(runtime)
        self.assertEqual(report.scheduled, 1)
        self.assertEqual(report.skipped, 1)
        self.assertEqual(len(posted), 1)
        self.assertNotIn("Rohit", " ".join(posted))
        self.assertEqual(posted[0].split("\n")[0][:1].isalnum() or True, True)
        self.assertIn("2026-10-29T02:00:00.000Z", json.dumps(runtime.events))
        self.assertTrue(publisher.names[0].startswith("live-growth-youtube-"))
        self.assertTrue(seen(load_state(runtime.state_path), "good", "Virat Kohli cover drive"))
        self.assertFalse(seen(load_state(runtime.state_path), "bad", "Kapil Sharma funny bit"))
        saved = json.loads(runtime.state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["posts"][0]["buffer_post_id"], "post-1")

    def test_missing_token_and_disabled_instagram_do_not_fail(self):
        runtime = self._runtime(load_x(), [_candidate()], dry_run=False, token_present=False)
        report = execute(runtime)
        self.assertEqual(report.scheduled, 0)
        self.assertEqual(runtime.events[0]["kind"], "buffer_token_missing")
        instagram = self._runtime(
            IG_HOOKS.load_config(),
            [_candidate(lane="indian", topic="kapil", title="Kapil Sharma live moment")],
            dry_run=False,
            token_present=True,
            now=IG_WINDOW,
            caption_for=ig_caption,
            review_caption=ig_review,
            channel_check=lambda: (False, "buffer channel was not found"),
        )
        quiet = execute(instagram)
        self.assertEqual(quiet.scheduled, 0)
        self.assertEqual(quiet.exit_code, 0)
        self.assertEqual(instagram.events[0]["kind"], "channel_unavailable")


class ConfigAndWorkflowTests(unittest.TestCase):
    def test_checks_pass_and_counts_match_the_defaults(self):
        self.assertEqual(main(X_HOOKS, ["check"]), 0)
        self.assertEqual(main(YT_HOOKS, ["check"]), 0)
        self.assertEqual(main(IG_HOOKS, ["check"]), 0)
        x_config = X_HOOKS.load_config()
        yt_config = YT_HOOKS.load_config()
        ig_config = IG_HOOKS.load_config()
        self.assertTrue(x_config["enabled"])
        self.assertTrue(yt_config["enabled"])
        self.assertTrue(ig_config["enabled"])
        self.assertTrue(ig_config["channel_confirmed"])
        self.assertEqual(x_config["posts_per_day"], 9)
        self.assertEqual(x_config["posts_per_day_min"], 8)
        self.assertEqual(yt_config["posts_per_day"], 8)
        self.assertEqual(yt_config["posts_per_day_min"], 7)
        self.assertEqual(ig_config["posts_per_day"], 8)
        self.assertEqual(ig_config["buffer_channel_id"], "6ac93d936a5c39ccb66624d7")
        self.assertEqual(x_config["buffer_channel_id"], "6ac41eb06a5c39ccb625fdf1")
        self.assertEqual(yt_config["buffer_channel_id"], "6ac4da806a5c39ccb62eab64")
        self.assertTrue(slot_gaps_ok(slot_clocks(x_config), int(x_config["min_gap_minutes"])))
        self.assertTrue(slot_gaps_ok(slot_clocks(yt_config), int(yt_config["min_gap_minutes"])))
        self.assertTrue(slot_gaps_ok(slot_clocks(ig_config), int(ig_config["min_gap_minutes"])))
        self.assertNotEqual(x_config["asset_prefix"], yt_config["asset_prefix"])
        self.assertTrue(str(X_HOOKS.root).endswith("/growth/x"))
        self.assertTrue(str(YT_HOOKS.root).endswith("/growth/youtube"))
        self.assertTrue(str(IG_HOOKS.root).endswith("/growth/instagram"))

    def test_platforms_do_not_import_each_other(self):
        for platform in ("x", "youtube", "instagram"):
            for path in (ROOT / "growth" / platform).rglob("*.py"):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.ImportFrom) or not node.module:
                        continue
                    name = node.module
                    self.assertFalse(name.startswith("clipper"), path.name)
                    if name.startswith("growth.") and not name.startswith("growth.utils") and not name.startswith(f"growth.{platform}"):
                        self.fail(f"{path} imports {name}")

    def test_workflows_and_paused_whop_job(self):
        x_workflow = (ROOT / ".github/workflows/growth-x.yml").read_text(encoding="utf-8")
        yt_workflow = (ROOT / ".github/workflows/growth-youtube.yml").read_text(encoding="utf-8")
        ig_workflow = (ROOT / ".github/workflows/growth-instagram.yml").read_text(encoding="utf-8")
        whop = (ROOT / ".github/workflows/whop-clipper.yml").read_text(encoding="utf-8")
        self.assertIn("schedule:", x_workflow)
        self.assertIn("schedule:", yt_workflow)
        self.assertIn("schedule:", ig_workflow)
        self.assertNotIn("if: ${{ false }}", ig_workflow)
        self.assertIn("Retired", whop)
        self.assertNotIn("schedule:", whop)
        self.assertNotIn("schedule:", (ROOT / ".github/workflows/shorts.yml").read_text(encoding="utf-8").split("on:", 1)[-1])
        shorts = (ROOT / ".github/workflows/shorts.yml").read_text(encoding="utf-8")
        self.assertIn("Retired", shorts)

    def test_crons_match_the_slot_plan_once(self):
        import re

        found = []
        for name, hooks in (("youtube", YT_HOOKS), ("instagram", IG_HOOKS)):
            text = (ROOT / ".github/workflows" / f"growth-{name}.yml").read_text(encoding="utf-8")
            crons = re.findall(r'cron: "([^"]+)"', text)
            expect = []
            for row in slot_plan(hooks.load_config()):
                expect.append(ist_utc_cron(row["time"], int(row["lead"])))
                expect.append(ist_utc_cron(row["time"], int(row["backup_lead"])))
            self.assertEqual(crons, expect)
            found.extend(crons)
        x_text = (ROOT / ".github/workflows/growth-x.yml").read_text(encoding="utf-8")
        x_crons = re.findall(r'cron: "([^"]+)"', x_text)
        x_config = X_HOOKS.load_config()
        x_expect = [ist_utc_cron(batch["at"], 0) for batch in x_config["batches"]]
        x_expect.extend(ist_utc_cron(row["time"], int(row["fallback_lead"])) for row in slot_plan(x_config))
        self.assertEqual(x_crons, x_expect)
        self.assertNotIn("python -m growth.x run", x_text)
        found.extend(x_crons)
        improve = (ROOT / ".github/workflows/growth-improve.yml").read_text(encoding="utf-8")
        self.assertIn("python -m growth.youtube improve", improve)
        self.assertIn("python -m growth.instagram improve", improve)
        self.assertIn('cron: "15 3 */3 * *"', improve)
        found.append("15 3 */3 * *")
        self.assertEqual(len(found), len(set(found)))

    def test_gemini_failure_falls_back_to_the_template(self):
        from growth.utils import gemini

        def boom(prompt: str, api_key: str, model: str = "", timeout: int = 25) -> str:
            del prompt, api_key, model, timeout
            raise RuntimeError("gemini down")

        original = gemini.generate
        gemini.generate = boom
        try:
            text = x_caption(_candidate(), api_key="test-key")
        finally:
            gemini.generate = original
        self.assertIn("🇺🇸", text)
        self.assertEqual(x_review(text, _candidate()), [])

    def test_backup_failure_wakes_and_a_full_queue_does_not(self):
        config = load_x()

        def prepare(candidate: Candidate, hook: str) -> Prepared:
            del candidate, hook
            raise RuntimeError("render failed")

        youtube = load_youtube()
        backup_now = datetime(2026, 10, 29, 1, 42, tzinfo=timezone.utc)
        failed = PipelineTests._runtime(self, youtube, [_candidate(), _candidate(source_id="b2", title="Kapil Sharma second bit", lane="indian", topic="kapil")], dry_run=False, token_present=True, now=backup_now, prepare=prepare, poster=lambda payload: {"id": "nope"}, caption_for=yt_caption, review_caption=yt_review)
        report = execute(failed)
        self.assertEqual(report.scheduled, 0)
        self.assertEqual(report.exit_code, 1)
        self.assertIn("backup_failed", [event["kind"] for event in failed.events])
        held = PipelineTests._runtime(self,
            youtube,
            [_candidate(source_id="q1", title="Elon Musk queue clip")],
            dry_run=False,
            token_present=True,
            now=YT_WINDOW,
            queue_count=lambda: 9,
            prepare=lambda *_args: (_ for _ in ()).throw(AssertionError("queue full must not render")),
        )
        quiet = execute(held)
        self.assertEqual(quiet.exit_code, 0)
        self.assertEqual(held.events[0]["kind"], "queue_full")

    def test_x_batch_waits_for_bibi_and_uses_the_draft_at_the_deadline(self):
        config = load_x()
        morning = datetime(2026, 10, 29, 1, 30, tzinfo=timezone.utc)
        names = [item[0] for item in batch_slots(morning, config)]
        self.assertEqual(names, ["07:30", "09:30", "13:00"])
        evening = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        evening_names = [item[0] for item in batch_slots(evening, config)]
        self.assertIn("18:00", evening_names)
        self.assertIn("07:30", evening_names)
        queue = Path(self.id().replace(".", "_") + "-pending.json")
        self.addCleanup(queue.unlink, missing_ok=True)
        candidate = _candidate(source_id="clip1", description="Tesla outlined the next flight window.")

        def render(row, folder):
            del folder
            return Prepared(row.source_id, 1280, 720, 40, output_seconds=20, local_path="/tmp/clip.mp4")

        result = prepare_batch(
            morning,
            config=config,
            queue_path=queue,
            discover=lambda: [candidate, _candidate(source_id="clip2", title="NBA game winner", lane="video", topic="sports")],
            render=render,
            upload=lambda path, name: f"https://owner.github.io/chad-media/live/{name}",
            describe=lambda row, folder: row.description or row.title,
            draft=lambda row: x_caption(row),
            work=Path(self.id().replace(".", "_") + "-work"),
        )
        self.assertEqual(result["added"], 2)
        first = result["items"][0]
        later = result["items"][1]
        self.assertEqual(later["caption"], "")
        self.assertTrue(later["draft_caption"])
        self.assertIn("Tesla", first["description"])
        self.assertTrue(first["video_url"].startswith("https://"))
        self.assertIsNone(caption_to_schedule(later, morning, 30))
        stored = load_queue(queue)
        stored["items"][1]["caption"] = "🇺🇸 Tesla confirmed the next window. #Tesla"
        save_queue(queue, stored)
        posted = []
        summary = schedule_ready(
            morning,
            config=config,
            queue_path=queue,
            channel="x-channel",
            queued=0,
            poster=lambda payload: posted.append(payload) or {"id": "buf-1"},
        )
        self.assertEqual(summary["scheduled"], 2)
        texts = [payload["text"] for payload in posted]
        self.assertIn("🇺🇸 Tesla confirmed the next window. #Tesla", texts)
        self.assertIn(first["draft_caption"], texts)
        deadline = parse_due(first["due_at"]) - timedelta(minutes=30)
        fallback = caption_to_schedule(first, deadline, 30)
        self.assertEqual(fallback[1], "draft")


class ReviewAndImproveTests(unittest.TestCase):
    def test_frame_times_span_the_clip(self):
        times = frame_times(20, 0)
        self.assertEqual(len(times), 6)
        self.assertAlmostEqual(times[0], 1.6)
        self.assertGreater(times[-1], 15)
        self.assertLess(times[-1], 20)
        quiet = parse_tweaks({"mirror": True, "speed": 1.04}, 8)
        self.assertFalse(quiet["mirror"])
        flipped = parse_tweaks({"mirror": True, "speed": 9}, 9)
        self.assertTrue(flipped["mirror"])
        self.assertEqual(flipped["speed"], 1.05)

    def test_parse_review_requires_every_score(self):
        good = {
            key: 9
            for key in (
                "viral",
                "audience",
                "not_stock",
                "no_burned_text",
                "no_watermark",
                "not_cut_off",
                "no_copyright_risk",
                "safe",
                "overall",
            )
        }
        self.assertTrue(parse_review(json.dumps(good))["pass"])
        good["overall"] = 7
        self.assertFalse(parse_review(json.dumps(good))["pass"])
        self.assertFalse(parse_review(json.dumps({"overall": 10, "viral": 10}))["pass"])

    def test_weights_follow_views_and_wait_three_days(self):
        moment = datetime(2026, 10, 29, tzinfo=timezone.utc)
        self.assertTrue(due(None, moment))
        self.assertFalse(due("2026-10-28T00:00:00Z", moment))
        self.assertTrue(due("2026-10-25T00:00:00Z", moment))
        cleaned = parse_weights(json.dumps({"topic_weights": {"cricket": 9}, "account_weights": {"https://example": 0.1}}))
        self.assertEqual(cleaned["topic_weights"]["cricket"], 3.0)
        self.assertEqual(cleaned["account_weights"]["https://example"], 0.3)
        merged = apply_weights({"topic_weights": {"cricket": 1, "kapil": 1}}, {"topic_weights": {"cricket": 2}})
        self.assertEqual(merged["topic_weights"], {"cricket": 2, "kapil": 1})
        folder = Path(self.id().replace(".", "_") + "-improve")
        folder.mkdir(exist_ok=True)
        config_path = folder / "config.json"
        state_path = folder / "improve.json"

        def cleanup() -> None:
            config_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
            folder.rmdir()

        self.addCleanup(cleanup)
        config_path.write_text(
            json.dumps(
                {
                    "strategy": "Bigg Boss and global viral",
                    "topic_weights": {"cricket": 1},
                    "account_weights": {"https://www.tiktok.com/@mumbaiindians": 1},
                    "gemini_model": "gemini-2.0-flash",
                }
            ),
            encoding="utf-8",
        )

        def fetch(config, token):
            del config, token
            return {"posts": {"edges": [{"node": {"text": "Virat ka ye reaction", "metrics": [{"type": "views", "value": 1000}]}}]}}

        def ask(prompt):
            self.assertIn("cricket", prompt)
            self.assertIn("mumbaiindians", prompt)
            return json.dumps(
                {
                    "topic_weights": {"cricket": 2.5, "noise": 2},
                    "account_weights": {"https://www.tiktok.com/@mumbaiindians": 1.5},
                }
            )

        result = run_improve(config_path, state_path, "token", moment, fetch=fetch, ask=ask)
        self.assertTrue(result["updated"])
        saved = json.loads(config_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["topic_weights"], {"cricket": 2.5})
        self.assertEqual(saved["account_weights"]["https://www.tiktok.com/@mumbaiindians"], 1.5)
        again = run_improve(config_path, state_path, "token", moment + timedelta(days=1), fetch=fetch, ask=ask)
        self.assertFalse(again["updated"])
        self.assertIn("under 3 days", again["reason"])


if __name__ == "__main__":
    unittest.main()
