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

DEFAULT_MODEL = "gemini-flash-latest"
UPLOAD_URL = "https://generativelanguage.googleapis.com/upload/v1beta/files"
FILES_URL = "https://generativelanguage.googleapis.com/v1beta"


def _http_detail(exc: Exception) -> str:
    try:
        return exc.read().decode("utf-8", "replace")[:400]  # type: ignore[attr-defined]
    except Exception:
        return ""


FALLBACK_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.8-flash")
TRANSIENT_RETRIES = 1  # one retry for 503/500/timeouts, then the next model
BACKOFF = 4.0
MINUTE_WAIT = 30.0  # per-minute 429 with no retryDelay
_sleep = time.sleep


class QuotaExhausted(RuntimeError):
    """Every key hit a 429 quota error (or the request budget is spent)."""


class BudgetExhausted(QuotaExhausted):
    """The per-run request budget is spent."""


class ModelUnavailable(RuntimeError):
    """503/500/timeout persisted after the retry, or the model is 404."""


_budget: dict = {"limit": None, "used": 0}


def set_budget(limit: int | None) -> None:
    """Cap every HTTP request to Gemini (uploads and generateContent). None means no cap."""
    _budget["limit"] = limit
    _budget["used"] = 0


def requests_used() -> int:
    return int(_budget["used"])


def requests_left() -> int | None:
    limit = _budget["limit"]
    return None if limit is None else max(0, limit - _budget["used"])


def _charge(label: str) -> None:
    limit = _budget["limit"]
    if limit is not None and _budget["used"] >= limit:
        raise BudgetExhausted(f"{label}: gemini request budget of {limit} used up")
    _budget["used"] += 1


def api_keys(primary: str = "") -> list[str]:
    """GEMINI_API_KEY plus optional GEMINI_API_KEY_2 / _3, deduplicated, primary first."""
    keys: list[str] = []
    for value in (primary, os.environ.get("GEMINI_API_KEY"), os.environ.get("GEMINI_API_KEY_2"), os.environ.get("GEMINI_API_KEY_3")):
        value = (value or "").strip()
        if value and value not in keys:
            keys.append(value)
    return keys


def model_chain(model: str = "") -> list[str]:
    preferred = (os.environ.get("GEMINI_MODEL_FIRST") or "").strip()
    chain = [preferred] if preferred else []
    chain += [m for m in FALLBACK_MODELS if m not in chain]
    if model and model not in chain:
        chain.append(model)
    return chain


def _status(exc: Exception) -> int:
    return int(getattr(exc, "code", 0) or 0)


def _retry_delay(detail: str) -> float:
    match = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', detail) or re.search(r"retry in (\d+(?:\.\d+)?)s", detail)
    return min(90.0, float(match.group(1))) if match else 0.0


def _is_timeout(exc: Exception) -> bool:
    text = str(exc).lower()
    return isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in text


def _per_minute(detail: str) -> bool:
    """True only when the 429 is clearly a per-minute limit and not a per-day one."""
    return "PerMinute" in detail and "PerDay" not in detail


def call_with_retry(send: Callable[[], dict], label: str = "gemini") -> dict:
    """Send one request with the retry policy.

    - 429 per-day (or unclear): QuotaExhausted right away, so the caller moves to the next key.
    - 429 per-minute: wait retryDelay once, retry once, then QuotaExhausted.
    - 503/500/timeout: one retry after a short backoff, then ModelUnavailable (next model).
    - 404: ModelUnavailable. Anything else: RuntimeError.
    Every attempt is charged against the request budget.
    """
    minute_waited = False
    transient = 0
    while True:
        _charge(label)
        try:
            return send()
        except Exception as exc:  # noqa: BLE001
            detail = _http_detail(exc)
            status = _status(exc)
            if status == 429:
                if _per_minute(detail) and not minute_waited:
                    minute_waited = True
                    wait = _retry_delay(detail) or MINUTE_WAIT
                    print(f"{label}: 429 per-minute, waiting {wait:.0f}s once", flush=True)
                    _sleep(wait)
                    continue
                kind = "per-minute again" if minute_waited else "per-day/unclear"
                raise QuotaExhausted(f"{label}: 429 {kind}") from exc
            if status in (500, 502, 503, 504) or _is_timeout(exc):
                if transient < TRANSIENT_RETRIES:
                    transient += 1
                    print(f"{label}: {status or 'timeout'}, retry {transient}/{TRANSIENT_RETRIES} in {BACKOFF:.0f}s", flush=True)
                    _sleep(BACKOFF)
                    continue
                raise ModelUnavailable(f"{label}: {status or 'timeout'} after retry") from exc
            if status == 404:
                raise ModelUnavailable(f"{label}: 404 {detail[:200]}") from exc
            raise RuntimeError(f"{label}: {exc} {detail}") from exc


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
    """POST generateContent: next model on 503/timeout/404, next key on quota."""
    data = json.dumps(body).encode("utf-8")
    keys = api_keys(api_key) if rotate else [api_key]
    if not keys:
        raise RuntimeError("no gemini api key")
    errors: list[str] = []
    unavailable = False
    for key in keys:
        for name in model_chain(model):
            url = f"{FILES_URL}/models/{name}:generateContent"
            try:
                return call_with_retry(lambda: _post(url, data, key, timeout), f"gemini {name}")
            except BudgetExhausted:
                raise
            except QuotaExhausted as exc:
                errors.append(str(exc))
                print(f"{exc}; switching to the next key", flush=True)
                break
            except ModelUnavailable as exc:
                errors.append(str(exc)[:200])
                unavailable = True
                print(f"{exc}; trying the next model", flush=True)
                continue
        else:
            # every model was unavailable for this key; another key will not fix a 503
            raise RuntimeError("all gemini models unavailable: " + "; ".join(errors)[-400:])
    if unavailable and not any("429" in e for e in errors):
        raise RuntimeError("; ".join(errors)[-400:])
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


PROXY_HEIGHT = 360
PROXY_FPS = 15
PROXY_SECONDS = 60


def make_proxy(path: Path) -> Path:
    """Low-res copy for review only: 360p, 15fps, at most 60s, mono audio. Falls back to the original."""
    import subprocess
    import tempfile

    out = Path(tempfile.mkdtemp(prefix="gemini-proxy-")) / f"{path.stem}.proxy.mp4"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error", "-t", str(PROXY_SECONDS), "-i", str(path),
        "-vf", f"scale=-2:{PROXY_HEIGHT},fps={PROXY_FPS}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "30", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "1", "-b:a", "48k", "-movflags", "+faststart", str(out),
    ]
    try:
        subprocess.run(cmd, check=True, timeout=180, capture_output=True)
    except Exception as exc:  # noqa: BLE001
        print(f"gemini proxy failed ({str(exc)[:120]}); sending the original", flush=True)
        return path
    if not out.exists() or out.stat().st_size == 0:
        return path
    print(f"gemini proxy {path.stat().st_size // 1024}KB -> {out.stat().st_size // 1024}KB", flush=True)
    return out


def watch_video(path: Path, prompt: str, api_key: str, model: str = DEFAULT_MODEL, timeout: int = 180) -> str:
    """Send a low-res proxy of the mp4, with audio, and return Gemini's text.

    Uploaded files belong to the key's project, so on a quota error the proxy is
    re-uploaded under the next key.
    """
    keys = api_keys(api_key)
    if not keys:
        raise RuntimeError("no gemini api key")
    proxy = make_proxy(path)
    errors: list[str] = []
    try:
        for key in keys:
            try:
                uploaded = call_with_retry(lambda: upload_video(proxy, key, timeout=timeout), "gemini upload")
            except BudgetExhausted:
                raise
            except QuotaExhausted as exc:
                errors.append(str(exc))
                continue
            except ModelUnavailable as exc:
                raise RuntimeError(str(exc)) from exc
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
                except BudgetExhausted:
                    raise
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
    finally:
        if proxy != path:
            proxy.unlink(missing_ok=True)
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
