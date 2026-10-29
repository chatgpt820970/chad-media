"""Open or update one ci-failed issue when a backup slot still did not post."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def _run(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False, env=env)


def upsert_ci_issue(platform: str, body: str, repo: str, token: str) -> str:
    if not repo or not token:
        raise RuntimeError("GITHUB_REPOSITORY and GH_TOKEN are required to open an issue")
    env = os.environ.copy()
    env["GH_TOKEN"] = token
    title = f"ci-failed: growth-{platform}"
    _run(
        ["gh", "label", "create", "ci-failed", "--repo", repo, "--color", "B60205", "--description", "Growth slot failed after its backup run"],
        env,
    )
    listed = _run(
        ["gh", "issue", "list", "--repo", repo, "--state", "open", "--label", "ci-failed", "--search", f"in:title {title}", "--json", "number,title"],
        env,
    )
    number = ""
    try:
        rows = json.loads(listed.stdout or "[]")
    except json.JSONDecodeError:
        rows = []
    for row in rows:
        if str(row.get("title") or "") == title:
            number = str(row.get("number") or "")
            break
    if number:
        commented = _run(["gh", "issue", "comment", number, "--repo", repo, "--body", body], env)
        if commented.returncode != 0:
            raise RuntimeError((commented.stderr or commented.stdout or "issue comment failed")[-300:])
        return number
    created = _run(
        ["gh", "issue", "create", "--repo", repo, "--title", title, "--label", "ci-failed", "--body", body],
        env,
    )
    if created.returncode != 0:
        raise RuntimeError((created.stderr or created.stdout or "issue create failed")[-300:])
    return (created.stdout or "").strip()


def alert_from_log(platform: str, log_path: Path, repo: str, token: str) -> str:
    tail = ""
    if log_path.is_file():
        lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        tail = "\n".join(lines[-12:])
    body = f"Growth {platform} did not post a slot after the backup run.\n\n```\n{tail}\n```\n"
    return upsert_ci_issue(platform, body, repo, token)
