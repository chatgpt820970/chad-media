"""Gemini helpers. YouTube and Instagram send the whole mp4, audio included."""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

from growth.utils.ytdlp import Candidate

DEFAULT_MODEL = "gemini-3.8-flash"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILES_URL = "https://generativelanguage.googleapis.com/v1beta"


def _http_detail(exc: Exception) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:400]  # type: ignore[attr-defined]
    except Exception:
        return ""


FALLBACK_MODELS = ("gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash")
RETRIES = 3
BACKOFF = 4.0
_sleep = time.sleep


class QuotaExhausted(RuntimeError):
    """Every key hit a 429 quota error."""


def api_keys(primary: str = "") -> list[str]:
    """GEMINI_API_KEY plus optional GEMINI_API_KEY_2 / _3, deduplicated, primary first."""
    keys: list[str] = []
    for value in (primary, os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_API_KEY_2"), os.environ.get("GEMINI_API_KEY_3")):
        value = (value or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def model_chain(model: str) -> list[str]:
    chain = [m for m in [model, *FALLBACK_MODELS] if m]
    return list(dict.fromkeys(chain))


def _status(exc: Exception) -> int:
    return int(getattr(exc, "code", 0) or 0)


def _retry_delay(detail: str) -> float:
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', detail) or re.search(r"retry in (\d+(?:\.\d+)?)s", detail)
    return min(90.0, float(match.group(1))) if match else 0.0


def _is_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in text


def _is_quota(detail: str) -> bool:
    return "quota" in detail.lower() or "RESOURCE_EXHAUSTED" in detail


def call_with_retry(send: Callable[[], dict], label: str = "gemini") -> dict:
    """Run `send` with exponential backoff on 429, 503, 500 and timeouts.

    Raises QuotaExhausted right away on a 429 whose body is a quota error so the
    caller can rotate keys. Other errors are re-raised with the API body attached.
    """
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            return send()
        except Exception as exc:  # noqa: BLE001
            detail = _http_detail(exc)
            status = _status(exc)
            if status == 429 and _is_quota(detail) and "PerDay" in detail:
                raise QuotaExhausted(f"{label}: daily quota exhausted") from exc
            retryable = status in (429, 500, 503) or _is_timeout(exc)
            if not retryable or attempt == RETRIES:
                if status == 429 and _is_quota(detail):
                    raise QuotaExhausted(f"{label}: quota exhausted") from exc
                raise RuntimeError(f"{label}: {exc} {detail}") from exc
            wait = _retry_delay(detail) or BACKOFF * (2 ** attempt)
            print(f"{label}: {status or 'timeout'}, retry {attempt + 1}/{RETRIES} in {wait:.0f}s", flush=True)
            _sleep(wait)
            last = exc
    raise RuntimeError(f"{label}: {last}")


def _post(url: str, body: bytes, api_key: str, timeout: int) -> dict:
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def generate_json(body: dict, api_key: str, model: str, timeout: int, rotate: bool = True) -> dict:
    """POST generateContent with retries, model fallback on 404, and key rotation on quota errors."""
    data = json.dumps(body).encode("utf-8")
    keys = api_keys(api_key) if rotate else [api_key]
    if not keys:
        raise RuntimeError("no gemini api key")
    errors: list[str] = []
    for key in keys:
        for name in model_chain(model):
            url = f"{FILES_URL}/models/{name}:generateContent"
            try:
                return call_with_retry(lambda: _post(url, data, key, timeout), f"gemini {name}")
            except QuotaExhausted as exc:
                errors.append(str(exc))
                print(f"{exc}; rotating to the next key", flush=True)
                break
            except RuntimeError as exc:
                errors.append(str(exc)[:300])
                if " 404" in str(exc) or "Not Found" in str(exc):
                    continue
                raise
    raise QuotaExhausted("; ".join(errors)[-600:] or "gemini failed")


def generate(prompt: str, api_key: str, model: str = DEFAULT_MODEL, timeout: int = 25) -> str:
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 220},
    }
    text = _response_text(generate_json(body, api_key, model, timeout))
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
    """Send the mp4, with audio, and return Gemini's text.

    Uploaded files belong to the key's project, so on a quota error the file is
    re-uploaded under the next key.
    """
    keys = api_keys(api_key)
    if not keys:
        raise RuntimeError("no gemini api key")
    errors: list[str] = []
    for key in keys:
        try:
            uploaded = call_with_retry(lambda: upload_video(path, key, timeout=timeout), "gemini upload")
        except QuotaExhausted as exc:
            errors.append(str(exc))
            continue
        try:
            uri = str(uploaded.get("uri") or "")
            if not uri:
                raise RuntimeError("gemini file has no uri")
            body = {
                "contents": [
                    {
                        "parts": [
                            {"file_data": {"mime_type": "video/mp4", "file_uri": uri}},
                            {"text": prompt},
                        ]
                    }
                ],
                "generationConfig": {"temperature": 0.4, "maxOutputTokens": 900},
            }
            try:
                payload = generate_json(body, key, model, timeout, rotate=False)
            except QuotaExhausted as exc:
                errors.append(str(exc))
                print("gemini key out of quota; trying the next key", flush=True)
                continue
            text = _response_text(payload)
            if not text:
                raise RuntimeError("gemini returned nothing after watching the video")
            return text
        finally:
            delete_video(str(uploaded.get("name") or ""), key)
    raise QuotaExhausted("all gemini keys out of quota: " + "; ".join(errors)[-400:])


def generate_vision(prompt: str, images: list[tuple[str, bytes]], api_key: str, model: str = DEFAULT_MODEL, timeout: int = 45) -> str:
    parts: list[dict] = [{"text": prompt}]
    for mime, data in images:
        parts.append({"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode("ascii")}})
    body = {"contents": [{"parts": parts}], "generationConfig": {"temperature": 0.2, "maxOutputTokens": 400}}
    text = _response_text(generate_json(body, api_key, model, timeout))
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
