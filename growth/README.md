# Growth bots

Three separate schedulers. Each one has its own config, dedupe log, and GitHub Actions workflow. They share only `growth/utils` (retry, dedupe, quality checks, yt-dlp parsing, 9:16 padding, and the Buffer payload helper). They do not import each other.

The paused Whop campaigns, `config/`, and the retired workflows are unchanged. These bots post on the same Buffer channels as the clip pipeline, on their own clocks.

| Bot | Account | Slots / day | When a run fires |
| --- | --- | --- | --- |
| `growth/x` | @chadriyo | 9 | One cron about 25 minutes before each IST slot, and a backup 10 minutes later |
| `growth/youtube` | Denzen (`@denzen0`) | 8 | One cron 28 minutes before each slot, and a backup 10 minutes later |
| `growth/instagram` | @benreelr | 8 | One cron 24 minutes before each slot, and a backup 10 minutes later |

YouTube and Instagram each schedule one post per cron. Buffer `dueAt` is the exact slot time. A failed candidate is skipped and the next one is tried (up to 6). If the slot still fails, the backup cron retries only that slot. A second failure exits non-zero and opens or updates a GitHub issue labeled `ci-failed`. A full queue (9 or more scheduled posts) or a missing Buffer channel is a clean skip.

X does not post an automatic caption. At 07:00 and 17:30 IST it renders the next batch, uploads each file, and appends `growth/x/state/pending_captions.json` with the slot time, public video URL, source title, a short description, and a Gemini draft. BIBI sets `caption` on an entry. `python -m growth.x schedule` (or `growth/x/schedule_captioned.py`) sends those entries to Buffer at the slot time. If `caption` is still empty 30 minutes before the slot, that same step sends the Gemini draft.

X publish times (IST) are 07:30, 09:30, 13:00, 18:00, 19:30, 20:25, 22:00, 23:15, and 00:30. 20:25 is used instead of 20:30 so the post stays 22 minutes away from the 20:47 news post. The 23:15 runners start at 22:15 and 22:25, and the 00:30 runners start at 00:08 and 00:18, so those jobs stay 20 minutes away from the 22:47 and 23:47 news posts. YouTube slots are 07:30, 12:00, 14:00, 17:00, 19:00, 20:30, 21:30, 22:30. Instagram slots are 08:30, 11:00, 13:30, 16:30, 18:30, 20:00, 21:00, 22:00. The 21:00 Instagram runners start at 20:15 and 20:25.

Instagram is enabled. Every run still asks the Buffer API whether `6ac93d936a5c39ccb66624d7` exists and skips the slot if it does not.

Instagram’s 50–60 figure is the **content mix**, not the post count. Of the day’s reels, about 50–60% are Indian (Bigg Boss, Kapil Sharma, cricket, Bollywood, TV serials). The rest are global viral clips. At the default of 8 reels, that is 4 Indian and 4 global (50%). At 7, it is 4 Indian and 3 global (about 57%).

X splits the day about 55% news (world, celebrity, AI, Elon Musk, Trump) and the rest sports and viral video. YouTube Shorts are Indian entertainment only, 15–30 seconds, with the hook on screen in the first second.

## What a run does

1. Search public videos with yt-dlp (`ytsearch` plus a TikTok tag URL). A source that fails is skipped.
2. Reject a candidate when it is under 720p (shorter side), a duplicate, an ad or promo, a safety hit, or a copyright risk (full movie / full episode / webrip-style titles, anything 90 minutes or longer, or a source longer than `max_source_seconds`, default 80 minutes).
3. Download a format whose note does not say watermark and whose height is at least 720.
4. Scan corner and edge frames with ffmpeg. Tesseract OCR rejects platform names and `@handles`. A pure corner-ink check, plus OpenCV Canny edges when `cv2` is installed, rejects a corner logo. A frame whose content is sliced on three or more edges is rejected. Letterbox bars are not treated as cutoff.
5. Render a short clip: trim, a 4% zoom when the frame is not letterboxed, a text hook, and a 1080×1920 fit. The subject is scaled to fit. The blurred background is what gets cropped to fill 9:16.
6. Upload `live-growth-<platform>-*.mp4` to the existing `clip-media` release and dispatch **Publish Pages** once. That workflow already copies `live-*.mp4` to GitHub Pages. Buffer receives the Pages URL.
7. Schedule the post with Buffer’s GraphQL `createPost` on that bot’s channel only.
8. Append the result to `state/log.jsonl` and, after a real post or a permanent reject, to `state/seen.json`.

A download miss, a scanner miss, or a bad candidate is logged and the run moves on. The job exits 0. A missing `BUFFER_API_TOKEN` is logged as `buffer_token_missing` and nothing is scheduled. A Buffer rate-limit or queue error stops the rest of that run without failing the job.

## Enable, disable, tune

Config files:

- `growth/x/config.json`
- `growth/youtube/config.json`
- `growth/instagram/config.json`

| Knob | Effect |
| --- | --- |
| `enabled` | `false` skips live scheduling. Dry-run still plans. |
| `posts_per_day` | How many posts to schedule. Clamped to `posts_per_day_min`, `posts_per_day_max`, and `safe_daily_cap`. |
| `slot_times` | IST clock (`timezone`). Gaps must be at least `min_gap_minutes` (90). One run fills the slots still ahead in the next `horizon_hours` (20). |
| `sources` | yt-dlp queries. `lane` is `news`/`video` on X, `indian` on YouTube, `indian`/`global` on Instagram. |
| `indian_share`, `indian_share_min`, `indian_share_max` | Instagram mix. Keep the share inside 0.50–0.60. |
| `news_share` | X news vs video split. |
| `clip_seconds`, `clip_min_seconds`, `clip_max_seconds` | Output length. YouTube must stay inside 15–30. |
| `hook_enable` | ffmpeg `drawtext` window. YouTube is `lt(t,1)` (first second). |
| `min_resolution` | Never set this below 720. |
| `max_examined` | How many downloads one run will attempt. Default 16, so a run stays inside the 35-minute Actions limit. |
| `buffer_channel_id` | Channel used when the override variable is empty. |

Checks:

```bash
python3 -m growth.x check
python3 -m growth.youtube check
python3 -m growth.instagram check
python3 -m unittest discover -s tests -v
```

Dry-run (no download, no Buffer call):

```bash
python3 -m growth.x run --dry-run --now 2026-10-29T01:00:00Z
python3 -m growth.youtube run --dry-run --now 2026-10-29T01:00:00Z
python3 -m growth.instagram run --dry-run --now 2026-10-29T01:00:00Z
```

### Turn X or YouTube off

Set `"enabled": false` in that bot’s config and push. The workflow still runs; the bot logs `disabled` and exits 0. To stop the schedule entirely, delete the `schedule` block in `.github/workflows/growth-x.yml` or `growth-youtube.yml`.

### Turn Instagram off

Set `"enabled": false` and `"channel_confirmed": false` together, and remove the `schedule` list from `.github/workflows/growth-instagram.yml`.

Captions use Gemini (`gemini-2.0-flash`, override with `GEMINI_MODEL`) when `GEMINI_API_KEY` is set. Each platform has its own prompt. If Gemini errors or the caption fails that platform's checks, the template caption is used.

## Workflows

| File | When it runs |
| --- | --- |
| `.github/workflows/growth-x.yml` | Every day at 01:11 UTC, plus manual dispatch. Manual dispatch dry-runs unless `dry_run` is unchecked. |
| `.github/workflows/growth-youtube.yml` | Every day at 03:41 UTC, same dispatch rule. |
| `.github/workflows/growth-instagram.yml` | Manual dispatch only, and the job is `if: false`. No schedule. |

Each scheduled run installs ffmpeg, tesseract, a DejaVu font, yt-dlp, and `opencv-python-headless`, then commits `state/seen.json` and `state/log.jsonl` when they change. Videos are not committed.

Publish Pages uses `cancel-in-progress` on one concurrency group. A growth run dispatches it after uploading media. If a campaign publish is in progress, GitHub may cancel that run and start the new one. The new run still publishes every `live-*.mp4` on `clip-media`, including campaign clips.

## Secrets, channel ids, limits

Live scheduling needs `BUFFER_API_TOKEN` (the same secret `clip-live` uses). It is not in the repo. If it is missing, the run logs that and exits 0.

| Name | Kind | Required | Notes |
| --- | --- | --- | --- |
| `BUFFER_API_TOKEN` | secret | yes, for live posts | Bearer token for `https://api.buffer.com` |
| `YT_COOKIES` | secret | no | Netscape cookies. Public YouTube search still runs without them. |
| `X_COOKIES` | secret | no | Used only if a source URL is on X. |
| `INSTAGRAM_COOKIES` | secret | no | Used only if a source URL is on Instagram. |
| `TIKTOK_COOKIES` | secret | no | TikTok tag URLs are skipped when the public extract fails. |
| `BUFFER_CHANNEL_X` | variable | no | Overrides `6ac41eb06a5c39ccb625fdf1` (@chadriyo). |
| `BUFFER_CHANNEL_YOUTUBE` | variable | no | Overrides `6ac4da806a5c39ccb62eab64` (Denzen). |
| `BUFFER_CHANNEL_INSTAGRAM` | variable | no | Overrides `6ac93d936a5c39ccb66624d7` (@benreelr). The id is stored. The channel is not confirmed, so Instagram does not post. |
| `GITHUB_TOKEN` | provided | yes, in Actions | Uploads the mp4 and dispatches Publish Pages. |

`github.token` cannot be replaced by a personal token in these workflows. Local `python3 -m growth.x run` also needs `GH_TOKEN` and `GITHUB_REPOSITORY` (`owner/chad-media`) or the upload step is skipped.

Limits this bot will not cross:

- X: 9 posts/day. YouTube: 8. Instagram: 8. Raising `posts_per_day` above `safe_daily_cap` fails `check`.
- Slot gap: 90 minutes.
- Resolution: 720p minimum.
- Output: YouTube 15–30s. X up to 35s. Instagram up to 30s.
- Downloads per run: `max_examined` (16).
- Actions: one job a day per enabled bot, 35 minute timeout. Instagram does not run.

Limits owned by Buffer and the platforms, which this repo cannot read:

- Buffer’s free plan keeps about 10 scheduled posts per channel. The clip pipeline already schedules a full day on X and YouTube. Growth adds up to 9 and 8 more on those same channels. A paid Buffer plan is what makes the combined queue fit. On a queue or rate-limit error, that run stops.
- Instagram’s content-publishing cap is 50 posts per rolling 24 hours. 8 is under that cap. The bot does not try to post 50.
- Buffer must be able to fetch a public `video/mp4` with no redirect. GitHub release URLs are not used. The Pages URL is.

There is no separate Buffer organization secret. `createPost` only needs the channel id and `BUFFER_API_TOKEN`.
