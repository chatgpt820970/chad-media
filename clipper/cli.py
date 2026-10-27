"""clipper check | run | mark-submitted | monitor."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from clipper.alerts import alert
from clipper.buffer_api import POSTS_QUERY, graphql, post_nodes
from clipper.engine import threshold_warning
from clipper.fetchsrc import fetch_media
from clipper.publicsrc import fetch_public_report
from clipper.loadconfig import check_bundle, load_bundle, posting_platforms, repo_root
from clipper.livepost import empty_ledger, execute_live, prefetch_exclude_ids
from clipper.run import planned_downloads, run_pipeline, write_report
from clipper.strikes import empty_alerts, record_post_statuses
from clipper.submissions import (
    empty_pending,
    empty_views,
    load_json,
    mark_submitted,
    overdue_items,
    record_sent_posts,
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


def command_fetch(bundle: dict, args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    exclude = None
    if args.respect_ledger:
        ledger = load_json(bundle["root"] / "state" / "live.json", empty_ledger())
        sui = next(row for row in bundle["campaigns"]["campaigns"] if row["id"] == "sui-basecamp")
        platforms = [name for name in posting_platforms(bundle) if name in set(sui.get("platforms") or [])]
        exclude = prefetch_exclude_ids(ledger, platforms)
    manifest = fetch_media(
        bundle,
        output,
        env=dict(os.environ),
        include_dropbox=not args.skip_dropbox,
        exclude_sui_ids=exclude,
    )
    output.mkdir(parents=True, exist_ok=True)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"fetch ok={manifest['ok']} sui={bool(manifest.get('short'))}")
    for error in manifest.get("errors") or []:
        print(f"error: {error}", file=sys.stderr)
    return 0 if manifest.get("ok") else 1


def actions_enabled(name: str) -> bool:
    """Unset is on. The lowercase string false is the off switch."""
    return os.environ.get(name, "").strip().lower() != "false"


def command_fetch_public(bundle: dict, args: argparse.Namespace) -> int:
    output = Path(args.output_dir)
    report = fetch_public_report(bundle, output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    for row in report["sources"]:
        print(f"{row['name']} {row['status']} bytes={row.get('bytes') or 0} method={row.get('method') or '-'}")
    return 0


def command_run(bundle: dict, args: argparse.Namespace) -> int:
    if args.live:
        if not actions_enabled("CLIPPER_LIVE"):
            print("Refusing a live post. CLIPPER_LIVE is false.", file=sys.stderr)
            return 2
        if not (os.environ.get("BUFFER_API_TOKEN") or "").strip():
            print("Refusing a live post. BUFFER_API_TOKEN is empty.", file=sys.stderr)
            return 2
        return execute_live(
            bundle,
            now=_now(args.now),
            env=dict(os.environ),
            media_dir=Path(args.media_dir),
            output_dir=Path(args.output_dir),
        )
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


def sync_sent_posts(bundle: dict, pending: dict) -> int:
    """Poll Buffer and write pending.json in this same run, as soon as a post is sent."""
    token = os.environ.get("BUFFER_API_TOKEN") or ""
    if not token:
        print("BUFFER_API_TOKEN unset; sent posts are not polled")
        return 0
    api = bundle["pipeline"]["buffer_api_url"]
    org = bundle["channels"]["buffer_organization_id"]
    minutes = int(bundle["pipeline"]["submit_within_minutes"])
    alerts_path = bundle["root"] / "state" / "platform_alerts.json"
    alerts = load_json(alerts_path, empty_alerts())
    added = 0
    paused = False
    when = datetime.now(timezone.utc)
    for platform, spec in bundle["channels"]["platforms"].items():
        channel = str(os.environ.get(spec.get("channel_id_env") or "") or spec["buffer_channel_id"])
        try:
            data = graphql(api, token, POSTS_QUERY, {"organizationId": org, "channelId": channel})
        except Exception as exc:
            print(f"buffer poll {platform} failed: {exc.__class__.__name__}")
            continue
        nodes = post_nodes(data)
        added += record_sent_posts(
            pending,
            nodes,
            platform,
            bundle["campaigns"]["campaigns"],
            minutes,
        )
        for note in record_post_statuses(alerts, platform, nodes, when):
            alert(note)
            paused = True
    if paused:
        save_json(alerts_path, alerts)
    if added:
        print(f"recorded {added} sent post(s) into submissions/pending.json")
    return added


def command_monitor(bundle: dict) -> int:
    if not actions_enabled("CLIPPER_MONITOR"):
        print("CLIPPER_MONITOR is false. Deadline check skipped.")
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
    sync_sent_posts(bundle, pending)
    # Persist the moment Buffer reports sent, before the deadline sweep.
    save_json(pending_path, pending)
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
    run.add_argument(
        "--live",
        action="store_true",
        help="Post the due slot via Buffer GraphQL. Refuses when CLIPPER_LIVE is false. Unset is on.",
    )
    run.add_argument("--now", default="")
    run.add_argument("--output-dir", default="work/dry-run")
    run.add_argument("--media-dir", default="work/fetch", help="fetch-media output used by --live.")
    run.add_argument("--simulate-live", action="store_true")

    fetch = sub.add_parser("fetch")
    fetch.add_argument("--output-dir", default="work/fetch")
    fetch.add_argument("--skip-dropbox", action="store_true", help="Skip the Dhan Aziz zip. Live posting does not send it.")
    fetch.add_argument(
        "--respect-ledger",
        action="store_true",
        help="Skip a Drive file only when every live Sui platform has already posted it.",
    )

    public = sub.add_parser("fetch-public")
    public.add_argument("--output-dir", default="work/public")

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
    if args.command == "fetch":
        return command_fetch(bundle, args)
    if args.command == "fetch-public":
        return command_fetch_public(bundle, args)
    if args.command == "mark-submitted":
        return command_mark_submitted(bundle, args.key)
    if args.command == "monitor":
        return command_monitor(bundle)
    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
