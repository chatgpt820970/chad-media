"""Gemini vision gate. A clip is scheduled only when every score is at least 8."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from growth.utils.gemini import generate_vision
from growth.utils.media import probe
from growth.utils.retry import MinorFailure
from growth.utils.ytdlp import Candidate

SCORE_KEYS = (
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
MIN_SCORE = 8
FRACTIONS = (0.05, 0.3, 0.55, 0.8)


def frame_times(duration: float, start: float, count: int = 4) -> list[float]:
    """Seek a few frames across the clip, not at a fraction of a second."""
    times: list[float] = []
    span = max(0.0, float(duration))
    origin = max(0.0, float(start))
    for fraction in FRACTIONS[:count]:
        if span <= 0:
            times.append(origin)
            continue
        at = origin + span * fraction
        cap = origin + max(0.0, span - 0.15)
        times.append(max(0.0, min(at, cap)))
    return times


def sample_frames(path: Path, work: Path, count: int = 4) -> list[Path]:
    work.mkdir(parents=True, exist_ok=True)
    try:
        _width, _height, duration, start = probe(path)
    except (MinorFailure, OSError, ValueError):
        duration, start = 0.0, 0.0
    images: list[Path] = []
    for index, moment in enumerate(frame_times(duration, start, count)):
        dest = work / f"review-{index}.jpg"
        subprocess.run(
            [
                "ffmpeg", "-y", "-ss", f"{moment:.2f}", "-i", str(path),
                "-frames:v", "1", "-vf", "scale=480:-1", "-q:v", "5", str(dest),
            ],
            capture_output=True, timeout=40, check=False,
        )
        if dest.is_file() and dest.stat().st_size > 500:
            images.append(dest)
    return images


def parse_review(text: str, min_score: int = MIN_SCORE) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("gemini review was not json")
    payload = json.loads(match.group(0))
    scores: dict[str, int] = {}
    for key in SCORE_KEYS:
        if key not in payload:
            return {"overall": 0, "scores": scores, "pass": False, "note": f"missing {key}"}
        try:
            scores[key] = int(payload[key])
        except (TypeError, ValueError):
            return {"overall": 0, "scores": scores, "pass": False, "note": f"bad score {key}"}
    overall = scores["overall"]
    passed = min(scores.values()) >= int(min_score)
    return {"overall": overall, "scores": scores, "pass": passed, "note": str(payload.get("note") or "")}


def review_prompt(candidate: Candidate, strategy: str) -> str:
    return (
        "You review a short social video before it is reposted unchanged. "
        "Score each key from 1 to 10. A clip is scheduled only when every score is 8 or higher. "
        "If a rule is unclear for this clip, decide it and say so in note. "
        "Keys: viral (real viral entertainment value), "
        "audience (the audience named in the strategy would like it; Indian audience for Indian clips), "
        "not_stock (not stock or boring footage), "
        "no_burned_text (no titles, subtitles, or other junk burned on the picture), "
        "no_watermark (no third-party logo or watermark), "
        "not_cut_off (nothing important is sliced off the screen), "
        "no_copyright_risk (not a full episode, movie, or long copyrighted program), "
        "safe (not adult or offensive), overall. "
        f"Strategy: {strategy} "
        f"Title: {candidate.title}. Source: {candidate.url}. "
        f"Views: {candidate.view_count}. Likes: {candidate.like_count}. "
        "Return only JSON with those keys and a short note."
    )


def score_video(
    path: Path,
    candidate: Candidate,
    strategy: str,
    api_key: str,
    model: str,
    work: Path,
    min_score: int = MIN_SCORE,
) -> dict:
    frames = sample_frames(path, work)
    if not frames or not api_key:
        return {"overall": 0, "scores": {}, "pass": False, "note": "review frames or key missing"}
    images = [("image/jpeg", frame.read_bytes()) for frame in frames]
    text = generate_vision(review_prompt(candidate, strategy), images, api_key, model)
    return parse_review(text, min_score)
