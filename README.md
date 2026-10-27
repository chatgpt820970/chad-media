# chad-media

Clipping pipeline for an India-based clipper posting through [Buffer](https://publish.buffer.com) and submitting those posts by hand to [Content Rewards](https://contentrewards.com). GitHub Actions plans the day, renders vertical clips, and can schedule Buffer posts. It does not submit to Content Rewards.

Joined campaigns are **Sui BaseCamp** and **Dhan Aziz**. Everything else in `config/campaigns.json` is inactive. Dhan Aziz is prepared as a draft and is not posted until that clip's id is listed in `state/approvals.json`.

X has no campaign. X posts the owner's own plan (trending, celebrity, 4K anime), clean, with no logo. Instagram posting is off until that account can link to Content Rewards (`channels.platforms.instagram.enabled` is false). Public Instagram Reels are still downloaded, with cookies, as a source for X, YouTube, and TikTok. The Reels code and the IST clock stay in the repo. TikTok and YouTube slots that a campaign does not fill use that platform's content plan, also with no logo.

## What a run does

1. Read `config/campaigns.json`, `config/sources.json`, `config/channels.json`, and `config/pipeline.json`.
2. Drop clips that match the safety guard (sexual or nude, gore, hate, dangerous acts, misleading or spam captions), including the filename and title. Romantic, movie, anime, and sports clips stay allowed. A safety skip does not pause the platform.
3. Skip a source that still has a burned-in creator or platform watermark after one cover pass. A mark in the center is always skipped. The campaign logo, if that campaign requires one, is overlaid only after the frame is clean. Branding is a transparent PNG, not a text box.
4. Assign campaign footage only when that campaign is active, allows the platform and content type, fits the duration, has budget left for at least one minimum payout, and is not a duplicate. Sui BaseCamp is cut from its Google Drive folder. Dhan Aziz is an exact Dropbox repost and stays in `awaiting_approval` until you approve it. X, and any short-form slot a campaign does not take, uses the platform content plan with no logo and no campaign caption.
5. Split the day's slots by expected earnings. Same source never posts twice on one platform. Campaigns marked `one_shortform` post once inside YouTube, Instagram, and TikTok.
6. Schedule ten slots a day on each platform, at least 90 minutes apart, in IST. The quiet gap sits in that audience's dead hours. X, YouTube, Instagram, and TikTok do not share one clock. Edit the times in `config/pipeline.json`. Instagram keeps its clock, but live posting skips it while `enabled` is false.

Views to the first payout (`min_payout / rate_per_1k * 1000`) is printed as a **warning** when it is above `min_views_threshold` (default 1500). T Bird is about 10,000 views to the first $10. That warning does not reject the campaign and does not change `active`. Turning a campaign on is a config edit you make yourself.

`payout_timing` and `approval_notes` on each campaign are optional free text. Leave them empty until you have talked to the campaign manager. When they are filled in, the run summary prints them.

## Campaigns

| id | status | rate | min payout | views to first payout | platforms |
| --- | --- | --- | --- | --- | --- |
| sui-basecamp | active | $4 / 1k | $4 | 1000 | TikTok, Instagram, YouTube. Drive folder only. Caption includes `x.com/SuiNetwork`. No logo. |
| dhan-aziz | active, drafts only | $5 / 1k | $5 | 1000 (max $10 a video) | Instagram and YouTube. Exact Dropbox file, tag `@DHANAZIZ`, no logo, no edits. Posts only after approval. |
| t-bird | inactive | $1 / 1k | $10 | 10000 (warning) | X, YouTube, Instagram, TikTok |
| minogue | inactive | $1.50 / 1k | $1.50 | 1000 | YouTube, Instagram, TikTok |
| jackelyne | inactive | $1.50 / 1k | $2 | about 1334 | YouTube, Instagram, TikTok, dedicated pages only |
| a24-slayground | inactive | $1 / 1k | $1 | 1000 | YouTube, Instagram, TikTok |
| crookbrown | inactive | $1 / 1k | $1 | 1000 | TikTok only, notification so you can attach the sound |
| ricky-hilfiger | inactive | $2 / 1k | $3 | 1500 (on the line, no warning) | YouTube, Instagram, TikTok |
| rolling-stone | inactive | $2 / 1k | $2 | 1000 | TikTok and Instagram. Needs 30% Tier 1 viewers and music pages. |
| oz-ali | inactive | $2 / 1k | $2 | 1000 | Needs 60% Tier 1 and 45% UK. |

`dishpilot`, `flipgg`, `mrpa`, `arrafund`, and the A24 Evangelist Slayground card are not routable. Rules, source URLs, and gaps are in `config/campaigns.json`.

Jackelyne does not post to @benreelr, @bi67325, or Denzen. `logos/jackelyne.png` is a placeholder wordmark. Live posting stays blocked until the official PNG replaces it (`logo.placeholder` false) and the fan-page Buffer channel ids are set.

### Sui BaseCamp

[Campaign card](https://contentrewards.com/c/campaigns/1c64ec71-ba0e-4798-b017-869635798747). TikTok, Instagram, and YouTube Shorts. $4 per 1,000 views, minimum $4, maximum $300. About 1,000 views to the first payout, under the warning line.

Footage comes only from the public Drive folder:

https://drive.google.com/drive/folders/1tepyyEVFtGLi8Ukv8AddBDj2FcIb7d-i

```bash
gdown --folder --remaining-ok -O work/inbox/sui-basecamp \
  https://drive.google.com/drive/folders/1tepyyEVFtGLi8Ukv8AddBDj2FcIb7d-i
```

Live stream reference: https://x.com/SuiNetwork/status/2107667372077416613
Example clip: https://x.com/jackkk/status/2107707718903685516

Cut these moments: Hudi trading competition (Oct 6, 11:30pm–12:45am EST), Adeniyi keynote (Oct 7, 1:05–1:30am EST), Evan Cheng (Oct 8, 1:25–1:55am EST), Adeniyi closing (Oct 8, 4:10–4:40am EST). The public card also names the Rooms live demo at 0:59:40–1:10:40. Edits should be engaging. The caption must contain `x.com/SuiNetwork`. The card does not give a logo, so none is applied.

### Dhan Aziz

[Campaign card](https://contentrewards.com/c/campaigns/0f0e96b4-e0dc-4f18-9ac6-e6b41f070b3f). Instagram and YouTube Shorts only. $5 per 1,000 views, minimum $5, maximum $10 a video. Repost the Dropbox files exactly: no crop, no logo, no cover. Tag `@DHANAZIZ` and leave the post public.

https://www.dropbox.com/scl/fo/q7yh4nn5cmjco6tcn9qlp/AH5g6pAm_ok984WYOXul0O0?rlkey=xb4vtwrztelm5hugx8i1kd6tq&dl=1

The pipeline records a `curl` of that link with `dl=1`. Each file is listed as `awaiting_approval` and is not sent to Buffer until its `source_id` is in `state/approvals.json`.

### Where the other slots go

X is the owner's plan: trending, celebrity, and 4K anime, with no logo and no campaign caption. Leftover TikTok, Instagram, and YouTube slots use the same kind of platform plan (Indian-leaning YouTube and Instagram, international anime and Hollywood on TikTok). Those posts are not submitted to Content Rewards.

## Schedule (IST)

Ten slots a day on each platform. Adjacent slots in this file are 135 minutes apart, and `clipper check` rejects a clock whose minimum gap, including the midnight wrap, is under 90 minutes. Instagram uses the YouTube clock so it can be turned back on without retiming.

X, weighted to US evening, Europe evening, and India evening. Quiet about 10:15–14:00 IST:

`14:00, 16:15, 18:30, 20:45, 23:00, 01:15, 03:30, 05:45, 08:00, 10:15`

YouTube Shorts, India afternoon and night (about 12:00–14:00 and 19:00–23:00 IST). Quiet about 03:15–07:00 IST. Instagram keeps this same list for when Reels are enabled again:

`07:00, 09:15, 11:30, 13:45, 16:00, 18:15, 20:30, 22:45, 01:00, 03:15`

TikTok, US and Europe evenings. Quiet about 11:45–15:30 IST:

`15:30, 17:45, 20:00, 22:15, 00:30, 02:45, 05:00, 07:15, 09:30, 11:45`

Times are 27 October 2026 conversions (US EDT, Europe CET). Change the lists in config if you want different hours.

## Commands

```bash
python -m clipper check
python -m clipper run --dry-run --now 2026-10-27T00:00:00Z --output-dir work/dry-run
python -m clipper mark-submitted --key "campaign:platform:source-id"
CLIPPER_MONITOR=true python -m clipper monitor
```

`python -m clipper run --dry-run` writes the plan only. `python -m clipper fetch` downloads for real: `gdown` lists the Sui Drive folder and saves the smallest clip, `curl` saves the Dhan Aziz Dropbox share with `dl=1`, and content-plan slots try a Netscape cookie file for that site before any public source. `YT_COOKIES`, `TIKTOK_COOKIES`, and `INSTAGRAM_COOKIES` are written to a temp file at runtime, masked in the log, and passed to yt-dlp as `--cookies`. They are never committed. A missing secret, or a cookie download that saves nothing, falls back to cookie-free sources. X uses public search, then a syndication profile, then yt-dlp or gallery-dl, then the public mp4. TikTok cookies try a profile URL (`tiktok.com/@user`) before a tag page, because tag extractors often return no videos. Public TikTok does the same and skips a format whose note says watermark. Reddit uses public JSON with a User-Agent and then yt-dlp on v.redd.it. YouTube cookies use the web client. A most-replayed heatmap, when yt-dlp returns one, sets the download section. Otherwise the cut starts at 0:00. The cookie-free path is an Invidious or Piped frontend. Workflows install the yt-dlp nightly (`pip install -U --pre yt-dlp`). A miss is `unavailable` or, for YouTube, `needs_cookies`. Neither one fails the Sui download, and neither one pauses a platform. Instagram posting stays off. When `INSTAGRAM_COOKIES` is present, public Reels are listed from profile URLs first, then hashtags, and routed to X, YouTube Shorts, or TikTok. OCR drops a reel with a visible Instagram watermark or creator handle. The same reel is not posted to a second platform. Sexual titles are skipped. `config/blocklist.json` plus topics stored on `state/live.json` skip a celebrity or phrase for 7 days after it was posted. The caption is a unique content-plan caption. X captions also get a short hook for that day and source, and a light text line is burned into the X file. YouTube and TikTok stay without that line. A missing cookie file skips that source. An expired cookie file records a `cookies_expired` alert in `state/platform_alerts.json` and leaves `paused` empty. `python -m clipper fetch-public` probes each cookie-free source once. `python -m clipper fetch-cookies` probes the YouTube, TikTok, and Instagram cookie files once and writes `report.json`. Fetch does not call Buffer.

CI cuts that Sui file to an 8-second 720x1280 clip, writes `caption.txt` containing `x.com/SuiNetwork`, and uploads it as the `sui-basecamp-short` artifact and as an asset on the `clip-media` prerelease.

The 15-minute monitor is on unless `CLIPPER_MONITOR` is the lowercase string `false`. Its cron is `7,22,37,52 * * * *` UTC so it does not sit on the quarter hour. When it runs, it writes `submissions/pending.json` in that same run as soon as Buffer reports a campaign post `sent` with a public URL and `sentAt`. The 30-minute clock uses `sentAt`, not the scheduled time. The same poll pauses a platform in `state/platform_alerts.json` when Buffer reports the post `error`, `failed`, `rejected`, `removed`, or `deleted`. A missing cookie, an expired cookie, a skipped content-plan source, or a video URL this repo could not host does not pause. An expired cookie writes a `cookies_expired` alert and leaves `paused` empty. A bad media URL writes a `media_url` alert and the slot is retried on the next run. The job is capped at 3 minutes. `BUFFER_API_TOKEN` is passed in so the poll can run.

`python -m clipper run --live` posts one due slot per enabled platform (X, YouTube Shorts, TikTok) through Buffer's GraphQL `createPost` at `https://api.buffer.com`. It runs unless `CLIPPER_LIVE` is the lowercase string `false`, and it still requires `BUFFER_API_TOKEN` and a direct public `https` video URL (`200` or `206`, `video/mp4`, no redirect). The mp4 is uploaded to the `clip-media` release first, Publish Pages copies `live-*.mp4` to `https://<owner>.github.io/<repo>/live/`, and the runner checks that URL before `createPost`. `python -m clipper validate-media-url` does only that check. It refuses `example.invalid` and a `github.com/.../releases/download/` link. A slot that is already in the past is sent as `shareNow` so Buffer does not reject a past `dueAt`. Each post gets its own caption and a rotated hashtag slice. The same source is stored in `state/live.json` and is not posted to that platform again. A sent campaign post is written to `submissions/pending.json` with that public URL and `sentAt`. A Buffer or account error pauses that platform until you edit `state/platform_alerts.json`. A media-URL miss does not. The Actions job is `17 */2 * * *` UTC and can still be dispatched by hand. It starts unless `CLIPPER_LIVE` is `false`.

## Secrets and variables

Set these in the GitHub repo (Settings → Secrets and variables → Actions). Do not commit them.

| Name | Kind | Required | Purpose |
| --- | --- | --- | --- |
| `BUFFER_API_TOKEN` | secret | live posting | Bearer token for `https://api.buffer.com` GraphQL `createPost` |
| `YT_COOKIES` | secret | optional | Netscape cookie file contents for yt-dlp on YouTube. The workflow writes a temp file and unsets the raw secret |
| `X_COOKIES` | secret | optional | Same for X |
| `INSTAGRAM_COOKIES` | secret | optional | Netscape cookies for public Reels used as a source for X, YouTube, and TikTok. Instagram posting stays off. A missing or expired file is skipped |
| `TIKTOK_COOKIES` | secret | optional | Same for TikTok |
| `ALERT_WEBHOOK_URL` | secret | optional | Alert when a platform is stopped for errors or rate limits |
| `JACKELYNE_BUFFER_CHANNEL_INSTAGRAM` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `JACKELYNE_BUFFER_CHANNEL_TIKTOK` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `JACKELYNE_BUFFER_CHANNEL_YOUTUBE` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `BUFFER_CHANNEL_X` | variable | optional | Overrides the public X channel id |
| `BUFFER_CHANNEL_YOUTUBE` | variable | optional | Overrides Denzen |
| `BUFFER_CHANNEL_INSTAGRAM` | variable | optional | Overrides @benreelr |
| `BUFFER_CHANNEL_TIKTOK` | variable | optional | Overrides @bi67325 |
| `CLIPPER_LIVE` | variable | off switch | Live posting is on unless this is the lowercase string `false` |
| `CLIPPER_MONITOR` | variable | off switch | The 15-minute monitor is on unless this is the lowercase string `false` |
| `GITHUB_TOKEN` | provided | release upload | Uploads rendered mp4s to the `clip-media` release. Already available to Actions |

There is no Content Rewards API key. Account keys cannot submit clip URLs.

Buffer organization `6ac41da09a0b5aca7c671500` and the four channel ids were already public in this repo. They are account identifiers, not tokens.

## Content Rewards queue

When a Buffer post is actually live (`sent` and an external link), append one object to `submissions/pending.json`. The 30-minute clock starts at that live time, not at the scheduled time. Views are counted from the moment the URL is submitted.

Stable raw URL after this lands on `main`:

https://raw.githubusercontent.com/chatgpt820970/chad-media/main/submissions/pending.json

Mark an item submitted by either:

- Actions → Clip monitor → Run workflow, and pass the item `id` or `post_url`, or
- set that item's `status` to `submitted` in `submissions/pending.json` and commit.

`submissions/views.json` records Buffer view counts. Buffer refreshes metrics about once a day, so the numbers lag. Rows are flagged at 1,000 views and at the campaign `min_views`.

The assistant pastes each URL into Content Rewards in the browser. Whop-linked accounts do not count as a Content Rewards connection. The post has to be public, from an account connected inside Content Rewards, within 30 minutes, one URL per line.

## Workflows

- **Clip pipeline** runs on pull requests and on `main`. It installs ffmpeg and tesseract, runs the unit tests, `python -m clipper check`, and a dry-run. The dry-run must exit 0, schedule Sui BaseCamp, leave Dhan Aziz as a draft, and keep every payload on `example.invalid`. It also checks that the GitHub Pages mp4 is a direct `video/mp4` and that the release download URL is rejected. That check does not call Buffer. **fetch-media** downloads the Sui Drive clip, the Dhan Aziz Dropbox zip, and the content-plan sources, cuts an 8-second 720x1280 short, and uploads it to the `clip-media` release. **public-sources** probes X, TikTok, Reddit, and a YouTube frontend without cookies and uploads `report.json`. **cookie-downloads** probes `YT_COOKIES`, `TIKTOK_COOKIES`, and `INSTAGRAM_COOKIES` and uploads that report too. A source that does not download a video is recorded as unavailable, missing, or `cookies_expired` and does not fail the job. None of these jobs call Buffer.
- **Clip live** is scheduled at minute 17 of every even UTC hour (`17 */2 * * *`) and can be dispatched by hand. The job runs unless `CLIPPER_LIVE` is `false`. It posts the one peak-hour slot due in that window on X, YouTube, and TikTok. The ledger commit still runs when the post step fails, so a pause or a media-URL alert is pushed.
- **Clip monitor** is scheduled at minutes 7, 22, 37, and 52 and runs unless `CLIPPER_MONITOR` is `false`. Dispatch it to mark a submission. It receives `BUFFER_API_TOKEN` and writes pause alerts into `state/platform_alerts.json`. The job is capped at 3 minutes.
- **Whop Clipper** and **Shorts** are retired echo jobs. They no longer download, rebase, or upload video artifacts.
- **Prepare media** and **Scanner watchdog** are unchanged. Leave them disabled.
- **Publish Pages** copies `live-*.mp4` from the `clip-media` release into `/live/` on the same site as the news and shorts files. Live posting dispatches that workflow after the release upload finishes.

Videos are gitignored (`*.mp4`). Do not commit mp4s. Rendered files for a live post belong on the `clip-media` release, and Buffer fetches the Pages URL under `/live/`.

## What this does not automate

- Submitting a post URL to Content Rewards. No creator submit endpoint is documented. Whop's bounty API is a different product. Content-plan posts (X, and leftover TikTok, Instagram, and YouTube slots) are not submitted there.
- Approving a Dhan Aziz draft. The run only lists `awaiting_approval`. Add the `source_id` to `state/approvals.json` after you have looked at the file. The live cron does not download the Dropbox zip.
- Turning Instagram back on. Set `platforms.instagram.enabled` to true in `config/channels.json` once that account can link to Content Rewards. No campaign platform list has to change.
- Clearing a platform pause. Delete that platform's entry under `paused` in `state/platform_alerts.json` after you report the Buffer or removal error. The file is the alert log.
- Rolling Stone (30% Tier 1 and music pages) and Oz Ali (60% Tier 1 and 45% UK). Both stay inactive.
- Jackelyne fan-page username, bio (`@jackyalvvarez` and `https://www.instagram.com/jackyalvvarez/` plus a meme line), YouTube bio `@Jackyalvvarez`, profile photo, and pinned comment. Buffer cannot set those. `#unboxd` is in the caption because it was requested; it is not in the public brief excerpt.
- The official Jackelyne logo. The file in `logos/` is a placeholder, and live posting is blocked while `placeholder` is true.
- CrookBrown's TikTok sound "Wait For Me". Buffer has no sound field, so the post is a notification reminder.
- A24 hashtags and any watermark. They sit behind the signed-in brief. The caption names SLAYGROUND and 29 January 2027 and does not invent tags. Evangelist rules are not copied.
- Team Minogue hashtags. None were on the public card. The caption names Mike Minogue and starts with `#Ad`.
- A refreshed budget. `budget_snapshot` is a manual figure. A campaign with remaining budget below `min_payout` scores 0.
- View counts newer than Buffer's daily refresh.
- Watermarks that OCR misses, or marks that sit in the middle of the frame (those clips are skipped).
- Git history size. Removing the mp4s shrinks the current tree only. Purging history with `git filter-repo` is a separate owner step. Do not force-push `main` from this pipeline.

## Local check

Python 3.12, ffmpeg, and tesseract. No third-party packages are required for the dry-run.

```bash
python -m unittest discover -s tests -v
python -m clipper check
python -m clipper run --dry-run --output-dir work/dry-run
```
