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

YouTube and Instagram posting schedules are off until a Growth demos run approves sample clips. A manual run still asks Buffer whether `6ac93d936a5c39ccb66624d7` exists when Instagram is enabled.

Instagram’s 50–60 figure is the **content mix**, not the post count. Of the day’s reels, about 50–60% are Indian (Bigg Boss, Kapil Sharma, cricket, Bollywood, TV serials). The rest are global viral clips. At the default of 8 reels, that is 4 Indian and 4 global (50%). At 7, it is 4 Indian and 3 global (about 57%).

X splits the day about 55% news (world, celebrity, AI, Elon Musk, Trump) and the rest sports and viral video. YouTube and Instagram accept only native vertical video. Landscape footage is rejected. There is no blurred padding and no crop. A light edit (speed 1.02–1.05, a subtle grade, an optional mirror when the picture has no text, and a 0.3–0.5s trim) runs after Gemini approves the clip. Nothing is drawn on the video. The original song stays.

YouTube (Denzen) is Indian entertainment: Bigg Boss, Kapil Sharma, cricket fan edits, Bollywood, paparazzi, and a high-weight love/romantic lane (couple reels, romantic scenes, love-song edits). Clips stay between 10 and 40 seconds. The caption names the celebrity and includes `#shorts`.

Instagram stays a 50–60% Indian mix, with the same love lane weighted high, plus paparazzi pages such as Viral Bhayani, Instant Bollywood, Voompla, Bollywood Bubble, and Filmy Gyan, and Colors TV / Sony TV. The other share is global viral. At 8 reels that is 4 Indian and 4 global.

Before a YouTube or Instagram post is scheduled, the mp4 is uploaded through the Gemini Files API and Gemini watches the whole video, audio included. Every score has to be at least 8. A broadcaster or event logo, a crop, or a weak Indian-audience fit fails the clip. Local OCR also reads all four corners on six frames. Gemini then writes the Hinglish caption from what it saw. A missing `GEMINI_API_KEY` rejects the clip. Scores go to `state/log.jsonl`.

Every third day, `.github/workflows/growth-improve.yml` reads sent-post view counts from Buffer and asks Gemini to raise or lower `topic_weights` and `account_weights` in the YouTube and Instagram configs. Weights stay between 0.3 and 3. A run inside 3 days of the last tune does nothing. The workflow commits the config only when a weight changes.

## What a run does

1. Search the configured Indian-first accounts and hashtags with yt-dlp. YouTube and Instagram keep clips from the last `max_age_hours` (168) and rank them by views and likes, times the topic and account weights. Love is weighted highest. A source that fails, or a YouTube page that asks for sign-in, is skipped. TikTok account pages use Chrome impersonation.
2. Reject a candidate when it is under 720p (shorter side), a duplicate, an ad or promo, a safety hit, or a copyright risk (full movie / full episode / webrip-style titles, anything 90 minutes or longer, or a source longer than `max_source_seconds`, default 80 minutes).
3. Download a format whose note does not say watermark and whose height is at least 720.
4. Scan corner and edge frames with ffmpeg. Tesseract OCR rejects platform names and `@handles`. A pure corner-ink check, plus OpenCV Canny edges when `cv2` is installed, rejects a corner logo. A frame whose content is sliced on three or more edges is rejected. Letterbox bars are not treated as cutoff.
5. X still trims a short clip, may zoom 4% when the frame is not letterboxed, burns a text hook, and fits 1080×1920. YouTube and Instagram do not trim, zoom, or burn text. They keep the original audio. Vertical footage is copied. Landscape footage is padded to 9:16. A repost outside that bot’s clip window is rejected.
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
| `slots` | IST clock (`timezone`), with `lead` and `backup_lead` minutes. Gaps must be at least `min_gap_minutes`. |
| `sources` | yt-dlp accounts, hashtags, or `ytsearch` queries. `lane` is `news`/`video` on X, `indian` on YouTube, `indian`/`global` on Instagram. |
| `strategy` | YouTube and Instagram content rules. The caption prompt and the vision gate both send this text to Gemini. |
| `topic_weights`, `account_weights` | Ranking multipliers, 0.3–3.0. The improve workflow rewrites them from view counts. |
| `min_gemini_score` | YouTube and Instagram schedule only when every vision score is at least this (8). |
| `repost` | YouTube and Instagram keep the original short. X leaves this off. |
| `indian_share`, `indian_share_min`, `indian_share_max` | Instagram mix. Keep the share inside 0.50–0.60. |
| `news_share` | X news vs video split. |
| `clip_seconds`, `clip_min_seconds`, `clip_max_seconds` | Output length. YouTube reposts must stay inside 6–90 seconds. |
| `hook_enable` | ffmpeg `drawtext` window for X. YouTube and Instagram do not burn text. |
| `min_resolution` | Never set this below 720. |
| `max_examined` | How many downloads one run will attempt. YouTube and Instagram use 6. |
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

X captions are written by BIBI. YouTube and Instagram captions use Gemini (`gemini-2.0-flash`, override with `GEMINI_MODEL`) when `GEMINI_API_KEY` is set. If Gemini errors or the caption fails that platform's checks, the template caption is used. The vision gate does not have that fallback: an unscored clip is not scheduled.

## Workflows

| File | When it runs |
| --- | --- |
| `.github/workflows/growth-x.yml` | Prepare crons at 07:00 and 17:30 IST, plus one fallback cron before each publish slot. |
| `.github/workflows/growth-youtube.yml` | One cron 28 minutes before each IST slot, and a backup 10 minutes later. |
| `.github/workflows/growth-instagram.yml` | One cron before each IST slot, plus a backup about 10 minutes later. |
| `.github/workflows/growth-improve.yml` | About every 3 days. Tunes YouTube and Instagram weights and commits the config. |

Each scheduled post run installs ffmpeg, tesseract, a DejaVu font, yt-dlp with `curl_cffi`, and `opencv-python-headless`, then commits `state/seen.json` and `state/log.jsonl` when they change. Videos are not committed.

Publish Pages uses `cancel-in-progress` on one concurrency group. A growth run dispatches it after uploading media. If a campaign publish is in progress, GitHub may cancel that run and start the new one. The new run still publishes every `live-*.mp4` on `clip-media`, including campaign clips.

## Secrets, channel ids, limits

Live scheduling needs `BUFFER_API_TOKEN` (the same secret `clip-live` uses). It is not in the repo. If it is missing, the run logs that and exits 0.

| Name | Kind | Required | Notes |
| --- | --- | --- | --- |
| `BUFFER_API_TOKEN` | secret | yes, for live posts | Bearer token for `https://api.buffer.com` |
| `GEMINI_API_KEY` | secret | yes, for YouTube and Instagram | Vision gate and captions. Also used by the 3-day weight tune. X still posts from BIBI, with a Gemini draft only as the deadline fallback. |
| `YT_COOKIES` | secret | no | Netscape cookies. Public YouTube search still runs without them. |
| `X_COOKIES` | secret | no | Used only if a source URL is on X. |
| `INSTAGRAM_COOKIES` | secret | no | Used only if a source URL is on Instagram. |
| `TIKTOK_COOKIES` | secret | no | TikTok tag URLs are skipped when the public extract fails. |
| `BUFFER_CHANNEL_X` | variable | no | Overrides `6ac41eb06a5c39ccb625fdf1` (@chadriyo). |
| `BUFFER_CHANNEL_YOUTUBE` | variable | no | Overrides `6ac4da806a5c39ccb62eab64` (Denzen). |
| `BUFFER_CHANNEL_INSTAGRAM` | variable | no | Overrides `6ac93d936a5c39ccb66624d7` (@benreelr). Each run still checks that the channel exists. |
| `GITHUB_TOKEN` | provided | yes, in Actions | Uploads the mp4 and dispatches Publish Pages. |

`github.token` cannot be replaced by a personal token in these workflows. Local `python3 -m growth.x run` also needs `GH_TOKEN` and `GITHUB_REPOSITORY` (`owner/chad-media`) or the upload step is skipped.

Limits this bot will not cross:

- X: 9 posts/day. YouTube: 8. Instagram: 8. Raising `posts_per_day` above `safe_daily_cap` fails `check`.
- Slot gap: at least `min_gap_minutes`.
- Resolution: 720p minimum.
- Output: YouTube reposts 6–90s. X up to its configured clip max. Instagram reposts inside its configured clip window.
- Downloads per run: `max_examined` (6 on YouTube and Instagram).
- Actions: one job per slot, 35 minute timeout, plus the improve job about every 3 days.

Limits owned by Buffer and the platforms, which this repo cannot read:

- Buffer’s free plan keeps about 10 scheduled posts per channel. The clip pipeline already schedules a full day on X and YouTube. Growth adds up to 9 and 8 more on those same channels. A paid Buffer plan is what makes the combined queue fit. On a queue or rate-limit error, that run stops.
- Instagram’s content-publishing cap is 50 posts per rolling 24 hours. 8 is under that cap. The bot does not try to post 50.
- Buffer must be able to fetch a public `video/mp4` with no redirect. GitHub release URLs are not used. The Pages URL is.

There is no separate Buffer organization secret. `createPost` only needs the channel id and `BUFFER_API_TOKEN`.
