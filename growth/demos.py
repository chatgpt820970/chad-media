"""Build 3 YouTube and 3 Instagram sample reposts. Does not call Buffer.

Run with GEMINI_API_KEY set. At least two of the six clips must be love/romantic.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from growth.instagram.bot import load_config as load_instagram
from growth.instagram.caption import review_caption as review_instagram
from growth.instagram.discover import discover as discover_instagram
from growth.utils.media import apply_light_touch, is_vertical, produce
from growth.utils.pipeline import media_reasons
from growth.utils.quality import copyright_reason
from growth.utils.review_gate import score_video
from growth.utils.ytdlp import Candidate
from growth.youtube.bot import load_config as load_youtube
from growth.youtube.caption import review_caption as review_youtube
from growth.youtube.discover import discover as discover_youtube

LIMIT = 15 * 1024 * 1024
PER_PLATFORM = 3
LOVE_MINIMUM = 2
EXAMINE = 12


def _shrink(path: Path) -> Path:
    if path.stat().st_size <= LIMIT:
        return path
    dest = path.with_name(path.stem + "-small.mp4")
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(path),
            "-c:v", "libx264", "-preset", "medium", "-crf", "23",
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dest),
        ],
        capture_output=True,
        timeout=240,
        check=False,
    )
    if dest.is_file() and dest.stat().st_size <= LIMIT:
        return dest
    return path


def _local_reasons(config: dict, candidate: Candidate, prepared) -> list[str]:
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
    copy = copyright_reason(candidate.title, candidate.description, prepared.duration, float(config.get("max_source_seconds") or 4800))
    if copy:
        reasons.append(copy)
    low = float(config["clip_min_seconds"])
    high = float(config["clip_max_seconds"])
    if prepared.duration and (prepared.duration < low or prepared.duration > high):
        reasons.append(f"duration outside the clip window ({prepared.duration:.1f}s not in {low:g}-{high:g}s)")
    if prepared.width and prepared.height and not is_vertical(prepared.width, prepared.height):
        reasons.append("not native vertical")
    if not prepared.local_path and "not native vertical" not in reasons:
        reasons.append(
            f"render produced no file (hits={list(prepared.corner_hits)} cutoff={prepared.edge_cutoff} "
            f"scanners_unavailable={prepared.scanners_unavailable} size={prepared.width}x{prepared.height} dur={prepared.duration:.1f})"
        )
    return reasons


def _consider(config: dict, candidate: Candidate, work: Path, api_key: str, review) -> dict | None:
    print(f"TRY {config['platform']} {candidate.topic} views={candidate.view_count} {candidate.url}", flush=True)
    try:
        prepared = produce(candidate, "", config, work / candidate.source_id, dict(os.environ))
    except Exception as exc:
        print(f"SKIP download {exc}", flush=True)
        return None
    reasons = _local_reasons(config, candidate, prepared)
    if reasons:
        print(f"SKIP local {reasons[0]}", flush=True)
        return None
    try:
        verdict = score_video(
            Path(prepared.local_path),
            candidate,
            str(config.get("strategy") or ""),
            api_key,
            str(config.get("gemini_model") or "gemini-2.0-flash"),
            work / "review",
            int(config.get("min_gemini_score") or 8),
            str(config.get("platform") or ""),
        )
    except Exception as exc:
        print(f"SKIP gemini {exc}", flush=True)
        return None
    print(f"SCORE pass={verdict.get('pass')} overall={verdict.get('overall')} {verdict.get('scores')} note={verdict.get('note')}", flush=True)
    if not verdict.get("pass"):
        return None
    caption = str(verdict.get("caption") or "").strip()
    notes = review(caption, candidate) if caption else ["gemini caption missing"]
    if notes:
        print(f"SKIP caption {notes[0]}", flush=True)
        return None
    touched = Path(prepared.local_path).with_name(Path(prepared.local_path).stem + "-touch.mp4")
    try:
        apply_light_touch(Path(prepared.local_path), touched, verdict.get("tweaks") or {}, float(config["clip_min_seconds"]))
    except Exception as exc:
        print(f"SKIP edit {exc}", flush=True)
        return None
    final = _shrink(touched)
    if final.stat().st_size > LIMIT:
        print("SKIP over 15MB", flush=True)
        return None
    return {
        "platform": config["platform"],
        "topic": candidate.topic,
        "source": candidate.url,
        "title": candidate.title,
        "views": candidate.view_count,
        "likes": candidate.like_count,
        "scores": verdict.get("scores") or {},
        "overall": verdict.get("overall"),
        "note": verdict.get("note") or "",
        "tweaks": verdict.get("tweaks") or {},
        "caption": caption,
        "path": str(final),
        "bytes": final.stat().st_size,
    }


def _gather(config: dict, discover, review, api_key: str, work: Path) -> list[dict]:
    rows = discover(config, dict(os.environ))
    rows.sort(key=lambda row: (0 if row.topic == "love" else 1, -int(row.view_count or 0)))
    kept: list[dict] = []
    examined = 0
    for candidate in rows:
        if len(kept) >= PER_PLATFORM or examined >= EXAMINE:
            break
        examined += 1
        item = _consider(config, candidate, work, api_key, review)
        if item is None:
            continue
        kept.append(item)
    return kept


def _write(folder: Path, items: list[dict]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Growth demos",
        "",
        "Gemini watched each mp4, audio included, and approved it before the light edit. These files were not sent to Buffer.",
        "",
    ]
    counters = {"youtube": 0, "instagram": 0}
    for item in items:
        counters[item["platform"]] += 1
        name = f"{item['platform']}-{counters[item['platform']]}-{item['topic']}.mp4"
        dest = folder / name
        shutil.copy2(item["path"], dest)
        item["file"] = name
        lines.extend(
            [
                f"## {name}",
                f"- Source: {item['source']}",
                f"- Views: {item['views']}",
                f"- Likes: {item['likes']}",
                f"- Topic: {item['topic']}",
                f"- Gemini overall: {item['overall']}",
                f"- Gemini scores: {json.dumps(item['scores'], ensure_ascii=False)}",
                f"- Note: {item['note']}",
                f"- Tweaks: {json.dumps(item['tweaks'], ensure_ascii=False)}",
                "- Caption:",
                "",
                item["caption"],
                "",
            ]
        )
    (folder / "captions.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "report.json").write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    api_key = (os.environ.get("GEMINI_API_KEY") or "").strip()
    if not api_key:
        print("GEMINI_API_KEY is empty")
        return 1
    os.environ["GROWTH_KEEP_UNDATED"] = "1"
    folder = Path(os.environ.get("GROWTH_DEMO_DIR") or "work/growth-demos")
    work = folder / "scratch"
    jobs = (
        (load_youtube(), discover_youtube, review_youtube),
        (load_instagram(), discover_instagram, review_instagram),
    )
    saved: list[dict] = []
    for config, discover, review in jobs:
        found = _gather(config, discover, review, api_key, work / config["platform"])
        print(f"KEPT {config['platform']} {len(found)}", flush=True)
        saved.extend(found)
    love = sum(1 for item in saved if item["topic"] == "love")
    counts = {name: sum(1 for item in saved if item["platform"] == name) for name in ("youtube", "instagram")}
    if counts["youtube"] < PER_PLATFORM or counts["instagram"] < PER_PLATFORM or love < LOVE_MINIMUM:
        print(f"NOT ENOUGH youtube={counts['youtube']} instagram={counts['instagram']} love={love}")
        return 1
    _write(folder, saved)
    print(json.dumps([{key: item[key] for key in ("file", "source", "views", "overall", "topic", "caption")} for item in saved], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
