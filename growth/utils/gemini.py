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

DEFAULT_MODEL = "gemini-3.8-flash"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILES_URL = "https://generativelanguage.googleapis.com/v1beta"


_RESOLVED: dict[str, str] = {}


def _http_detail(exc: Exception) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:400]  # type: ignore[attr-defined]
    except Exception:
        return ""


def resolve_model(model: str, api_key: str) -> str:
    """Return `model` if the API serves it, else the newest flash model that supports generateContent."""
    if model in _RESOLVED:
        return _RESOLVED[model]
    try:
        request = urllib.request.Request(f"{FILES_URL}/models?pageSize=1000", headers={"x-goog-api-key": api_key}, method="GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            listing = json.loads(response.read().decode("utf-8")).get("models") or []
    except Exception as exc:
        print(f"gemini model listing failed: {exc} {_http_detail(exc)}", flush=True)
        return model
    names = [
        str(item.get("name") or "").removeprefix("models/")
        for item in listing
        if "generateContent" in (item.get("supportedGenerationMethods") or [])
    ]
    chosen = model
    if model not in names:
        flash = [n for n in names if "flash" in n and "lite" not in n and "image" not in n and "tts" not in n and "live" not in n and "exp" not in n]
        stable = [n for n in flash if "preview" not in n] or flash
        if stable:
            latest = [n for n in stable if "latest" in n]
            chosen = latest[0] if latest else sorted(stable)[-1]
        print(f"gemini model {model} not served; using {chosen}", flush=True)
    _RESOLVED[model] = chosen
    return chosen


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
    try:
        uploaded = upload_video(path, api_key, timeout=timeout)
    except Exception as exc:
        raise RuntimeError(f"file upload: {exc} {_http_detail(exc)}") from exc
    try:
        uri = str(uploaded.get("uri") or "")
        if not uri:
            raise RuntimeError("gemini file has no uri")
        model = resolve_model(model, api_key)
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
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            detail = _http_detail(exc)
            import re as _re
            hint = _re.search(r"use models/([\w.\-]+)", detail)
            if not hint or hint.group(1) == model:
                raise RuntimeError(f"generateContent {model}: {exc} {detail}") from exc
            print(f"gemini model {model} retired; retrying with {hint.group(1)}", flush=True)
            _RESOLVED[model] = hint.group(1)
            request.full_url = f"{FILES_URL}/models/{hint.group(1)}:generateContent"
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
