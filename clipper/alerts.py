"""Print a GitHub Actions error and optionally POST it to ALERT_WEBHOOK_URL."""

from __future__ import annotations

import json
import os
import urllib.request


def alert(message: str, webhook: str | None = None) -> None:
    print(f"::error::{message}")
    url = webhook if webhook is not None else os.environ.get("ALERT_WEBHOOK_URL") or ""
    if not url:
        return
    body = json.dumps({"text": message}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            response.read()
    except OSError as exc:
        print(f"alert webhook failed: {exc.__class__.__name__}")
