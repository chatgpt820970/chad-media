"""Render a few real clips per platform. Does not call Buffer."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["PATH"] = str(Path.home() / ".local" / "bin") + os.pathsep + os.environ.get("PATH", "")

from growth.instagram.bot import HOOKS as IG_HOOKS
from growth.instagram.caption import caption_for as ig_caption
from growth.utils.media import produce
from growth.utils.ytdlp import collect
from growth.x.bot import HOOKS as X_HOOKS
from growth.x.caption import caption_for as x_caption
from growth.youtube.bot import HOOKS as YT_HOOKS
from growth.youtube.caption import caption_for as yt_caption

OUT = Path("/opt/cursor/artifacts/growth-demos")
WANTED = 3


def render_platform(name: str, hooks, caption_for) -> list[dict]:
    config = hooks.load_config()
    work = ROOT / "work" / f"demo-{name}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    saved: list[dict] = []
    candidates = collect(list(config["sources"])[:4], timeout=70)
    print(f"{name}: {len(candidates)} candidates", flush=True)
    for candidate in candidates:
        if len(saved) >= WANTED:
            break
        caption = caption_for(candidate)
        folder = work / candidate.source_id
        try:
            prepared = produce(candidate, caption.splitlines()[0], config, folder, os.environ)
        except Exception as exc:
            print(f"{name} skip {candidate.source_id}: {exc}", flush=True)
            continue
        if not prepared.local_path or prepared.scanners_unavailable or prepared.corner_hits or prepared.edge_cutoff:
            print(
                f"{name} reject {candidate.source_id} path={bool(prepared.local_path)} "
                f"scan={prepared.scanners_unavailable} marks={prepared.corner_hits} cut={prepared.edge_cutoff} "
                f"{prepared.width}x{prepared.height}",
                flush=True,
            )
            continue
        if min(prepared.width, prepared.height) < 720:
            print(f"{name} lowres {candidate.source_id} {prepared.width}x{prepared.height}", flush=True)
            continue
        filename = f"{name}-{len(saved) + 1}-{candidate.source_id}.mp4"
        dest = OUT / filename
        shutil.copyfile(prepared.local_path, dest)
        saved.append(
            {
                "platform": name,
                "file": filename,
                "title": candidate.title,
                "url": candidate.url,
                "caption": caption,
                "size": dest.stat().st_size,
            }
        )
        print(f"{name} saved {filename} {dest.stat().st_size}", flush=True)
    return saved


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []
    rows.extend(render_platform("x", X_HOOKS, x_caption))
    rows.extend(render_platform("youtube", YT_HOOKS, yt_caption))
    rows.extend(render_platform("instagram", IG_HOOKS, ig_caption))
    lines = ["# Growth demo captions", ""]
    for row in rows:
        lines.append(f"## {row['platform']} — {row['file']}")
        lines.append("")
        lines.append(f"Source: {row['url']}")
        lines.append("")
        lines.append(row["caption"])
        lines.append("")
    text = "\n".join(lines)
    (OUT / "captions.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"rendered {len(rows)}")
    return 0 if len(rows) >= 6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
