# chad-media

Clipping pipeline for an India-based clipper posting through [Buffer](https://publish.buffer.com) and submitting those posts by hand to [Content Rewards](https://contentrewards.com). GitHub Actions plans the day, renders vertical clips, and can schedule Buffer posts. It does not submit to Content Rewards.

Joined campaigns are **Sui BaseCamp** and **Dhan Aziz**. Everything else in `config/campaigns.json` is inactive. Dhan Aziz is prepared as a draft and is not posted until that clip's id is listed in `state/approvals.json`.

X has no campaign. X posts the owner's own plan (trending, celebrity, 4K anime), clean, with no logo. TikTok, Instagram, and YouTube slots that a campaign does not fill use that platform's content plan, also with no logo.

## What a run does

1. Read `config/campaigns.json`, `config/sources.json`, `config/channels.json`, and `config/pipeline.json`.
2. Drop clips that match the safety guard (sexual or nude, gore, hate, dangerous acts, misleading or spam captions). Romantic, movie, anime, and sports clips stay allowed.
3. Skip a source that still has a burned-in creator or platform watermark after one cover pass. A mark in the center is always skipped. The campaign logo, if that campaign requires one, is overlaid only after the frame is clean. Branding is a transparent PNG, not a text box.
4. Assign campaign footage only when that campaign is active, allows the platform and content type, fits the duration, has budget left for at least one minimum payout, and is not a duplicate. Sui BaseCamp is cut from its Google Drive folder. Dhan Aziz is an exact Dropbox repost and stays in `awaiting_approval` until you approve it. X, and any short-form slot a campaign does not take, uses the platform content plan with no logo and no campaign caption.
5. Split the day's slots by expected earnings. Same source never posts twice on one platform. Campaigns marked `one_shortform` post once inside YouTube, Instagram, and TikTok.
6. Schedule ten slots a day on each platform, about two hours apart, in IST. The quiet gap sits in that audience's dead hours. X, YouTube, Instagram, and TikTok do not share one clock. Edit the times in `config/pipeline.json`.

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

X, weighted to US evening, Europe evening, and India evening. Quiet about 10:15–14:00 IST:

`14:00, 16:15, 18:30, 20:45, 23:00, 01:15, 03:30, 05:45, 08:00, 10:15`

YouTube Shorts and Instagram, India lunch and evening. Quiet about 03:15–07:00 IST:

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

Dry-run writes `work/dry-run/report.md`, `report.json`, `buffer_payloads.json`, and `awaiting_approval.json`. It does not download, post, or edit `submissions/pending.json`. Planned Buffer payloads use `example.invalid` URLs and are not sent. Dhan Aziz stays out of that payload list until you approve it.

`python -m clipper run --live` also requires the variable `CLIPPER_LIVE=true`, and it still refuses `example.invalid` URLs.

## Secrets and variables

Set these in the GitHub repo (Settings → Secrets and variables → Actions). Do not commit them.

| Name | Kind | Required | Purpose |
| --- | --- | --- | --- |
| `BUFFER_API_TOKEN` | secret | live posting | Bearer token for `https://api.buffer.com` GraphQL `createPost` |
| `YT_COOKIES` | secret | optional | Netscape cookie file contents for yt-dlp on YouTube. The workflow writes a temp file and unsets the raw secret |
| `X_COOKIES` | secret | optional | Same for X |
| `INSTAGRAM_COOKIES` | secret | optional | Same for Instagram |
| `TIKTOK_COOKIES` | secret | optional | Same for TikTok |
| `ALERT_WEBHOOK_URL` | secret | optional | Alert when a platform is stopped for errors or rate limits |
| `JACKELYNE_BUFFER_CHANNEL_INSTAGRAM` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `JACKELYNE_BUFFER_CHANNEL_TIKTOK` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `JACKELYNE_BUFFER_CHANNEL_YOUTUBE` | secret or variable | Jackelyne only | Fan-page Buffer channel id |
| `BUFFER_CHANNEL_X` | variable | optional | Overrides the public X channel id |
| `BUFFER_CHANNEL_YOUTUBE` | variable | optional | Overrides Denzen |
| `BUFFER_CHANNEL_INSTAGRAM` | variable | optional | Overrides @benreelr |
| `BUFFER_CHANNEL_TIKTOK` | variable | optional | Overrides @bi67325 |
| `CLIPPER_LIVE` | variable | live posting | Must be the string `true` or scheduled live posts stay off |
| `CLIPPER_MONITOR` | variable | deadline cron | Must be `true` or the 15-minute monitor job is skipped and does not use a runner |
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

- **Clip pipeline** runs on pull requests and on `main`. It installs ffmpeg and tesseract, runs the unit tests, `python -m clipper check`, and a dry-run. The dry-run must exit 0, schedule Sui BaseCamp, leave Dhan Aziz as a draft, and keep every payload on `example.invalid`.
- **Clip monitor** is scheduled every 15 minutes but the job is skipped unless `CLIPPER_MONITOR` is `true`. Dispatch it to mark a submission.
- **Whop Clipper** and **Shorts** are retired echo jobs. They no longer download, rebase, or upload video artifacts.
- **Prepare media** and **Scanner watchdog** are unchanged. Leave them disabled.
- **Publish Pages** is unchanged.

Videos are gitignored (`*.mp4`). Do not commit mp4s. Rendered files for a live post belong on the `clip-media` release so Buffer can fetch a public URL.

## What this does not automate

- Submitting a post URL to Content Rewards. No creator submit endpoint is documented. Whop's bounty API is a different product. Content-plan posts (X, and leftover TikTok, Instagram, and YouTube slots) are not submitted there.
- Approving a Dhan Aziz draft. The run only lists `awaiting_approval`. Add the `source_id` to `state/approvals.json` after you have looked at the file.
- Downloading the Drive or Dropbox folders inside CI. The dry-run records `gdown --folder` and a Dropbox `dl=1` curl. It does not fetch the videos.
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
