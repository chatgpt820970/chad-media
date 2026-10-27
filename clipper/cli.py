"""clipper check | run | mark-submitted | monitor."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from clipper.alerts import alert
from clipper.engine import threshold_warning
from clipper.loadconfig import check_bundle, load_bundle, repo_root
from clipper.run import planned_downloads, run_pipeline, write_report
from clipper.submissions import (
    empty_pending,
    empty_views,
    load_json,
    mark_submitted,
    overdue_items,
    save_json,
    sweep_edited,
)


def _now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    text = value.replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def _paths(bundle: dict) -> tuple[Path, Path, Path]:
    root: Path = bundle["root"]
    submissions = bundle["pipeline"]["submissions"]
    return (
        root / submissions["pending_path"],
        root / submissions["submitted_path"],
        root / submissions["views_path"],
    )


def command_check(bundle: dict) -> int:
    errors = check_bundle(bundle)
    threshold = float(bundle["pipeline"].get("min_views_threshold") or 1500)
    for row in bundle["campaigns"]["campaigns"]:
        note = threshold_warning(row, threshold)
        if note:
            print(f"warning: {note}")
    if errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        return 1
    active = [row["id"] for row in bundle["campaigns"]["campaigns"] if row.get("active")]
    if active:
        print("active campaigns: " + ", ".join(active))
    else:
        print("no campaign is active; posting stays disabled until you set active to true")
    print("config ok")
    return 0


def command_run(bundle: dict, args: argparse.Namespace) -> int:
    if args.live:
        if os.environ.get("CLIPPER_LIVE", "").lower() != "true":
            print("Refusing a live post. Set the CLIPPER_LIVE variable to true as well as --live.", file=sys.stderr)
            return 2
        print(
            "Refusing to post. Dry-run media uses example.invalid URLs. "
            "Upload rendered files to the clip-media release before a live run.",
            file=sys.stderr,
        )
        return 2
    report = run_pipeline(
        bundle,
        now=_now(args.now),
        env=dict(os.environ),
        simulate_live=args.simulate_live,
    )
    output = Path(args.output_dir)
    write_report(report, output)
    downloads = planned_downloads(report, bundle)
    (output / "downloads.json").write_text(
        __import__("json").dumps(downloads, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report.get("posting_note") or f"posting {report['posting']}")
    for warning in report["warnings"]:
        print(f"warning: {warning}")
    print(
        f"scheduled {report['summary'].get('scheduled', 0)} "
        f"reminders {report['summary'].get('reminder', 0)} "
        f"buffer payloads {sum(1 for row in report['packages'] if row.get('buffer'))}"
    )
    if report["errors"]:
        for error in report["errors"]:
            print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


def command_mark_submitted(bundle: dict, key: str) -> int:
    pending_path, submitted_path, _views = _paths(bundle)
    pipeline = bundle["pipeline"]
    pending = load_json(
        pending_path,
        empty_pending(pipeline["submissions"]["raw_url"], int(pipeline["submit_within_minutes"])),
    )
    submitted = load_json(submitted_path, {"version": 1, "items": []})
    when = datetime.now(timezone.utc)
    pending, submitted = sweep_edited(pending, submitted, when)
    try:
        pending, submitted = mark_submitted(pending, submitted, key, when)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    save_json(pending_path, pending)
    save_json(submitted_path, submitted)
    print(f"marked submitted: {key}")
    return 0


def command_monitor(bundle: dict) -> int:
    if os.environ.get("CLIPPER_MONITOR", "").lower() != "true":
        print("CLIPPER_MONITOR is not true. Deadline check skipped.")
        return 0
    pending_path, submitted_path, views_path = _paths(bundle)
    pipeline = bundle["pipeline"]
    pending = load_json(
        pending_path,
        empty_pending(pipeline["submissions"]["raw_url"], int(pipeline["submit_within_minutes"])),
    )
    submitted = load_json(submitted_path, {"version": 1, "items": []})
    views = load_json(views_path, empty_views())
    when = datetime.now(timezone.utc)
    pending, submitted = sweep_edited(pending, submitted, when)
    save_json(pending_path, pending)
    save_json(submitted_path, submitted)
    save_json(views_path, views)
    late = overdue_items(pending, when)
    if not late:
        print(f"pending {len(pending.get('items') or [])}; none overdue")
        return 0
    for item in late:
        alert(f"Content Rewards submit overdue {item['id']} deadline {item['deadline_at']}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="clipper")
    parser.add_argument("--root", default="")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check")

    run = sub.add_parser("run")
    run.add_argument("--dry-run", action="store_true", help="Plan only. This is the default.")
    run.add_argument("--live", action="store_true", help="Blocked unless CLIPPER_LIVE=true, and still refuses example.invalid URLs.")
    run.add_argument("--now", default="")
    run.add_argument("--output-dir", default="work/dry-run")
    run.add_argument("--simulate-live", action="store_true")

    mark = sub.add_parser("mark-submitted")
    mark.add_argument("--key", required=True, help="Pending item id or post URL")

    sub.add_parser("monitor")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = Path(args.root) if args.root else repo_root()
    bundle = load_bundle(root)
    if args.command == "check":
        return command_check(bundle)
    if args.command == "run":
        return command_run(bundle, args)
    if args.command == "mark-submitted":
        return command_mark_submitted(bundle, args.key)
    if args.command == "monitor":
        return command_monitor(bundle)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
