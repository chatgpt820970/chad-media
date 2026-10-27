"""Buffer's current GraphQL API. Dry-run builds the same payload and does not send it."""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone

CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess {
      post { id status dueAt sentAt externalLink text }
    }
    ... on MutationError { message }
  }
}
"""

POSTS_QUERY = """
query Posts($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(first: 30, input: { organizationId: $organizationId, filter: { channelIds: [$channelId] } }) {
    edges {
      node {
        id
        status
        dueAt
        sentAt
        externalLink
        text
        metrics { type value }
      }
    }
  }
}
"""


class BufferError(RuntimeError):
    pass


def due_stamp(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_post_input(package: dict, channel_id: str, video_url: str) -> dict:
    platform = package["platform"]
    caption = package["caption"]
    payload: dict = {
        "channelId": channel_id,
        "text": caption,
        "schedulingType": "notification" if package["status"] == "reminder" else "automatic",
        "mode": "customScheduled",
        "dueAt": package["due_at"],
        "assets": [{"video": {"url": video_url}}],
    }
    if platform == "instagram":
        payload["metadata"] = {"instagram": {"type": "reel", "shouldShareToFeed": True}}
    elif platform == "youtube":
        title = caption.split("\n")[-1][:100] or "Short"
        payload["metadata"] = {
            "youtube": {
                "title": title[:100],
                "categoryId": "24",
                "privacy": "public",
                "madeForKids": False,
            }
        }
    elif platform == "tiktok":
        payload["metadata"] = {"tiktok": {"title": caption.split("\n")[0][:150]}}
    return payload


def graphql(url: str, token: str, query: str, variables: dict) -> dict:
    if not token:
        raise BufferError("BUFFER_API_TOKEN is empty")
    body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if payload.get("errors"):
        message = payload["errors"][0].get("message", "buffer error")
        raise BufferError(message)
    return payload.get("data") or {}


def create_post(url: str, token: str, post_input: dict) -> dict:
    if "example.invalid" in json.dumps(post_input):
        raise BufferError("refusing to send a dry-run URL")
    data = graphql(url, token, CREATE_POST, {"input": post_input})
    result = data.get("createPost") or {}
    if result.get("__typename") == "MutationError" or result.get("message"):
        raise BufferError(result.get("message") or "createPost failed")
    post = result.get("post")
    if not post:
        raise BufferError("createPost returned no post")
    return post


def view_count(metrics: list[dict] | None) -> int | None:
    for metric in metrics or []:
        if metric.get("type") == "views":
            return int(float(metric.get("value") or 0))
    return None


def is_rate_limited(message: str) -> bool:
    text = (message or "").lower()
    return any(token in text for token in ("rate limit", "too many requests", "429", "quota exceeded"))


def should_stop_platform(message: str, error_count: int, limit: int) -> bool:
    """Stop the platform on a rate limit, or after `limit` other errors. Do not retry."""
    if is_rate_limited(message):
        return True
    return error_count >= limit


class PlatformHalted(BufferError):
    def __init__(self, platform: str, message: str):
        super().__init__(message)
        self.platform = platform


class ErrorBudget:
    """Per-platform error counter. A halted platform is not posted to again in the run."""

    def __init__(self, limit: int = 2):
        self.limit = limit
        self.counts: dict[str, int] = {}
        self.halted: set[str] = set()

    def record(self, platform: str, message: str) -> bool:
        self.counts[platform] = self.counts.get(platform, 0) + 1
        if should_stop_platform(message, self.counts[platform], self.limit):
            self.halted.add(platform)
            return True
        return False
