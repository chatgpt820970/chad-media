"""Public video URLs Buffer can fetch.

GitHub release download links answer 302 and then application/octet-stream
with an expiring signature. Buffer rejects those. Live clips are copied onto
GitHub Pages, which returns 200 video/mp4 with no redirect. The file is on
the release before Pages is published, and createPost is not called until a
GET from this runner sees a real mp4.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone


class MediaURLError(RuntimeError):
    """The video URL is our problem. Do not pause the platform."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_MEDIA_MARKERS = (
    "could not be read",
    "video could not",
    "unable to fetch video",
    "unable to read video",
    "invalid video url",
    "invalid post: video",
)


def is_media_url_error(message: str) -> bool:
    text = (message or "").lower()
    return any(marker in text for marker in _MEDIA_MARKERS)


def pages_video_url(repo: str, name: str) -> str:
    owner, repo_name = repo.split("/", 1)
    return f"https://{owner}.github.io/{repo_name}/live/{name}"


def verify_public_mp4(url: str, timeout: int = 25) -> None:
    """HEAD/GET without following redirects. Raises MediaURLError on a miss."""
    text = (url or "").strip()
    if not text.startswith("https://") or "example.invalid" in text:
        raise MediaURLError("refusing a video URL Buffer cannot fetch")
    opener = urllib.request.build_opener(_NoRedirect)
    headers = {"User-Agent": "chad-media"}
    status = 0
    content_type = ""
    try:
        head = opener.open(urllib.request.Request(text, headers=headers, method="HEAD"), timeout=timeout)
        status = int(head.status)
        content_type = head.headers.get("content-type") or ""
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            raise MediaURLError(f"redirect {exc.code}") from exc
        if exc.code != 405:
            raise MediaURLError(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise MediaURLError(f"{exc.__class__.__name__}: {exc}") from exc
    try:
        opened = opener.open(
            urllib.request.Request(text, headers={**headers, "Range": "bytes=0-31"}, method="GET"),
            timeout=timeout,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            raise MediaURLError(f"redirect {exc.code}") from exc
        raise MediaURLError(f"HTTP {exc.code}") from exc
    except Exception as exc:
        raise MediaURLError(f"{exc.__class__.__name__}: {exc}") from exc
    status = int(opened.status or status)
    if status not in (200, 206):
        raise MediaURLError(f"HTTP {status}")
    content_type = (opened.headers.get("content-type") or content_type).split(";", 1)[0].strip().lower()
    if content_type != "video/mp4":
        raise MediaURLError(f"content-type {content_type or 'missing'}")
    magic = opened.read(16)
    if b"ftyp" not in magic:
        raise MediaURLError("body is not an mp4")


def publish_live_pages(repo: str, token: str) -> None:
    """Run Publish Pages so /live/*.mp4 is on the public site. Blocks until it finishes."""
    if not repo or not token:
        raise MediaURLError("GH_TOKEN and GITHUB_REPOSITORY are required to publish media")
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    started = datetime.now(timezone.utc)
    ran = subprocess.run(
        ["gh", "workflow", "run", "publish-pages.yml", "--repo", repo, "--ref", "main"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if ran.returncode != 0:
        detail = (ran.stderr or ran.stdout or "workflow run failed")[-300:]
        raise MediaURLError(detail)
    run_id = _wait_for_run(repo, env, started)
    watched = subprocess.run(
        ["gh", "run", "watch", run_id, "--repo", repo, "--exit-status"],
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=1200,
    )
    if watched.returncode != 0:
        detail = (watched.stderr or watched.stdout or "pages deploy failed")[-300:]
        raise MediaURLError(detail)


def _wait_for_run(repo: str, env: dict, started: datetime) -> str:
    for _ in range(20):
        time.sleep(3)
        listed = subprocess.run(
            [
                "gh",
                "run",
                "list",
                "--workflow",
                "publish-pages.yml",
                "--repo",
                repo,
                "--limit",
                "5",
                "--json",
                "databaseId,event,createdAt,status",
            ],
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        if listed.returncode != 0:
            continue
        try:
            rows = json.loads(listed.stdout or "[]")
        except json.JSONDecodeError:
            continue
        for row in rows:
            if row.get("event") != "workflow_dispatch":
                continue
            created = str(row.get("createdAt") or "")
            try:
                moment = datetime.fromisoformat(created.replace("Z", "+00:00"))
            except ValueError:
                continue
            if moment >= started:
                return str(row["databaseId"])
    raise MediaURLError("Publish Pages did not start")
