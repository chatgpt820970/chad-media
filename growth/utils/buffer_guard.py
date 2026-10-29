"""Runtime Buffer checks: the channel exists, and the free-plan queue is under 9."""

from __future__ import annotations

from clipper.buffer_api import BufferError, create_post, graphql, post_nodes

CHANNEL_QUERY = """
query GrowthChannel($id: ChannelId!) {
  channel(input: { id: $id }) {
    id
    isDisconnected
    isLocked
  }
}
"""

QUEUED_QUERY = """
query GrowthQueued($organizationId: OrganizationId!, $channelId: ChannelId!) {
  posts(first: 10, input: { organizationId: $organizationId, filter: { channelIds: [$channelId], status: [scheduled] } }) {
    edges { node { id status } }
  }
}
"""

API = "https://api.buffer.com"


def channel_is_ready(token: str, channel_id: str, url: str = API) -> tuple[bool, str]:
    try:
        data = graphql(url, token, CHANNEL_QUERY, {"id": channel_id})
    except BufferError as exc:
        return False, str(exc)[:240]
    channel = data.get("channel") or {}
    if not channel.get("id"):
        return False, "buffer channel was not found"
    if channel.get("isDisconnected"):
        return False, "buffer channel is disconnected"
    if channel.get("isLocked"):
        return False, "buffer channel is locked"
    return True, ""


def post_video(token: str, payload: dict, url: str = API) -> dict:
    return create_post(url, token, payload)


def scheduled_count(token: str, organization_id: str, channel_id: str, url: str = API) -> int:
    data = graphql(
        url,
        token,
        QUEUED_QUERY,
        {"organizationId": organization_id, "channelId": channel_id},
    )
    return len(post_nodes(data))
