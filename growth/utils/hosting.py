"""Put a rendered mp4 where Buffer can fetch it.

Publish Pages already copies `live-*.mp4` from the `clip-media` release onto
GitHub Pages. Growth uses that copy step as-is and does not edit the workflow.
Files are named `live-growth-<platform>-...` so they stay distinct from campaign clips.
"""

from __future__ import annotations

from pathlib import Path

from clipper.livepost import upload_release_asset
from clipper.mediahost import pages_video_url, publish_live_pages, verify_public_mp4

from growth.utils.retry import MinorFailure
from growth.utils.ytdlp import safe_id


def asset_name(prefix: str, source_id: str) -> str:
    return f"{prefix}-{safe_id(source_id)}.mp4"


def public_page_url(repo: str, name: str) -> str:
    return pages_video_url(repo, name)


class PagesPublisher:
    def __init__(self, repo: str, token: str, tag: str):
        self.repo = repo
        self.token = token
        self.tag = tag
        self.urls: dict[str, str] = {}

    def add(self, path: str, name: str) -> str:
        if not self.repo or not self.token:
            raise MinorFailure("GH_TOKEN and GITHUB_REPOSITORY are required to publish media")
        if not path or not Path(path).is_file():
            raise MinorFailure("rendered file is missing")
        upload_release_asset(Path(path), name, repo=self.repo, tag=self.tag, token=self.token)
        url = pages_video_url(self.repo, name)
        self.urls[name] = url
        return url

    def finalize(self) -> dict[str, str]:
        """Publish Pages once, then verify each file. Values are error strings."""
        if not self.urls:
            return {}
        try:
            publish_live_pages(self.repo, self.token)
        except Exception as exc:
            message = str(exc)[:300]
            return {name: message for name in self.urls}
        errors: dict[str, str] = {}
        for name, url in self.urls.items():
            try:
                verify_public_mp4(url)
            except Exception as exc:
                errors[name] = str(exc)[:300]
        return errors
