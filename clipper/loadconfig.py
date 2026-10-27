"""Load pipeline JSON and inbox files."""

from __future__ import annotations

import json
from pathlib import Path

from clipper.safety import classify

ROOT = Path(__file__).resolve().parents[1]
PLATFORMS = ("x", "youtube", "instagram", "tiktok")
PLATFORM_ALIASES = {
    "x": "x",
    "twitter": "x",
    "youtube": "youtube",
    "youtube_shorts": "youtube",
    "shorts": "youtube",
    "instagram": "instagram",
    "instagram_reels": "instagram",
    "reels": "instagram",
    "tiktok": "tiktok",
}
RETIRED_TOKENS = (
    "flip.gg",
    "#FlipGG",
    "#ArraFund",
    "ArraFund",
    "#MrPa",
    "Mr. Pa",
    "dishpilot",
    "#Dishpilot",
    "#Rascals",
    "fastloanpilot",
)


def repo_root() -> Path:
    return ROOT


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must be a JSON object")
    return data


def load_bundle(root: Path | None = None) -> dict:
    root = root or ROOT
    campaigns = _read_json(root / "config" / "campaigns.json")
    channels = _read_json(root / "config" / "channels.json")
    sources = _read_json(root / "config" / "sources.json")
    pipeline = _read_json(root / "config" / "pipeline.json")
    fixtures = _read_json(root / "config" / "fixtures" / "catalog.json")
    approvals_path = root / "state" / "approvals.json"
    if approvals_path.is_file():
        approvals = _read_json(approvals_path)
    else:
        approvals = {"version": 1, "approved": []}
    return {
        "root": root,
        "campaigns": campaigns,
        "channels": channels,
        "sources": sources,
        "pipeline": pipeline,
        "fixtures": fixtures,
        "approvals": approvals,
    }


def active_campaigns(bundle: dict) -> list[dict]:
    return [row for row in bundle["campaigns"]["campaigns"] if row.get("active")]


def posting_platforms(bundle: dict) -> list[str]:
    """Platforms that may receive a post. Missing `enabled` stays on.

    Instagram is left in the campaign lists and in the slot clock. Set
    `channels.platforms.instagram.enabled` to false to skip it until that
    account can link to Content Rewards, then set it back to true.
    """
    channels = (bundle.get("channels") or {}).get("platforms") or {}
    enabled: list[str] = []
    for platform in PLATFORMS:
        spec = channels.get(platform) or {}
        if spec.get("enabled", True):
            enabled.append(platform)
    return enabled


def campaign_map(bundle: dict) -> dict[str, dict]:
    return {row["id"]: row for row in bundle["campaigns"]["campaigns"]}


def canonical_platform(name: str) -> str:
    key = (name or "").strip().lower()
    if key not in PLATFORM_ALIASES:
        raise ValueError(f"unknown platform {name!r}")
    return PLATFORM_ALIASES[key]


def iter_queries(sources: dict) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for platform, spec in sources.get("platforms", {}).items():
        for query in spec.get("queries", []):
            found.append((f"{platform}:{query.get('content_type')}", str(query.get("q", ""))))
    for campaign_id, spec in sources.get("campaign_queries", {}).items():
        for query in spec.get("queries", []):
            found.append((f"{campaign_id}:{query.get('content_type')}", str(query.get("q", ""))))
    return found


def check_bundle(bundle: dict) -> list[str]:
    errors: list[str] = []
    rows = bundle["campaigns"]["campaigns"]
    ids = [row["id"] for row in rows]
    if len(ids) != len(set(ids)):
        errors.append("campaign ids must be unique")
    removed = {row["id"] for row in bundle["campaigns"].get("removed", [])}
    for retired in ("dishpilot", "flipgg", "mrpa", "arrafund"):
        if retired not in removed:
            errors.append(f"{retired} must stay in campaigns.removed")
        if retired in ids:
            errors.append(f"{retired} must not be a routable campaign")
    for label, query in iter_queries(bundle["sources"]):
        hit = classify(query)
        if hit:
            errors.append(f"blocked category {hit} in source query {label}")
    root: Path = bundle["root"]
    for row in rows:
        for field in ("payout_timing", "approval_notes"):
            if field in row and not isinstance(row[field], str):
                errors.append(f"{row['id']} {field} must be free text")
        if "min_payout" not in row and "min_payout_usd" not in row:
            errors.append(f"{row['id']} needs min_payout")
        if "max_payout" not in row and "max_payout_usd" not in row:
            errors.append(f"{row['id']} needs max_payout")
        if "min_views" not in row:
            errors.append(f"{row['id']} needs min_views")
        logo = row.get("logo") or {}
        if logo.get("required"):
            rel = logo.get("file")
            if not rel:
                errors.append(f"{row['id']} requires a logo file")
            elif not (root / rel).is_file():
                errors.append(f"{row['id']} logo missing: {rel}")
        for platform in row["platforms"]:
            if platform not in PLATFORMS:
                errors.append(f"{row['id']} has unknown platform {platform}")
        if row["id"] == "crookbrown" and row["platforms"] != ["tiktok"]:
            errors.append("crookbrown must stay TikTok-only")
        if row["id"] == "minogue" and "x" in row["platforms"]:
            errors.append("minogue must not route to X")
    by_id = {row["id"]: row for row in rows}
    for required in ("sui-basecamp", "dhan-aziz", "rolling-stone", "oz-ali"):
        if required not in by_id:
            errors.append(f"missing campaign {required}")
    sui = by_id.get("sui-basecamp")
    if sui:
        if set(sui.get("platforms") or []) != {"youtube", "instagram", "tiktok"}:
            errors.append("sui-basecamp must be TikTok, Instagram, and YouTube only")
        phrases = (sui.get("caption") or {}).get("required_phrases") or []
        if "x.com/SuiNetwork" not in phrases:
            errors.append("sui-basecamp caption must include x.com/SuiNetwork")
        if (sui.get("logo") or {}).get("required"):
            errors.append("sui-basecamp has no provided logo; do not require one")
        source = sui.get("source") or {}
        if source.get("kind") != "google_drive_folder" or "drive.google.com" not in str(source.get("url") or ""):
            errors.append("sui-basecamp footage must come from the Google Drive folder")
    dhan = by_id.get("dhan-aziz")
    if dhan:
        if set(dhan.get("platforms") or []) != {"youtube", "instagram"}:
            errors.append("dhan-aziz must be Instagram and YouTube only")
        if dhan.get("edits") != "none" or dhan.get("approval") != "required":
            errors.append("dhan-aziz is an exact repost and stays awaiting approval until listed")
        if (dhan.get("logo") or {}).get("required"):
            errors.append("dhan-aziz must not carry a logo")
        source = dhan.get("source") or {}
        if source.get("kind") != "dropbox_folder" or "dropbox.com" not in str(source.get("url") or ""):
            errors.append("dhan-aziz footage must come from the Dropbox folder")
        phrases = (dhan.get("caption") or {}).get("required_phrases") or []
        if "@DHANAZIZ" not in phrases:
            errors.append("dhan-aziz caption must tag @DHANAZIZ")
    pipeline = bundle["pipeline"]
    maximum = int(pipeline["max_posts_per_platform"])
    if maximum > 10:
        errors.append("max_posts_per_platform cannot exceed 10")
    if pipeline.get("min_views_threshold") is None:
        errors.append("min_views_threshold must be set as a warning line")
    from clipper.engine import min_slot_gap_minutes

    for platform in PLATFORMS:
        spec = pipeline["schedule"][platform]
        if spec.get("timezone") != "Asia/Kolkata":
            errors.append(f"{platform} slot times must use Asia/Kolkata")
        slots = spec["slots"]
        if len(slots) != maximum:
            errors.append(f"{platform} needs exactly {maximum} IST slots")
        if min_slot_gap_minutes(slots) < 90:
            errors.append(f"{platform} slots must stay at least 90 minutes apart")
        limits = (pipeline.get("platform_limits") or {}).get(platform) or {}
        if "max_hashtags" not in limits or "max_duration_sec" not in limits:
            errors.append(f"{platform} needs platform_limits")
    return errors


def read_inbox_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        lines.append(text)
    return lines
