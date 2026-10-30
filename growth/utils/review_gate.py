"""Gemini watches the mp4, audio included, before a clip can be scheduled."""

from __future__ import annotations

import json
import re
from pathlib import Path

from growth.utils.gemini import watch_video
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
FRACTIONS = (0.08, 0.22, 0.38, 0.54, 0.70, 0.86)


def frame_times(duration: float, start: float, count: int = 6) -> list[float]:
    """Six points across a clip. The local logo scan reads every corner at each one."""
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


def parse_review(text: str, min_score: int = MIN_SCORE) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        raise ValueError("gemini review was not json")
    payload = json.loads(match.group(0))
    scores: dict[str, int] = {}
    for key in SCORE_KEYS:
        if key not in payload:
            return {"overall": 0, "scores": scores, "pass": False, "note": f"missing {key}", "caption": "", "tweaks": {}}
        try:
            scores[key] = int(payload[key])
        except (TypeError, ValueError):
            return {"overall": 0, "scores": scores, "pass": False, "note": f"bad score {key}", "caption": "", "tweaks": {}}
    overall = scores["overall"]
    passed = min(scores.values()) >= int(min_score)
    return {
        "overall": overall,
        "scores": scores,
        "pass": passed,
        "note": str(payload.get("note") or ""),
        "caption": str(payload.get("caption") or "").strip(),
        "tweaks": parse_tweaks(payload, scores.get("no_burned_text", 0)),
    }


def parse_tweaks(payload: dict, no_burned_text: int) -> dict:
    """Clamp Gemini's light-edit choices. Mirror only when the picture has no text."""
    try:
        speed = float(payload.get("speed") or 1.03)
    except (TypeError, ValueError):
        speed = 1.03
    speed = min(1.05, max(1.02, speed))
    try:
        seconds = float(payload.get("trim_seconds") or 0.4)
    except (TypeError, ValueError):
        seconds = 0.4
    seconds = min(0.5, max(0.3, seconds))
    trim = str(payload.get("trim") or "start")
    if trim not in ("start", "end"):
        trim = "start"
    mirror = bool(payload.get("mirror", False)) and int(no_burned_text) >= 9
    return {
        "speed": round(speed, 3),
        "grade": bool(payload.get("grade", True)),
        "mirror": mirror,
        "trim": trim,
        "trim_seconds": round(seconds, 2),
    }


def review_prompt(candidate: Candidate, strategy: str, platform: str) -> str:
    shorts = " Include #shorts in the hashtags." if platform == "youtube" else ""
    return (
        "Watch this entire video, including the audio. Review it the way a person would before reposting it unchanged. "
        "Do not judge from a single frame. "
        "Score each key from 1 to 10. Approve the clip only when every score is 8 or higher. "
        "If a rule is unclear, decide it and say so in note. "
        "viral: real entertainment value, not a dull clip. "
        "audience: an Indian audience would actually want this. Foreign sport with no Indian star, and clips that are not about India, score low. "
        "not_stock: not stock or boring footage. "
        "no_burned_text: no titles, subtitles, or other junk burned on the picture. "
        "no_watermark: no third-party watermark and no broadcaster or event logo. "
        "ICC, IPL, World Cup, Star Sports, Hotstar, JioCinema, Sony, Colors, ESPN, NBA, and FIFA bugs all score 1. "
        "Any corner logo or watermark at all, including music label bugs like T-Series, Zee Music / Z Music, Saregama, Sony Music, Tips, YRF, Eros, Shemaroo, scores 1. "
        "Trailers, teasers, 'book tickets' or 'in cinemas' promos, jukeboxes and lyrical videos score 1 on no_copyright_risk. "
        "Devotional or religious content (Ram, Ramayana, Ravana, Shiv/Mahadev, Krishna, Hanuman, bhajan, aarti, mantra) scores 1 on audience. "
        "A blurry, soft, or low-quality picture, or faces cut by the frame, scores 1 on not_cut_off. "
        "A clip that ends abruptly mid-word or mid-lyric scores at most 6 on viral. "
        "not_cut_off: nothing important is sliced off. A widescreen match or scene that was cropped into a vertical frame, so the action or a person is cut by the left or right edge, scores 1. "
        "A clip filmed vertically, with the subject fully in frame, can score high. "
        "no_copyright_risk: not a full episode, movie, or long program. "
        "safe: not adult or offensive. "
        "overall: your decision after watching. "
        "Then write caption from what you saw and heard. Match the mood: funny for comedy, soft and romantic for a love clip, emotional for an emotional clip, hype for sport. "
        "Natural Hinglish, like a person texting a friend, with the celebrity or show name when you know it. "
        "One short line, then 3 to 5 hashtags. No emoji, no stock phrase, no links, and never copy the source title."
        f"{shorts} "
        "Also choose a light edit. speed is 1.02 to 1.05. grade is true for a subtle color change. "
        "mirror is true only when no words are written on the picture. trim is start or end. trim_seconds is 0.3 to 0.5. "
        "Do not ask for burned-in text, a crop, a zoom, or blurred bars. Keep the original song. "
        f"Strategy: {strategy} "
        f"Title: {candidate.title}. Source: {candidate.url}. "
        f"Views: {candidate.view_count}. Likes: {candidate.like_count}. "
        "Return only JSON with the score keys, note, caption, speed, grade, mirror, trim, and trim_seconds. "
        "Put a newline between the caption line and the hashtags."
    )


def score_video(
    path: Path,
    candidate: Candidate,
    strategy: str,
    api_key: str,
    model: str,
    work: Path,
    min_score: int = MIN_SCORE,
    platform: str = "",
) -> dict:
    del work
    if not path.is_file() or not api_key:
        return {"overall": 0, "scores": {}, "pass": False, "note": "video or key missing", "caption": ""}
    text = watch_video(path, review_prompt(candidate, strategy, platform), api_key, model)
    return parse_review(text, min_score)
