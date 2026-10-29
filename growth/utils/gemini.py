"""Gemini helpers. YouTube and Instagram send the whole mp4, audio included."""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from growth.utils.ytdlp import Candidate

DEFAULT_MODEL = "gemini-2.0-flash"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILES_URL = "https://generativelanguage.googleapis.com/v1beta"


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


def _response_text(payload: dict) -> str:
    chunks = (((payload.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [])
    return "".join(str(part.get("text") or "") for part in chunks).strip()


def upload_video(path: Path, api_key: str, timeout: int = 180) -> dict:
    """Upload an mp4 through the Gemini Files API and wait until it can be watched."""
    size = path.stat().st_size
    start = urllib.request.Request(
        UPLOAD_URL,
        data=json.dumps({"file": {"display_name": path.name}}).encode("utf-8"),
        headers={
            "x-goog-api-key": api_key,
            "X-Goog-Upload-Protocol": "resumable",
            "X-Goog-Upload-Command": "start",
            "X-Goog-Upload-Header-Content-Length": str(size),
            "X-Goog-Upload-Header-Content-Type": "video/mp4",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(start, timeout=60) as response:
        upload_url = response.headers.get("X-Goog-Upload-URL") or response.headers.get("x-goog-upload-url")
    if not upload_url:
        raise RuntimeError("gemini file upload did not return an upload url")
    put = urllib.request.Request(
        upload_url,
        data=path.read_bytes(),
        headers={
            "Content-Length": str(size),
            "X-Goog-Upload-Offset": "0",
            "X-Goog-Upload-Command": "upload, finalize",
        },
        method="POST",
    )
    with urllib.request.urlopen(put, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    uploaded = payload.get("file") or {}
    name = str(uploaded.get("name") or "")
    if not name:
        raise RuntimeError("gemini file upload returned no file name")
    deadline = time.time() + timeout
    while str(uploaded.get("state") or "") == "PROCESSING":
        if time.time() > deadline:
            raise RuntimeError("gemini file stayed processing")
        time.sleep(2)
        request = urllib.request.Request(
            f"{FILES_URL}/{name}",
            headers={"x-goog-api-key": api_key},
            method="GET",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            uploaded = json.loads(response.read().decode("utf-8"))
    if str(uploaded.get("state") or "") not in ("ACTIVE", ""):
        if uploaded.get("state") and uploaded.get("state") != "ACTIVE":
            raise RuntimeError(f"gemini file state {uploaded.get('state')}")
    return uploaded


def delete_video(name: str, api_key: str) -> None:
    if not name:
        return
    request = urllib.request.Request(
        f"{FILES_URL}/{name}",
        headers={"x-goog-api-key": api_key},
        method="DELETE",
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            return
    except Exception:
        return


def watch_video(path: Path, prompt: str, api_key: str, model: str = DEFAULT_MODEL, timeout: int = 180) -> str:
    """Send the mp4, with audio, and return Gemini's text."""
    uploaded = upload_video(path, api_key, timeout=timeout)
    try:
        uri = str(uploaded.get("uri") or "")
        if not uri:
            raise RuntimeError("gemini file has no uri")
        url = f"{FILES_URL}/models/{model}:generateContent"
        body = json.dumps(
            {
                "contents": [
                    {
                        "parts": [
                            {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                            {"text": prompt},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 700},
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
        text = _response_text(payload)
        if not text:
            raise RuntimeError("gemini returned nothing after watching the video")
        return text
    finally:
        delete_video(str(uploaded.get("name") or ""), api_key)


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
