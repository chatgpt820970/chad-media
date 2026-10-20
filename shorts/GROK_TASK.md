# Grok task: post the shorts queue

Paste this whole note to your Grok assistant. It needs Buffer connected, with the YouTube channel in that Buffer organization. No other keys.

The pipeline prepares clips twice a day. You run twice a day, after each prep, and schedule that batch into Buffer at fixed India times.

## When you run

Prep is dispatched at **06:30 IST** and **14:30 IST**. Start your pass after that prep has had time to publish the feed (check `generated_at`).

Post **7 clips a day**, at these IST clock times:

| After which prep | Schedule these times |
| --- | --- |
| 06:30 IST | 8:00 AM, 11:00 AM, 1:00 PM |
| 14:30 IST | 4:00 PM, 7:00 PM, 9:00 PM, 11:00 PM |

That is 3 clips in the morning pass and 4 in the afternoon pass. Each prep aims to leave at least 4 new `ready` clips (usually 5), so one failed review still leaves a replacement.

## Each pass

1. Open the feed:
   https://chatgpt820970.github.io/chad-media/feed/shorts_queue.json
2. `ready_today` counts items with `status` `ready` whose `queued_at` is today (UTC). `daily_target` is 7. Use that only as a check that the pipeline produced today's clips. Your own posted-id list decides what you still need to schedule.
3. Load your posted-id list (start it empty and keep it across chats). Skip any id already in that list, and any item whose `status` is not `ready`.
4. Call `get_account` and read `currentTime` and the account timezone. Build every `dueAt` from that timezone. The clock times above are **Asia/Kolkata** (IST, offset +05:30). If the Buffer account is already on IST, use that offset directly. If it is not, convert each IST clock time into the account's offset. `dueAt` must be an ISO 8601 timestamp with that offset, and it must be later than `currentTime`.
5. For this pass, take the times in the table that are still in the future. Skip a time that has already passed. Morning pass: up to the three morning times. Afternoon pass: the four afternoon times. If a morning time was missed and is still in the future, the afternoon pass may fill it before the afternoon times.
6. For each time, pick the next remaining `ready` item. Prefer a mix across the day: `chinese` or `funny`, and `feelgood` when both are available.
7. Open all three `frame_urls` and look at the pictures.
   - Skip the clip if any frame shows a TikTok, Instagram, or X logo, a username, or an `@handle` watermark. Do not ask anyone to blur or crop it out.
   - Skip it if it is not actually funny, or not actually warm and feel-good. A dull ad, a trailer, or a clip that only exists to sell something does not qualify.
   - Skip it if `duration` is under 30 or not under 3 minutes.
   - Add every skipped id to your posted-id list so you do not review it again. Do not post it. Use the next ready clip for that same time.
8. For a clip that passes, write a new catchy title (under 90 characters) and a short caption in your own words. Use `title` and `caption` only as a draft. Do not mention TikTok, Instagram, or X, and do not leave a source `@handle` in the text.
9. Post it with Buffer to the YouTube channel:
   - `channelId`: the YouTube channel from `list_channels` for this Buffer organization. Do not guess the id.
   - `schedulingType`: `automatic`
   - `mode`: `customScheduled`
   - `dueAt`: the IST slot for this clip, converted as in step 4. Example shape: `2026-10-20T08:00:00+05:30`
   - `text`: the caption you wrote
   - `assets`: one video whose `url` is exactly this item's `mp4_url` (it is a public `video/mp4` file)
   - `metadata.youtube.title`: the title you wrote
   - `metadata.youtube.categoryId`: the item's `youtube_category_id` (`23` comedy, `10` music)
   - `metadata.youtube.privacy`: `public`
   - `metadata.youtube.madeForKids`: false
10. After Buffer accepts the post, append that item's `id` to your posted-id list. Never post the same id again.
11. Stop when this pass's times are filled, or when no future time and no ready clip remain. Do not use `addToQueue` and do not publish immediately.

The Buffer free plan caps how many posts can sit in the schedule. If Buffer rejects a post for that cap, stop this pass and leave the remaining times for the next one. Do not dump the rest in as queue posts.

The pipeline already refused YouTube sources, clips under 30 seconds, and files it could tell were watermarked. The frames are still your check. If the feed has no new `ready` clips, schedule nothing.
