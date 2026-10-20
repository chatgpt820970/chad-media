# Shorts

A separate, keyless pipeline that queues vertical clips for a YouTube channel. It does not use API keys, tokens, or repo secrets, and it does not change the other workflows in this repo.

The feed is published on GitHub Pages:

https://chatgpt820970.github.io/chad-media/feed/shorts_queue.json

Each item has `id`, `source_url`, `platform`, `duration` (seconds), `mp4_url` (a direct `video/mp4` file), `frame_urls` (three review frames), `title`, `caption`, and `status` (`ready` or `posted`). `lane` is `funny`, `chinese`, or `feelgood`. `youtube_category_id` is `23` (Comedy) or `10` (Music).

## What a run does

`.github/workflows/shorts.yml` runs three times a day (01:20, 09:20, and 17:20 UTC). It can also be started by hand.

1. Look for new public clips. YouTube is never a source.
2. Keep clips that are at least 30 seconds and under 3 minutes, with enough plays to count as high engagement.
3. Download a watermark-free file when the platform offers one. Clips that still show a TikTok, Instagram, or X logo or `@handle` are skipped. Nothing is blurred or cropped to hide a watermark.
4. Encode 1080x1920 H.264 + AAC, loudness-normalized, faststart MP4, under Buffer's YouTube upload size.
5. Save three frames (about 15%, 50%, and 85%).
6. Publish the mp4s, frames, and `feed/shorts_queue.json` on GitHub Pages. The existing news media on that site is copied back into the same deploy so this workflow does not wipe it. After the other Pages workflow runs, this one republishes the shorts files.
7. Record every accepted or rejected id in `shorts/state/seen.json` so a clip is never queued twice. `shorts/state/posted.json` (`{"ids": []}`) marks ids already posted; those items stay `status: "posted"`.
8. Drop media older than 4 days, and never keep more than 18 clips. Binaries live on the `shorts-media` GitHub Release and on Pages, not in git history.

## Sources

| Platform | What happens |
| --- | --- |
| TikTok | Works without a login. Posts come from public comedy, Chinese, and Indian-music accounts via gallery-dl. The file saved is the public no-watermark `play` URL, not the watermarked download. |
| X | Skipped. Profile timelines and media tabs ask for a logged-in session, and the public syndication endpoint is rate-limited. A run still checks, and `sources.x` in the feed says why it was skipped. |
| Instagram | Skipped. Reels redirect to the login page without a session. `sources.instagram` records that. |
| YouTube | Never used. |

Chinese-leaning clips come from public TikTok accounts such as `@mrnigelng`, `@madewithlau`, and `@thefoodranger`. Feel-good Indian music comes from accounts such as `@yoyohoneysingh`, `@diljitdosanjh`, `@armaanmalik`, and `@jonitamusic`, plus warm clips from `@humankind`. Funny high-engagement clips come from `@khaby.lame`, `@dudeperfect`, `@failarmy`, `@kingbach`, `@benoftheweek`, and `@spencerx`.

## Review before anything is posted

This workflow only fills the queue. It does not post to YouTube. `GROK_TASK.md` is the instruction to paste into the Grok assistant that checks the three frames, rewrites the title and caption, and posts 2–3 clips a day through Buffer.

## Run it by hand

The scheduled workflow is the normal path. Locally, with ffmpeg, tesseract, yt-dlp, and gallery-dl installed:

```bash
pip install -r shorts/requirements.txt
python shorts/pipeline.py collect --target 4 --require 3
```
