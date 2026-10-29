"""Gemini caption helper. A failed call falls back to the platform template."""

from __future__ import annotations

import base64
import json
import os
import urllib.request
from collections.abc import Callable

from growth.utils.ytdlp import Candidate

DEFAULT_MODEL = "gemini-2.0-flash"


def generate(prompt: str, api_key: str, model: str = DEFAULT_MODEL, timeout: int = 25) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps(
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"temperature": 0.6, "maxOutputTokens": 220},
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    parts = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text") or "") for part in parts).strip()
    if not text:
        raise RuntimeError("gemini returned an empty caption")
    return text


def generate_vision(prompt: str, images: list[tuple[str, bytes]], api_key: str, model: str = DEFAULT_MODEL, timeout: int = 45) -> str:
    parts: list[dict] = [{"text": prompt}]
    for mime, data in images:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}})
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400}}).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    chunks = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    text = "".join(str(part.get("text") or "") for part in chunks).strip()
    if not text:
        raise RuntimeError("gemini vision returned nothing")
    return text


def caption_or_template(
    prompt: str,
    template: str,
    review: Callable[[str, Candidate], list[str]],
    candidate: Candidate,
    api_key: str = "",
    model: str = "",
) -> str:
    key = (api_key or os.environ.get("GEMINI_API_KEY") or "").strip()
    chosen = (model or os.environ.get("GEMINI_MODEL") or DEFAULT_MODEL).strip()
    if not key:
        return template
    try:
        text = generate(prompt, key, chosen)
    except Exception:
        return template
    if review(text, candidate):
        return template
    return text
