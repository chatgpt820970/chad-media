"""Command line shared by the growth bots. Each bot passes its own hooks."""

from __future__ import annotations

import argparse
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from growth.utils.alert import alert_from_log
from growth.utils.improve import run_improve
from growth.utils.review_gate import score_video
from growth.utils.buffer_guard import channel_is_ready, scheduled_count
from growth.utils.hosting import PagesPublisher
from growth.utils.media import produce
from growth.utils.pipeline import Runtime, execute, live_poster, parse_now
from growth.utils.schedule import channel_id
from growth.utils.ytdlp import Candidate, safe_id


@dataclass
class Hooks:
    root: Path
    load_config: Callable[[], dict]
    validate: Callable[[dict], list[str]]
    discover: Callable[[dict, dict], list[Candidate]]
    targets_for: Callable[[int, dict], dict[str, int] | None]
    caption_for: Callable[[Candidate], str]
    review_caption: Callable[[str, Candidate], list[str]]


def main(hooks: Hooks, argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"growth.{hooks.root.name}")
    parser.add_argument("command", choices=("check", "run", "alert", "improve"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--next-slot", action="store_true")
    parser.add_argument("--now", default="")
    args = parser.parse_args(argv)
    config = hooks.load_config()
    if args.command == "alert":
        repo = (os.environ.get("GITHUB_REPOSITORY") or "").strip()
        token = (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or "").strip()
        print(alert_from_log(str(config["platform"]), hooks.root / "state" / "log.jsonl", repo, token))
        return 0
    errors = hooks.validate(config)
    if args.command == "improve":
        if errors:
            print("\n".join(errors))
            return 1
        result = run_improve(
            hooks.root / "config.json",
            hooks.root / "state" / "improve.json",
            (os.environ.get("BUFFER_API_TOKEN") or "").strip(),
            parse_now(args.now) if args.now else None,
        )
        print(json.dumps(result))
        return 1 if result.get("error") else 0
    if args.command == "check":
        if errors:
            print("\n".join(errors))
            return 1
        print(f"{config['platform']} config ok")
        return 0
    if errors:
        print("\n".join(errors))
        return 1
    env = dict(os.environ)
    token = (env.get("BUFFER_API_TOKEN") or "").strip()
    work = hooks.root.parent.parent / "work" / f"growth-{config['platform']}"
    publisher = None
    if not args.dry_run:
        publisher = PagesPublisher(
            (env.get("GITHUB_REPOSITORY") or "").strip(),
            (env.get("GH_TOKEN") or env.get("GITHUB_TOKEN") or "").strip(),
            str(config.get("release_tag") or "clip-media"),
        )
    runtime = Runtime(
        config=config,
        state_path=hooks.root / "state" / "seen.json",
        log_path=hooks.root / "state" / "log.jsonl",
        now=parse_now(args.now),
        dry_run=args.dry_run,
        discover=lambda: hooks.discover(config, env),
        targets_for=lambda goal: hooks.targets_for(goal, config),
        caption_for=hooks.caption_for,
        review_caption=hooks.review_caption,
        prepare=lambda candidate, hook: produce(candidate, hook, config, work / safe_id(candidate.source_id), env),
        publisher=publisher,
        poster=live_poster(token) if token else None,
        token_present=bool(token),
        channel=channel_id(config, env),
        force_next=bool(args.next_slot),
        review_gate=(
            lambda prepared, candidate, _config=config, _work=work, _env=env: score_video(
                Path(prepared.local_path),
                candidate,
                str(_config.get("strategy") or ""),
                (_env.get("GEMINI_API_KEY") or "").strip(),
                str(_config.get("gemini_model") or "gemini-flash-latest"),
                _work / "review",
                int(_config.get("min_gemini_score") or 8),
                str(_config.get("platform") or ""),
            )
        )
        if config.get("repost")
        else None,
        channel_check=(lambda: channel_is_ready(token, channel_id(config, env))) if token else None,
        queue_count=(
            lambda: scheduled_count(token, str(config.get("buffer_organization_id") or ""), channel_id(config, env))
        )
        if token
        else None,
    )
    report = execute(runtime)
    post_id = ""
    outcome = ""
    for event in runtime.events:
        outcome = str(event.get("kind") or outcome)
        if event.get("kind") == "scheduled":
            post_id = str(event.get("reason") or "")
    print(
        f"GROWTH platform={config['platform']} scheduled={report.scheduled} "
        f"rejected={report.rejected} skipped={report.skipped} dry_run={report.dry_run} "
        f"outcome={outcome} buffer_post_id={post_id}"
    )
    return report.exit_code
