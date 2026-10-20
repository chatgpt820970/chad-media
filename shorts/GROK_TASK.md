# Grok task: post the shorts queue

Paste this whole note to your Grok assistant. It needs Buffer connected, with the YouTube channel in that Buffer organization. No other keys.

## Every day, post 2 or 3 clips

1. Open the feed:
   https://chatgpt820970.github.io/chad-media/feed/shorts_queue.json
2. Load your own posted-id list (start it empty and keep it across chats). Also skip any id already in that list, and any item whose `status` is not `ready`.
3. From the remaining `ready` items, pick 2 or 3 for today. Prefer a mix: one `chinese` or `funny` clip, and one `feelgood` clip when both are available. Prefer higher items in the feed when you have no other reason to choose.
4. For each pick, open all three `frame_urls` and look at the pictures.
   - Skip the clip if any frame shows a TikTok, Instagram, or X logo, a username, or an `@handle` watermark. Do not ask anyone to blur or crop it out.
   - Skip it if it is not actually funny, or not actually warm and feel-good. A dull ad, a trailer, or a clip that only exists to sell something does not qualify.
   - Skip it if `duration` is under 30 or not under 3 minutes.
   - Add every skipped id to your posted-id list so you do not review it again. Do not post it.
5. For a clip that passes, write a new catchy title (under 90 characters) and a short caption in your own words. Use `title` and `caption` only as a draft. Do not mention TikTok, Instagram, or X, and do not leave a source `@handle` in the text.
6. Post it with Buffer to the YouTube channel:
   - `channelId`: the YouTube channel from `list_channels` for this Buffer organization. Do not guess the id.
   - `schedulingType`: `automatic`
   - `mode`: `addToQueue`
   - `text`: the caption you wrote
   - `assets`: one video whose `url` is exactly this item's `mp4_url` (it is a public `video/mp4` file)
   - `metadata.youtube.title`: the title you wrote
   - `metadata.youtube.categoryId`: the item's `youtube_category_id` (`23` comedy, `10` music)
   - `metadata.youtube.privacy`: `public`
   - `metadata.youtube.madeForKids`: false
7. After Buffer accepts the post, append that item's `id` to your posted-id list. Never post the same id again.
8. Stop at 2 or 3 successful posts for the day even if more `ready` items remain. Tomorrow's feed will still have them until they age out (about 4 days).

The pipeline already refused YouTube sources, clips under 30 seconds, and files it could tell were watermarked. The frames are still your check. If the feed is empty or every item is skipped, post nothing.
