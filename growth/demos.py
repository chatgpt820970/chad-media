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
from growth.utils.gemini import QuotaExhausted, api_keys
from growth.utils.review_gate import score_video
from growth.utils.ytdlp import Candidate
from growth.youtube.bot import load_config as load_youtube
from growth.youtube.caption import review_caption as review_youtube
from growth.youtube.discover import discover as discover_youtube

LIMIT = 15 * 1024 * 1024
PER_PLATFORM = 3
LOVE_MINIMUM = 2
EXAMINE = 12
GEMINI_BUDGET = 8  # Gemini calls per run, across both platforms


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


class Budget:
    def __init__(self, calls: int) -> None:
        self.calls = calls
        self.exhausted = False

    def take(self) -> bool:
        if self.exhausted or self.calls <= 0:
            return False
        self.calls -= 1
        return True


def _prepare(config: dict, candidate: Candidate, work: Path):
    """Local-only pass: download, duration, resolution, OCR, render. No Gemini."""
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
    return prepared


def _review(config: dict, candidate: Candidate, prepared, work: Path, api_key: str, review) -> dict | None:
    """One combined Gemini call: approve the clip and write its caption."""
    try:
        verdict = score_video(
            Path(prepared.local_path),
            candidate,
            str(config.get("strategy") or ""),
            api_key,
            str(config.get("gemini_model") or "gemini-3.8-flash"),
            work / "review",
            int(config.get("min_gemini_score") or 8),
            str(config.get("platform") or ""),
        )
    except QuotaExhausted:
        raise
    except Exception as exc:
        print(f"SKIP gemini {str(exc)[:300]}", flush=True)
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


def _rank(row: Candidate) -> tuple:
    return (0 if row.topic == "love" else 1, -int(row.view_count or 0))


def _gather(config: dict, discover, review, api_key: str, work: Path, budget: Budget, used: set[str]) -> list[dict]:
    rows = [row for row in discover(config, dict(os.environ)) if row.url not in used]
    rows.sort(key=_rank)
    # Stage 1: local pre-filter only, so Gemini sees just the best candidates.
    ready: list[tuple[Candidate, object]] = []
    for candidate in rows[:EXAMINE]:
        prepared = _prepare(config, candidate, work)
        if prepared is not None:
            ready.append((candidate, prepared))
    print(f"LOCAL {config['platform']} passed={len(ready)} of {min(len(rows), EXAMINE)}", flush=True)
    # Stage 2: Gemini, best first, stop once enough are approved or the budget runs out.
    kept: list[dict] = []
    for candidate, prepared in ready:
        if len(kept) >= PER_PLATFORM:
            break
        if not budget.take():
            print("STOP gemini budget used up", flush=True)
            break
        try:
            item = _review(config, candidate, prepared, work, api_key, review)
        except QuotaExhausted as exc:
            budget.exhausted = True
            print(f"STOP gemini quota {str(exc)[:200]}", flush=True)
            break
        if item is not None:
            kept.append(item)
            used.add(candidate.url)
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
    keys = api_keys()
    api_key = keys[0] if keys else ""
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
    budget = Budget(int(os.environ.get("GROWTH_GEMINI_BUDGET") or GEMINI_BUDGET))
    used: set[str] = set()
    for config, discover, review in jobs:
        found = _gather(config, discover, review, api_key, work / config["platform"], budget, used)
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
