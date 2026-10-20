# Shorts

A separate, keyless pipeline that queues vertical clips for a YouTube channel. It does not use API keys, tokens, or repo secrets, and it does not change the other workflows in this repo.

The feed is published on GitHub Pages:

https://chatgpt820970.github.io/chad-media/feed/shorts_queue.json

Each item has `id`, `source_url`, `platform`, `duration` (seconds), `mp4_url` (a direct `video/mp4` file), `frame_urls` (three review frames), `title`, `caption`, and `status` (`ready` or `posted`). `lane` is `funny`, `chinese`, or `feelgood`. `youtube_category_id` is `23` (Comedy) or `10` (Music).

`ready_today` is how many items in the feed have `status: "ready"` and a `queued_at` on today's UTC date. The two prep runs fall on that same date in IST. `daily_target` is 7: the number of new ready clips a full day is meant to clear.

## What a run does

cron-job.org dispatches `.github/workflows/shorts.yml` at **06:30 and 14:30 IST**. Each of those runs tries to add **5** new clips and must add **at least 4** with `status: "ready"`. Together that is 8 to 10 new ready clips, so the day clears 7 even if a few candidates are rejected.

The GitHub schedule at 01:20, 09:20, and 17:20 UTC (06:50, 14:50, and 22:50 IST) is only a backup. A backup run adds nothing when that slot's clips are already in the feed. The late run can still fill a day whose dispatches produced nothing.

The workflow also runs when the collector code itself changes on `main`. Updating the seen log or the queue does not start another run.

1. Look for new public clips. YouTube is never a source.
2. Keep clips that are at least 30 seconds and under 3 minutes.
3. Download a watermark-free file when the platform offers one. Clips that still show a TikTok, Instagram, or X logo or `@handle` are skipped. Nothing is blurred or cropped to hide a watermark.
4. Encode 1080x1920 H.264 + AAC, loudness-normalized, faststart MP4, under Buffer's YouTube upload size. The picture fills the 9:16 frame. A clip that is already vertical is center-cropped. Any wider clip is placed on a blurred, scaled copy of the same video. Black padding is not used.
5. Save three frames (about 15%, 50%, and 85%).
6. Publish the mp4s, frames, and `feed/shorts_queue.json` on GitHub Pages. The existing news media on that site is copied back into the same deploy so this workflow does not wipe it. After the other Pages workflow runs, this one republishes the shorts files.
7. Record every accepted or rejected id in `shorts/state/seen.json` so a clip is never queued twice. `shorts/state/posted.json` (`{"ids": []}`) marks ids already posted; those items stay `status: "posted"` and do not count toward `ready_today`.
8. Drop media older than 4 days, and never keep more than 24 clips. Binaries live on the `shorts-media` GitHub Release and on Pages, not in git history.

## Sources

| Platform | What happens |
| --- | --- |
| TikTok | Works without a login. Posts come from public comedy, Chinese, and Indian-music accounts via gallery-dl. The file saved is the public no-watermark `play` URL, not the watermarked download. |
| X | Works without a login when public search returns a post id. `api.fxtwitter.com/status/<id>` supplies the direct mp4. Profile timelines, Nitter, and vxtwitter are not used. |
| Instagram | Works without a login when public search returns a reel code. yt-dlp downloads that reel as a guest. The embed page, gallery-dl, and ddinstagram are not used. |
| YouTube | Never used. |

If search is blocked, `sources.x` or `sources.instagram` in the feed says so, and the run still fills the slot from TikTok.

Chinese-leaning clips come from public TikTok accounts such as `@mrnigelng`, `@madewithlau`, `@thefoodranger`, and `@thestevenhe`, plus Chinese-funny posts found on X and Instagram. Feel-good Indian music comes from accounts such as `@yoyohoneysingh`, `@diljitdosanjh`, `@armaanmalik`, and `@jonitamusic`, plus warm clips from `@humankind`. Funny high-engagement clips come from `@khaby.lame`, `@dudeperfect`, `@failarmy`, `@kingbach`, `@benoftheweek`, `@spencerx`, and `@zachking`.

## Review before anything is posted

This workflow only fills the queue. It does not post to YouTube. `GROK_TASK.md` is the instruction to paste into the Grok assistant. Grok runs after each prep and schedules 7 clips a day at 8:00, 11:00, 13:00, 16:00, 19:00, 21:00, and 23:00 IST with Buffer `customScheduled` / `dueAt`.

## Run it by hand

The scheduled workflow is the normal path. Locally, with ffmpeg, tesseract, yt-dlp, and gallery-dl installed:

```bash
pip install -r shorts/requirements.txt
python shorts/pipeline.py collect --target 5 --require 4
```
