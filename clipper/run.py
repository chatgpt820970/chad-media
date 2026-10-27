"""Build a day's packages. Dry-run does not post, download, or touch the live queue."""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from clipper.buffer_api import build_post_input, due_stamp
from clipper.engine import (
    allocate_slots,
    build_caption,
    campaign_score,
    choose_campaign,
    content_plan_caption,
    cross_post_block,
    deal_sequence,
    fingerprint,
    foreign_tokens,
    publish_blockers,
    source_rank,
    threshold_warning,
    upcoming_slots,
    variant_for,
    views_to_first_payout,
    ytdlp_command,
)
from clipper.fetchsrc import download_command
from clipper.loadconfig import PLATFORMS, canonical_platform, check_bundle, posting_platforms
from clipper.render import logo_filter
from clipper.safety import classify
from clipper.submissions import live_item
from clipper.watermark import resolve_watermark, synthetic_frame

POSTED = {"scheduled", "reminder"}


def _clip_from_fixture(raw: dict) -> dict:
    platform = canonical_platform(raw["platform"])
    return {
        "source_id": raw.get("source_id_override") or raw["source_id"],
        "catalog_id": raw["source_id"],
        "platform": platform,
        "content_type": raw["content_type"],
        "title": raw.get("title") or raw["source_id"],
        "url": raw.get("url") or "",
        "duration_sec": raw.get("duration_sec"),
        "lane": raw.get("lane") or "editorial",
        "ready": bool(raw.get("ready")),
        "watermark": raw.get("watermark") or "clean",
    }


def editorial_plan(sources: dict, per_platform: int) -> list[dict]:
    jobs = []
    for platform, spec in sources.get("platforms", {}).items():
        weights = {item["content_type"]: float(item["weight"]) for item in spec.get("mix", [])}
        quotas = allocate_slots(weights, per_platform)
        queries = {item["content_type"]: item["q"] for item in spec.get("queries", [])}
        for content_type, count in sorted(quotas.items()):
            jobs.append(
                {
                    "platform": platform,
                    "content_type": content_type,
                    "q": queries.get(content_type, ""),
                    "count": count,
                    "lane": "editorial",
                }
            )
    return jobs


def _channel_id(channels: dict, campaign: dict, platform: str, env: dict) -> str:
    if campaign.get("account_mode") == "dedicated":
        name = (campaign.get("dedicated_channel_env") or {}).get(platform) or ""
        return str(env.get(name) or "").strip() if name else ""
    spec = channels["platforms"][platform]
    override = str(env.get(spec.get("channel_id_env") or "") or "").strip()
    return override or spec["buffer_channel_id"]


def _shell(clip: dict, **extra) -> dict:
    package = {
        "source_id": clip["source_id"],
        "fingerprint": clip.get("fingerprint") or fingerprint(clip),
        "platform": clip["platform"],
        "content_type": clip["content_type"],
        "title": clip["title"],
        "url": clip.get("url") or "",
        "campaign_id": None,
        "status": "",
        "reason": "",
        "caption": "",
        "due_at": None,
        "buffer": None,
        "manual_steps": [],
        "watermark_status": clip.get("watermark_status") or "clean",
        "variant": None,
        "logo_applied": False,
        "filter_graph": "",
        "submit_page": "",
        "content_rewards_campaign_id": "",
    }
    package.update(extra)
    return package


def _take(preferred: str, buckets: dict, scores: dict) -> tuple[dict | None, str | None]:
    if buckets.get(preferred):
        return buckets[preferred].pop(0), preferred
    for cid in sorted(scores, key=lambda key: (-scores[key], key)):
        if buckets.get(cid):
            return buckets[cid].pop(0), cid
    return None, None


def run_pipeline(
    bundle: dict,
    *,
    now: datetime,
    env: dict | None = None,
    simulate_live: bool = False,
    output_size: tuple[int, int] | None = None,
) -> dict:
    env = env or {}
    errors = check_bundle(bundle)
    campaigns = bundle["campaigns"]["campaigns"]
    by_id = {row["id"]: row for row in campaigns}
    pipeline = bundle["pipeline"]
    settings = pipeline.get("watermark") or {}
    risky = set((pipeline.get("duplicate_protection") or {}).get("risky_platforms") or [])
    threshold = float(pipeline.get("min_views_threshold") or 1500)
    warnings = [note for note in (threshold_warning(row, threshold) for row in campaigns) if note]
    active_ids = [row["id"] for row in campaigns if row.get("active")]
    approved = {str(item) for item in (bundle.get("approvals") or {}).get("approved") or []}
    exact_types = {
        content_type
        for row in campaigns
        if row.get("active") and row.get("edits") == "none"
        for content_type in row.get("content_types") or []
    }
    drafts: list[dict] = []
    width, height = output_size or (720, 1280)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    packages: list[dict] = []
    prepared: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for raw in bundle["fixtures"]["clips"]:
        clip = _clip_from_fixture(raw)
        hit = classify(clip["title"]) or classify(clip["url"])
        if hit:
            packages.append(_shell(clip, status="rejected_safety", reason=hit))
            continue
        fp = fingerprint(clip)
        clip["fingerprint"] = fp
        key = (fp, clip["platform"])
        if key in seen:
            packages.append(_shell(clip, status="duplicate", reason="same source on this platform"))
            continue
        seen.add(key)
        if clip["content_type"] in exact_types:
            clip["watermark_status"] = "as_provided"
            prepared.append(clip)
            continue
        frame = synthetic_frame(clip["watermark"])
        checked = resolve_watermark(frame, 180, 320, settings)
        clip["watermark_status"] = checked["status"]
        if checked["status"] == "skipped":
            packages.append(
                _shell(
                    clip,
                    status="skipped_watermark",
                    reason="burned-in mark remained after cover",
                    watermark_status="skipped",
                )
            )
            continue
        prepared.append(clip)

    grouped: dict[str, list[dict]] = defaultdict(list)
    for clip in prepared:
        grouped[clip["platform"]].append(clip)

    ledger: dict[str, set[str]] = defaultdict(set)
    used_captions: dict[str, set[str]] = defaultdict(set)
    open_slots: dict[str, int] = {}
    char_limit = int(pipeline["x_char_limit"])
    enabled = set(posting_platforms(bundle))

    for platform in PLATFORMS:
        if platform not in enabled:
            open_slots[platform] = 0
            continue
        limits = (pipeline.get("platform_limits") or {}).get(platform) or {}
        platform_cap = limits.get("max_duration_sec")
        buckets: dict[str, list[dict]] = defaultdict(list)
        editorial: list[dict] = []
        for clip in grouped.get(platform, []):
            if clip.get("lane") == "editorial":
                duration = clip.get("duration_sec")
                if platform_cap is not None and duration is not None and duration > float(platform_cap):
                    packages.append(_shell(clip, status="platform_too_long", reason=f"over {platform_cap}s"))
                    continue
                editorial.append(clip)
                continue
            campaign, why = choose_campaign(clip, campaigns, platform_cap)
            if campaign is None:
                if why.startswith("inactive:"):
                    status = "inactive"
                elif why in {"too_short", "too_long", "platform_too_long"}:
                    status = why
                else:
                    status = "unassigned"
                packages.append(_shell(clip, status=status, reason=why))
                continue
            channel = _channel_id(bundle["channels"], campaign, platform, env)
            blockers = publish_blockers(campaign, clip, channel)
            mode = campaign.get("cross_post") or "vary"
            block = cross_post_block(mode, platform, ledger[clip["fingerprint"]], risky)
            if block and not blockers:
                packages.append(
                    _shell(
                        clip,
                        campaign_id=campaign["id"],
                        status="risky_duplicate" if block == "risky_shortform" else "duplicate",
                        reason=block,
                    )
                )
                continue
            if blockers:
                packages.append(
                    _shell(
                        clip,
                        campaign_id=campaign["id"],
                        status=blockers[0],
                        reason=",".join(blockers),
                        manual_steps=_manual(campaign, blockers),
                        submit_page=campaign.get("submit_page") or "",
                        content_rewards_campaign_id=campaign.get("content_rewards_campaign_id") or "",
                    )
                )
                continue
            if campaign.get("approval") == "required" and clip["source_id"] not in approved:
                drafts.append(
                    {
                        "source_id": clip["source_id"],
                        "platform": platform,
                        "campaign_id": campaign["id"],
                        "title": clip["title"],
                        "url": clip.get("url") or "",
                        "status": "awaiting_approval",
                    }
                )
                packages.append(
                    _shell(
                        clip,
                        campaign_id=campaign["id"],
                        status="awaiting_approval",
                        reason="add this source_id to state/approvals.json after you approve the draft",
                        submit_page=campaign.get("submit_page") or "",
                        content_rewards_campaign_id=campaign.get("content_rewards_campaign_id") or "",
                    )
                )
                continue
            buckets[campaign["id"]].append(clip)

        scores = {}
        for cid, rows in buckets.items():
            if rows:
                score = campaign_score(by_id[cid], pipeline["allocation"])
                if score > 0:
                    scores[cid] = score
        schedule = pipeline["schedule"][platform]
        count = len(schedule["slots"])
        quotas = allocate_slots(scores, count)
        sequence = deal_sequence(quotas)
        slots = upcoming_slots(now, schedule["slots"], schedule["timezone"], count)
        filled = 0
        free_slots: list[datetime] = []
        for slot, preferred in zip(slots, sequence):
            clip, cid = _take(preferred, buckets, scores)
            if clip is None or cid is None:
                free_slots.append(slot)
                continue
            campaign = by_id[cid]
            try:
                caption = build_caption(
                    campaign,
                    clip,
                    platform,
                    char_limit,
                    limits=limits,
                    used=used_captions[platform],
                )
            except ValueError as exc:
                errors.append(str(exc))
                packages.append(_shell(clip, campaign_id=cid, status="error", reason=str(exc)))
                continue
            leaked = foreign_tokens(caption, campaign, campaigns)
            unsafe = classify(caption)
            if unsafe:
                errors.append(f"{cid} caption blocked as {unsafe}")
                packages.append(_shell(clip, campaign_id=cid, status="rejected_safety", reason=unsafe))
                continue
            if leaked:
                errors.append(f"{cid} caption includes {leaked}")
                packages.append(_shell(clip, campaign_id=cid, status="error", reason="foreign caption token"))
                continue
            mode = campaign.get("cross_post") or "vary"
            if cross_post_block(mode, platform, ledger[clip["fingerprint"]], risky):
                packages.append(_shell(clip, campaign_id=cid, status="risky_duplicate", reason="ledger"))
                continue
            status = "reminder" if campaign.get("publish_mode") == "notification" else "scheduled"
            if campaign.get("edits") == "none":
                variant = {"crop_x": 0.0, "start_sec": 0.0}
                graph, uses_logo = "copy", False
            else:
                variant = variant_for(platform)
                graph, uses_logo = logo_filter(campaign, width, height, variant["crop_x"])
            if "drawtext" in graph:
                errors.append("render graph used drawtext")
            channel = _channel_id(bundle["channels"], campaign, platform, env)
            due = due_stamp(slot)
            package = _shell(
                clip,
                campaign_id=cid,
                status=status,
                caption=caption,
                due_at=due,
                variant=variant,
                logo_applied=uses_logo,
                filter_graph=graph,
                manual_steps=_manual(campaign, ["sound"] if status == "reminder" else []),
                watermark_status=clip["watermark_status"],
                submit_page=campaign.get("submit_page") or "",
                content_rewards_campaign_id=campaign.get("content_rewards_campaign_id") or "",
            )
            video_url = f"https://example.invalid/dry-run/{clip['source_id']}-{platform}.mp4"
            package["buffer"] = build_post_input(package, channel, video_url)
            ledger[clip["fingerprint"]].add(platform)
            packages.append(package)
            filled += 1
        if len(sequence) < len(slots):
            free_slots.extend(slots[len(sequence) :])
        editorial.sort(key=lambda row: row["source_id"])
        for slot in free_slots:
            if not editorial:
                break
            clip = editorial.pop(0)
            if cross_post_block("vary", platform, ledger[clip["fingerprint"]], risky):
                packages.append(_shell(clip, status="duplicate", reason="content plan"))
                continue
            try:
                caption = content_plan_caption(
                    clip, platform, char_limit, limits=limits, used=used_captions[platform]
                )
            except ValueError as exc:
                errors.append(str(exc))
                packages.append(_shell(clip, status="error", reason=str(exc)))
                continue
            dummy = {"id": "content-plan", "caption": {"exclusive_tokens": []}}
            leaked = foreign_tokens(caption, dummy, campaigns)
            unsafe = classify(caption)
            if unsafe or leaked:
                packages.append(
                    _shell(
                        clip,
                        status="rejected_safety" if unsafe else "error",
                        reason=unsafe or "foreign caption token",
                    )
                )
                continue
            variant = variant_for(platform)
            graph, uses_logo = logo_filter({"logo": {"required": False}}, width, height, variant["crop_x"])
            if uses_logo or "drawtext" in graph:
                errors.append("content plan render used a logo or drawtext")
            channel = _channel_id(bundle["channels"], {"account_mode": "shared"}, platform, env)
            package = _shell(
                clip,
                campaign_id=None,
                status="scheduled",
                reason="content plan",
                caption=caption,
                due_at=due_stamp(slot),
                variant=variant,
                logo_applied=False,
                filter_graph=graph,
                watermark_status=clip["watermark_status"],
            )
            video_url = f"https://example.invalid/dry-run/{clip['source_id']}-{platform}.mp4"
            package["buffer"] = build_post_input(package, channel, video_url)
            ledger[clip["fingerprint"]].add(platform)
            packages.append(package)
            filled += 1
        for clip in editorial:
            packages.append(_shell(clip, status="deferred", reason="content plan daily cap"))
        for cid, rows in buckets.items():
            for clip in rows:
                packages.append(_shell(clip, campaign_id=cid, status="deferred", reason="daily cap"))
        open_slots[platform] = count - filled

    preview = []
    if simulate_live:
        for package in packages:
            if package["status"] != "scheduled" or not package.get("campaign_id") or not package.get("due_at"):
                continue
            posted_at = datetime.fromisoformat(package["due_at"].replace("Z", "+00:00"))
            preview.append(
                live_item(
                    package,
                    f"https://example.invalid/live/{package['source_id']}",
                    posted_at,
                    int(pipeline["submit_within_minutes"]),
                    buffer_post_id="dry-run",
                )
            )
            break

    slot_count = len(pipeline["schedule"]["instagram"]["slots"])
    plan = editorial_plan(bundle["sources"], slot_count)
    for job in plan:
        job["will_download"] = True
        job["lane"] = "content_plan"
    summary: dict[str, int] = defaultdict(int)
    for package in packages:
        summary[package["status"]] += 1
    scheduled_count = summary.get("scheduled", 0) + summary.get("reminder", 0)
    posting = "enabled" if scheduled_count else "disabled"
    if "instagram" in enabled:
        instagram_note = "Instagram posting is enabled. "
        dhan_note = (
            "Dhan Aziz stays in awaiting_approval until the source id is listed in state/approvals.json. "
        )
    else:
        instagram_note = (
            "Instagram is disabled until that account can link to Content Rewards. "
            "Turn platforms.instagram.enabled back to true to post Reels again. "
        )
        dhan_note = (
            "Dhan Aziz stays in awaiting_approval until the source id is listed in state/approvals.json, "
            "and it posts to YouTube only while Instagram is disabled. "
        )
    posting_note = (
        instagram_note
        + "X has no active campaign, so those slots use the owner's content plan "
        "(trending, celebrity, 4K anime) with no logo. "
        "Sui BaseCamp posts on each enabled platform that campaign allows. "
        + dhan_note
        + "Slots an active campaign does not fill use that platform's content plan, also with no logo. "
        "A views-to-first-payout warning does not turn a campaign on or off."
    )
    if posting == "disabled":
        posting_note = "Posting disabled: nothing was eligible to schedule. " + posting_note
    owner_notes = {
        row["id"]: {
            "payout_timing": row.get("payout_timing") or "",
            "approval_notes": row.get("approval_notes") or "",
        }
        for row in campaigns
    }
    payout_views = [
        {
            "id": row["id"],
            "active": bool(row.get("active")),
            "views_to_first_payout": views_to_first_payout(row),
            "min_payout": row.get("min_payout", row.get("min_payout_usd")),
            "max_payout": row.get("max_payout", row.get("max_payout_usd")),
            "min_views": row.get("min_views"),
            "payout_timing": row.get("payout_timing") or "",
            "approval_notes": row.get("approval_notes") or "",
        }
        for row in campaigns
    ]
    posts_per_day = {
        platform: len(pipeline["schedule"][platform]["slots"]) if platform in enabled else 0
        for platform in PLATFORMS
    }
    return {
        "mode": "dry-run",
        "now": due_stamp(now),
        "posting": posting,
        "posting_note": posting_note,
        "active_campaigns": active_ids,
        "drafts": drafts,
        "warnings": warnings,
        "payout_views": payout_views,
        "owner_notes": owner_notes,
        "posts_per_day": posts_per_day,
        "open_slots": open_slots,
        "packages": packages,
        "editorial_plan": plan,
        "submission_preview": preview,
        "raw_url": pipeline["submissions"]["raw_url"],
        "errors": errors,
        "summary": dict(summary),
        "campaign_notes": {row["id"]: row.get("automation_gaps") or [] for row in campaigns},
    }


def planned_downloads(report: dict, bundle: dict) -> list[dict]:
    """Download commands for posts that will go out. Campaign folders are fetched once."""
    by_id = {row["id"]: row for row in bundle["campaigns"]["campaigns"]}
    commands = []
    seen_sources: set[str] = set()
    for package in report["packages"]:
        if package["status"] not in POSTED:
            continue
        campaign = by_id.get(package.get("campaign_id") or "")
        source = (campaign or {}).get("source") or {}
        if source.get("kind") in {"google_drive_folder", "dropbox_folder"}:
            if campaign["id"] in seen_sources:
                continue
            seen_sources.add(campaign["id"])
            dest = f"work/inbox/{campaign['id']}"
            if source["kind"] == "dropbox_folder":
                dest = f"work/inbox/{campaign['id']}.zip"
            commands.append(
                {
                    "source_id": campaign["id"],
                    "platform": package["platform"],
                    "rank": 0,
                    "command": download_command(source, dest),
                    "cookies_env": "",
                }
            )
            continue
        rank = source_rank(package.get("url") or "", bundle["pipeline"])
        cookies_env = bundle["channels"]["platforms"][package["platform"]]["cookies_env"]
        commands.append(
            {
                "source_id": package["source_id"],
                "platform": package["platform"],
                "rank": rank,
                "command": ytdlp_command(package.get("url") or package["source_id"], "work/clip.mp4", None),
                "cookies_env": cookies_env,
            }
        )
    for row in bundle["campaigns"]["campaigns"]:
        source = row.get("source") or {}
        if not row.get("active") or row["id"] in seen_sources:
            continue
        if source.get("kind") not in {"google_drive_folder", "dropbox_folder"}:
            continue
        if not any(item.get("campaign_id") == row["id"] and item["status"] == "awaiting_approval" for item in report["packages"]):
            continue
        dest = f"work/inbox/{row['id']}.zip" if source["kind"] == "dropbox_folder" else f"work/inbox/{row['id']}"
        commands.append(
            {
                "source_id": row["id"],
                "platform": "",
                "rank": 0,
                "command": download_command(source, dest),
                "cookies_env": "",
            }
        )
    return commands


def write_report(report: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (output_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    payloads = [row["buffer"] for row in report["packages"] if row.get("buffer")]
    (output_dir / "buffer_payloads.json").write_text(json.dumps(payloads, indent=2) + "\n", encoding="utf-8")
    preview = {
        "automated_submit": False,
        "raw_url": report["raw_url"],
        "items": report["submission_preview"],
    }
    (output_dir / "pending_preview.json").write_text(json.dumps(preview, indent=2) + "\n", encoding="utf-8")
    (output_dir / "awaiting_approval.json").write_text(
        json.dumps({"version": 1, "items": report.get("drafts") or []}, indent=2) + "\n",
        encoding="utf-8",
    )


def render_markdown(report: dict) -> str:
    active = ", ".join(report["active_campaigns"]) or "(none)"
    lines = [
        "# Clip run",
        "",
        f"Mode: {report['mode']}",
        f"Posting: {report.get('posting', 'enabled')}",
        f"Active campaigns: {active}",
        f"Posts per day: {json.dumps(report['posts_per_day'])}",
        "",
    ]
    if report.get("posting_note"):
        lines.extend([report["posting_note"], ""])
    if report.get("warnings"):
        lines.extend(["## Views warnings", ""])
        lines.extend(f"- {warning}" for warning in report["warnings"])
        lines.append("")
        lines.append(
            "These lines are warnings. A campaign is posted only when its active flag is true."
        )
        lines.append("")
    notes = [
        (cid, note)
        for cid, note in (report.get("owner_notes") or {}).items()
        if (note.get("payout_timing") or "").strip() or (note.get("approval_notes") or "").strip()
    ]
    if notes:
        lines.extend(["## Payout timing and approval notes", ""])
        for cid, note in notes:
            if note.get("payout_timing"):
                lines.append(f"- {cid} payout timing: {note['payout_timing']}")
            if note.get("approval_notes"):
                lines.append(f"- {cid} approval notes: {note['approval_notes']}")
        lines.append("")
    if report.get("drafts"):
        lines.extend(["## Awaiting approval", ""])
        lines.append("These drafts are not posted. Add the source id to state/approvals.json after you approve it.")
        lines.append("")
        for draft in report["drafts"]:
            lines.append(f"- {draft['campaign_id']} {draft['platform']} {draft['source_id']}")
        lines.append("")
    lines.extend(["## Packages", ""])
    for package in report["packages"]:
        lines.append(
            f"- {package['platform']} {package['status']} {package.get('campaign_id') or '-'} "
            f"{package['source_id']} {package.get('caption').splitlines()[0] if package.get('caption') else ''}"
        )
    lines.extend(["", "## Open slots", ""])
    for platform, count in report["open_slots"].items():
        lines.append(f"- {platform}: {count}")
    if report["submission_preview"]:
        lines.extend(["", "## Submission preview", ""])
        for item in report["submission_preview"]:
            lines.append(
                f"- {item['id']} deadline {item['deadline_at']} url {item['post_url']}"
            )
    lines.extend(["", f"Queue file: {report['raw_url']}", ""])
    if report["errors"]:
        lines.append("## Errors")
        lines.extend(f"- {error}" for error in report["errors"])
    return "\n".join(lines) + "\n"


def _manual(campaign: dict, blockers: list[str]) -> list[str]:
    steps = []
    if "needs_account" in blockers:
        steps.append(
            "Create the Jackelyne fan pages and set JACKELYNE_BUFFER_CHANNEL_INSTAGRAM, "
            "JACKELYNE_BUFFER_CHANNEL_TIKTOK, and JACKELYNE_BUFFER_CHANNEL_YOUTUBE. "
            "Bio must include @jackyalvvarez and https://www.instagram.com/jackyalvvarez/."
        )
    if "needs_official_logo" in blockers:
        steps.append("Replace logos/jackelyne.png with the official transparent PNG and set logo.placeholder to false.")
    if "sound" in blockers or campaign.get("sound"):
        sound = campaign.get("sound") or {}
        if sound and not sound.get("automated", True):
            steps.append(
                f"Attach the TikTok sound {sound.get('title')} by {sound.get('artist')} before publishing. "
                "Buffer cannot set that sound."
            )
    return steps
