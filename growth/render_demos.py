"""Render demo clips with each bot's scan, 9:16 fit, and caption. Does not call Buffer.

YouTube's guest download is blocked in this environment, so the files are
public Wikimedia videos fetched directly and then passed through produce().
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from growth.instagram.bot import load_config as load_instagram
from growth.instagram.caption import caption_for as ig_caption
from growth.utils.media import produce
from growth.utils.ytdlp import Candidate
from growth.x.bot import load_config as load_x
from growth.x.caption import caption_for as x_caption
from growth.youtube.bot import load_config as load_youtube
from growth.youtube.caption import caption_for as yt_caption

OUT = Path("/opt/cursor/artifacts/growth-demos")
UA = "Mozilla/5.0 (compatible; chad-media-growth-demo/1.0)"

CLIPS = {
    "x": [
        ("trump-who", "President Trump Holds a News Conference on the WHO", "news", "trump", "https://upload.wikimedia.org/wikipedia/commons/c/c0/President_Trump_Holds_a_News_Conference_29_may_2020_focus_on_WHO.webm"),
        ("besiktas-goal", "Kaldırım scored the goal for a 3-3 draw in the Beşiktaş Fenerbahçe derby", "video", "sports", "https://upload.wikimedia.org/wikipedia/commons/2/20/Kald%C4%B1r%C4%B1m_scored_the_goal_for_a_3-3_draw_%28Be%C5%9Fikta%C5%9F-Fenerbah%C3%A7e_Derby_-_February_2019%29.webm"),
        ("wildlife", "Wildlife habitat filmed in Africa", "video", "news", "https://upload.wikimedia.org/wikipedia/commons/c/c0/Wildlife_Habitat_in_Africa.webm"),
    ],
    "youtube": [
        ("bhagoria", "Bhagoria dance from Madhya Pradesh India", "indian", "bollywood", "https://upload.wikimedia.org/wikipedia/commons/c/c2/Bhagoria_dance_from_MP_India.webm"),
        ("wiki-india", "Audience at Wikiconference India 2023", "indian", "serial", "https://upload.wikimedia.org/wikipedia/commons/9/9d/Audience_Wikiconference_India_2023_Day_Two.webm"),
        ("wiki-song", "Participants singing at Wikiconference India 2023", "indian", "kapil", "https://upload.wikimedia.org/wikipedia/commons/b/b0/Participants_singing_Wikiconference_India_2023_Day_Two.webm"),
    ],
    "instagram": [
        ("bhagoria-reel", "Bhagoria dance from Madhya Pradesh", "indian", "bollywood", "https://upload.wikimedia.org/wikipedia/commons/c/c2/Bhagoria_dance_from_MP_India.webm"),
        ("baganda", "Baganda traditional dance", "global", "global", "https://upload.wikimedia.org/wikipedia/commons/4/46/Baganda_traditional_dance_01.webm"),
        ("africa", "Wildlife habitat in Africa", "global", "global", "https://upload.wikimedia.org/wikipedia/commons/c/c0/Wildlife_Habitat_in_Africa.webm"),
    ],
}


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["curl", "-fsSL", "-A", UA, "-o", str(dest), url],
        check=True,
        timeout=180,
    )


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    configs = {"x": load_x(), "youtube": load_youtube(), "instagram": load_instagram()}
    captions = {"x": x_caption, "youtube": yt_caption, "instagram": ig_caption}
    lines = [
        "# Growth demo captions",
        "",
        "Rendered with each bot's quality scan and 9:16 blurred-pad pipeline. Not sent to Buffer.",
        "YouTube guest downloads were blocked here, so the sources are public Wikimedia files.",
        "Captions are the text each bot would attach. X shows the draft BIBI can replace.",
        "",
    ]
    saved = 0
    for platform, rows in CLIPS.items():
        kept = 0
        for source_id, title, lane, topic, url in rows:
            if kept >= 3:
                break
            work = ROOT / "work" / "growth-demos" / platform / source_id
            raw = work / "source.webm"
            print(f"download {platform} {source_id}", flush=True)
            try:
                download(url, raw)
            except Exception as exc:
                print(f"download failed {source_id}: {exc}", flush=True)
                continue
            candidate = Candidate(source_id, title, url, lane, topic, description=title)
            caption = captions[platform](candidate)
            try:
                prepared = produce(candidate, caption.splitlines()[0], configs[platform], work, os.environ, source_file=raw)
            except Exception as exc:
                print(f"render failed {source_id}: {exc}", flush=True)
                continue
            if not prepared.local_path:
                print(
                    f"rejected {source_id} {prepared.width}x{prepared.height} "
                    f"ocr={prepared.ocr_text[:80]!r} hits={prepared.corner_hits} cut={prepared.edge_cutoff} "
                    f"scan={prepared.scanners_unavailable}",
                    flush=True,
                )
                continue
            filename = f"{platform}-{kept + 1}-{source_id}.mp4"
            shutil.copyfile(prepared.local_path, OUT / filename)
            kept += 1
            saved += 1
            lines.extend(
                [
                    f"## {platform} — {filename}",
                    "",
                    f"Source: {url}",
                    "",
                    caption,
                    "",
                ]
            )
            print(f"saved {filename}", flush=True)
    text = "\n".join(lines)
    (OUT / "captions.md").write_text(text, encoding="utf-8")
    print(text)
    print(f"saved {saved}")
    return 0 if saved >= 6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
