"""Candidate loop shared by the three bots.

Platform modules supply captions, lane targets, and config. They do not call
each other. A bad candidate is logged and skipped. The run exits cleanly.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from clipper.buffer_api import BufferError, create_post, due_stamp, is_rate_limited

from growth.utils.dedupe import (
    clear_failure,
    load_state,
    posted_due,
    record_failure,
    remember,
    save_state,
    seen,
)
from growth.utils.hosting import asset_name
from growth.utils.log import append_events
from growth.utils.media import Prepared, apply_light_touch, is_vertical
from growth.utils.quality import copyright_reason, media_reasons, promo_reason, resolution_reason
from growth.utils.retry import MinorFailure, call_with_retry
from growth.utils.schedule import buffer_payload, channel_id, lane_for_index, match_run, next_future_slot, slot_clocks
from growth.utils.ytdlp import Candidate

OrderFn = Callable[[list[Candidate], dict[str, int] | None], list[Candidate]]
TargetsFn = Callable[[int], dict[str, int] | None]


@dataclass
class Runtime:
    config: dict
    state_path: Path
    log_path: Path
    now: datetime
    dry_run: bool
    discover: Callable[[], list[Candidate]]
    targets_for: TargetsFn
    caption_for: Callable[[Candidate], str]
    review_caption: Callable[[str, Candidate], list[str]]
    prepare: Callable[[Candidate, str], Prepared]
    publisher: object | None = None
    poster: Callable[[dict], dict] | None = None
    token_present: bool = False
    channel: str = ""
    force_next: bool = False
    channel_check: Callable[[], tuple[bool, str]] | None = None
    queue_count: Callable[[], int] | None = None
    review_gate: Callable[..., dict] | None = None
    events: list[dict] = field(default_factory=list)


@dataclass
class Report:
    scheduled: int = 0
    rejected: int = 0
    skipped: int = 0
    dry_run: bool = False
    exit_code: int = 0


def _emit(runtime: Runtime, kind: str, **kwargs: object) -> None:
    row = {"kind": kind, "platform": runtime.config["platform"], "at": runtime.now.isoformat()}
    row.update(kwargs)
    runtime.events.append(row)
    source = kwargs.get("source_id") or ""
    reason = kwargs.get("reason") or ""
    print(f"[growth {runtime.config['platform']}] {kind} {source} {reason}".rstrip(), flush=True)


def priority_order(candidates: list[Candidate], targets: dict[str, int] | None) -> list[Candidate]:
    if not targets:
        return list(candidates)
    buckets: dict[str, list[Candidate]] = {lane: [] for lane in targets}
    other: list[Candidate] = []
    for candidate in candidates:
        if candidate.lane in buckets:
            buckets[candidate.lane].append(candidate)
        else:
            other.append(candidate)
    ordered: list[Candidate] = []
    remaining = {lane: count for lane, count in targets.items()}
    while any(remaining[lane] > 0 and buckets[lane] for lane in targets):
        for lane in targets:
            if remaining[lane] <= 0 or not buckets[lane]:
                continue
            ordered.append(buckets[lane].pop(0))
            remaining[lane] -= 1
    for lane in targets:
        ordered.extend(buckets[lane])
    ordered.extend(other)
    return ordered


def _lane_needed(done: dict[str, int], targets: dict[str, int] | None) -> list[str] | None:
    if not targets:
        return None
    return [lane for lane, count in targets.items() if done.get(lane, 0) < count]


def _metadata_reasons(candidate: Candidate, config: dict) -> list[str]:
    reasons: list[str] = []
    if promo_reason(candidate.title) or promo_reason(candidate.description):
        if config.get("reject_promo"):
            reasons.append("ad or promo")
    copy = copyright_reason(
        candidate.title,
        candidate.description,
        candidate.duration,
        float(config.get("max_source_seconds") or 4800),
    )
    if copy:
        reasons.append(copy)
    resolution = resolution_reason(candidate.width, candidate.height, int(config["min_resolution"]))
    if resolution:
        reasons.append(resolution)
    return reasons


def _output_reason(prepared: Prepared, config: dict) -> str | None:
    low = float(config["clip_min_seconds"])
    high = float(config["clip_max_seconds"])
    if prepared.duration and prepared.duration < low:
        return "shorter than the clip minimum"
    if prepared.output_seconds and (prepared.output_seconds < low - 0.2 or prepared.output_seconds > high + 0.2):
        return "rendered length outside the clip window"
    return None


def execute(runtime: Runtime) -> Report:
    config = runtime.config
    report = Report(dry_run=runtime.dry_run)
    state = load_state(runtime.state_path)
    if not config.get("enabled", False) and not runtime.dry_run:
        _emit(runtime, "disabled", reason="config enabled is false")
        append_events(runtime.log_path, runtime.events)
        return report
    if not runtime.dry_run and not runtime.token_present:
        _emit(runtime, "buffer_token_missing", reason="BUFFER_API_TOKEN is empty")
        append_events(runtime.log_path, runtime.events)
        return report
    if not runtime.channel:
        _emit(runtime, "channel_missing", reason="buffer channel id is empty")
        append_events(runtime.log_path, runtime.events)
        return report

    hit = next_future_slot(runtime.now, config) if runtime.force_next else match_run(
        runtime.now, config, tolerance=int(config.get("lead_tolerance") or 4)
    )
    if not hit:
        _emit(runtime, "no_slot_in_window")
        append_events(runtime.log_path, runtime.events)
        return report
    due = due_stamp(hit["when"])
    role = str(hit["role"])
    if posted_due(state, due):
        _emit(runtime, "slot_already_posted", reason=hit["time"])
        append_events(runtime.log_path, runtime.events)
        return report
    if not runtime.dry_run:
        if runtime.channel_check is None or runtime.queue_count is None:
            _emit(runtime, "buffer_check_missing", reason="channel and queue checks are required")
            _finish_failure(runtime, report, state, due, role, "buffer checks missing")
            return report
        ok, reason = runtime.channel_check()
        if not ok:
            _emit(runtime, "channel_unavailable", reason=reason or "channel missing")
            append_events(runtime.log_path, runtime.events)
            save_state(runtime.state_path, state)
            return report
        try:
            queued = int(runtime.queue_count())
        except Exception as exc:
            _emit(runtime, "queue_check_failed", reason=str(exc)[:240])
            _finish_failure(runtime, report, state, due, role, str(exc)[:240])
            return report
        limit = int(config.get("queue_skip_at") or 9)
        if queued >= limit:
            _emit(runtime, "queue_full", reason=f"{queued} scheduled posts already queued")
            append_events(runtime.log_path, runtime.events)
            save_state(runtime.state_path, state)
            return report
    clocks = slot_clocks(config)
    index = clocks.index(hit["time"]) if hit["time"] in clocks else 0
    full_targets = runtime.targets_for(len(clocks))
    targets = {lane_for_index(index, full_targets): 1} if full_targets else None
    slots = [hit["when"]]
    goal = 1

    try:
        discovered = runtime.discover()
    except Exception as exc:
        _emit(runtime, "discover_failed", reason=str(exc)[:300])
        append_events(runtime.log_path, runtime.events)
        return report

    fresh: list[Candidate] = []
    for row in discovered:
        if seen(state, row.source_id, row.title):
            _emit(runtime, "duplicate", source_id=row.source_id, reason="already logged")
            continue
        fresh.append(row)
    ordered = priority_order(fresh, targets)
    done: dict[str, int] = {}
    ready: list[tuple[Candidate, Prepared, str, datetime]] = []
    examined = 0
    max_examined = int(config.get("max_examined") or 18)

    for index, candidate in enumerate(ordered):
        if len(ready) >= goal or examined >= max_examined:
            break
        needed = _lane_needed(done, targets)
        if needed is not None and candidate.lane not in needed:
            later = any(item.lane in needed for item in ordered[index + 1 :])
            if later:
                continue
        meta = _metadata_reasons(candidate, config)
        if meta:
            report.rejected += 1
            if not runtime.dry_run:
                remember(state, candidate.source_id, candidate.title, None)
            _emit(runtime, "rejected", source_id=candidate.source_id, reason=meta[0])
            continue
        caption = runtime.caption_for(candidate)
        caption_notes = runtime.review_caption(caption, candidate)
        if caption_notes:
            report.rejected += 1
            if not runtime.dry_run:
                remember(state, candidate.source_id, candidate.title, None)
            _emit(runtime, "rejected", source_id=candidate.source_id, reason=caption_notes[0])
            continue
        if runtime.dry_run:
            slot = slots[len(ready)]
            payload = buffer_payload(config["platform"], caption, runtime.channel, f"https://example.invalid/{config['platform']}/{candidate.source_id}.mp4", slot)
            ready.append((candidate, Prepared(candidate.source_id, 0, 0, candidate.duration or 0), caption, slot))
            _emit(
                runtime,
                "dry_run",
                source_id=candidate.source_id,
                reason=caption[:80],
                due_at=payload["dueAt"],
                channel=payload["channelId"],
            )
            done[candidate.lane] = done.get(candidate.lane, 0) + 1
            continue
        examined += 1
        hook = _hook(caption)
        try:
            prepared = call_with_retry(lambda candidate=candidate, hook=hook: runtime.prepare(candidate, hook), attempts=2)
        except MinorFailure as exc:
            report.skipped += 1
            _emit(runtime, "skipped", source_id=candidate.source_id, reason=str(exc)[:300])
            continue
        except Exception as exc:
            report.skipped += 1
            _emit(runtime, "skipped", source_id=candidate.source_id, reason=str(exc)[:300])
            continue
        reasons = media_reasons(
            prepared.width,
            prepared.height,
            prepared.duration,
            prepared.ocr_text,
            list(prepared.corner_hits),
            prepared.edge_cutoff,
            prepared.scanners_unavailable,
            int(config["min_resolution"]),
        )
        copy = copyright_reason(
            candidate.title,
            candidate.description,
            prepared.duration,
            float(config.get("max_source_seconds") or 4800),
        )
        if copy:
            reasons.append(copy)
        output_note = _output_reason(prepared, config)
        if output_note:
            reasons.append(output_note)
        if config.get("repost") and prepared.width and prepared.height and not is_vertical(prepared.width, prepared.height):
            reasons.append("not native vertical")
        if not prepared.local_path and "scanners unavailable" not in reasons and "not native vertical" not in reasons:
            reasons.append("render produced no file")
        if not reasons and runtime.review_gate is not None and not runtime.dry_run:
            try:
                verdict = runtime.review_gate(prepared, candidate)
            except Exception as exc:
                reasons.append(f"gemini review failed: {exc}"[:240])
            else:
                _emit(
                    runtime,
                    "gemini_score",
                    source_id=candidate.source_id,
                    reason=f"overall={verdict.get('overall')} pass={verdict.get('pass')} scores={verdict.get('scores')} note={verdict.get('note')}",
                )
                if not verdict.get("pass"):
                    reasons.append(f"gemini score {verdict.get('overall')}")
                else:
                    watched = str(verdict.get("caption") or "").strip()
                    caption_notes = runtime.review_caption(watched, candidate) if watched else ["gemini caption missing"]
                    if caption_notes:
                        reasons.append(caption_notes[0])
                    else:
                        caption = watched
                        tweaks = verdict.get("tweaks") or {}
                        touched = Path(prepared.local_path).with_name(Path(prepared.local_path).stem + "-touch.mp4")
                        try:
                            touched_duration = apply_light_touch(
                                Path(prepared.local_path),
                                touched,
                                tweaks,
                                float(config["clip_min_seconds"]),
                            )
                        except Exception as exc:
                            reasons.append(f"light edit failed: {exc}"[:240])
                        else:
                            prepared = Prepared(
                                source_id=prepared.source_id,
                                width=prepared.width,
                                height=prepared.height,
                                duration=prepared.duration,
                                ocr_text=prepared.ocr_text,
                                corner_hits=prepared.corner_hits,
                                edge_cutoff=prepared.edge_cutoff,
                                scanners_unavailable=prepared.scanners_unavailable,
                                output_seconds=touched_duration,
                                local_path=str(touched),
                                zoom=False,
                            )
        if reasons:
            report.rejected += 1
            permanent = "scanners unavailable" not in reasons
            if permanent:
                remember(state, candidate.source_id, candidate.title, None)
            _emit(runtime, "rejected", source_id=candidate.source_id, reason=reasons[0])
            continue
        slot = slots[len(ready)]
        ready.append((candidate, prepared, caption, slot))
        done[candidate.lane] = done.get(candidate.lane, 0) + 1

    if runtime.dry_run:
        report.scheduled = len(ready)
        _emit(runtime, "done", scheduled=report.scheduled, rejected=report.rejected, skipped=report.skipped)
        append_events(runtime.log_path, runtime.events)
        return report

    if not ready:
        _finish_failure(runtime, report, state, due, role, "every candidate failed")
        return report

    publisher = runtime.publisher
    if publisher is None:
        _finish_failure(runtime, report, state, due, role, "no media publisher")
        return report

    posted_rows: list[tuple[Candidate, str, str, datetime, str]] = []
    for candidate, prepared, caption, slot in ready:
        name = asset_name(str(config["asset_prefix"]), candidate.source_id)
        try:
            url = publisher.add(prepared.local_path, name)  # type: ignore[attr-defined]
        except Exception as exc:
            report.skipped += 1
            _emit(runtime, "skipped", source_id=candidate.source_id, reason=str(exc)[:300])
            continue
        posted_rows.append((candidate, caption, url, slot, name))
    errors: dict[str, str] = {}
    if posted_rows:
        try:
            errors = publisher.finalize()  # type: ignore[attr-defined]
        except Exception as exc:
            errors = {name: str(exc)[:300] for _candidate, _caption, _url, _slot, name in posted_rows}
    if runtime.poster is None:
        _finish_failure(runtime, report, state, due, role, "Buffer poster is not configured")
        return report
    poster = runtime.poster
    for candidate, caption, url, slot, name in posted_rows:
        if errors.get(name):
            report.skipped += 1
            _emit(runtime, "skipped", source_id=candidate.source_id, reason=errors[name])
            continue
        payload = buffer_payload(config["platform"], caption, runtime.channel, url, slot)
        post = None
        error: Exception | None = None
        for _attempt in range(2):
            try:
                post = poster(payload)
                error = None
                break
            except BufferError as exc:
                error = exc
                if is_rate_limited(str(exc)):
                    break
            except Exception as exc:
                error = exc
        if isinstance(error, BufferError) and is_rate_limited(str(error)):
            _emit(runtime, "rate_limited", reason=str(error)[:300])
            break
        if error is not None or post is None:
            report.skipped += 1
            _emit(runtime, "skipped", source_id=candidate.source_id, reason=str(error or "empty Buffer response")[:300])
            continue
        remember(
            state,
            candidate.source_id,
            candidate.title,
            {
                "source_id": candidate.source_id,
                "due_at": payload["dueAt"],
                "buffer_post_id": (post or {}).get("id") or "",
                "caption": caption[:180],
                "lane": candidate.lane,
            },
        )
        report.scheduled += 1
        _emit(runtime, "scheduled", source_id=candidate.source_id, reason=(post or {}).get("id") or "", due_at=payload["dueAt"])
    if report.scheduled:
        clear_failure(state, due)
        _emit(runtime, "done", scheduled=report.scheduled, rejected=report.rejected, skipped=report.skipped)
        save_state(runtime.state_path, state)
        append_events(runtime.log_path, runtime.events)
        return report
    _finish_failure(runtime, report, state, due, role, "buffer did not accept the post")
    return report


def _finish_failure(runtime: Runtime, report: Report, state: dict, due: str, role: str, reason: str) -> None:
    record_failure(state, due, reason)
    kind = "backup_failed" if role == "backup" else "primary_failed"
    if role == "backup":
        report.exit_code = 1
    _emit(runtime, kind, reason=reason[:240])
    save_state(runtime.state_path, state)
    append_events(runtime.log_path, runtime.events)


def _hook(caption: str) -> str:
    line = re.sub(r"#\w+", "", caption.splitlines()[0] if caption else "")
    return " ".join(line.split())[:80]


def live_poster(token: str, url: str = "https://api.buffer.com") -> Callable[[dict], dict]:
    def post(payload: dict) -> dict:
        return create_post(url, token, payload)

    return post


def parse_now(text: str) -> datetime:
    if not text:
        return datetime.now(timezone.utc)
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment
