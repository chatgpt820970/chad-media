import io
import json
import urllib.error

import pytest

from growth.utils import gemini


def _http(code, body):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps(body).encode()))


def _ok(text="hi"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_503_retries_once_then_next_model(monkeypatch):
    urls = []

    def fake(url, body, key, timeout):
        urls.append(url)
        if "flash-latest:" in url:
            raise _http(503, {"error": {"status": "UNAVAILABLE"}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    assert gemini.generate("p", "k1") == "hi"
    assert len(urls) == 3  # flash-latest twice, then flash-lite-latest
    assert "gemini-flash-lite-latest" in urls[2]


def test_per_minute_429_waits_retry_delay_once(monkeypatch):
    waits = []
    monkeypatch.setattr(gemini, "_sleep", waits.append)
    state = {"n": 0}

    def fake(url, body, key, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise _http(429, {"error": {"message": "quota", "details": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}, {"retryDelay": "17s"}]}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    gemini.generate("p", "k1")
    assert waits == [17.0]


def test_per_day_429_switches_key_without_retry(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_2", "k2")
    waits = []
    monkeypatch.setattr(gemini, "_sleep", waits.append)
    used = []

    def fake(url, body, key, timeout):
        used.append(key)
        if key == "k1":
            raise _http(429, {"error": {"message": "quota", "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
        return _ok("from k2")

    monkeypatch.setattr(gemini, "_post", fake)
    assert gemini.generate("p", "k1") == "from k2"
    assert used == ["k1", "k2"] and waits == []


def test_unclear_429_switches_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_2", "k2")
    used = []

    def fake(url, body, key, timeout):
        used.append(key)
        if key == "k1":
            raise _http(429, {"error": {"message": "Resource exhausted"}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    gemini.generate("p", "k1")
    assert used == ["k1", "k2"]


def test_budget_counts_every_request(monkeypatch):
    gemini.set_budget(3)

    def fake(url, body, key, timeout):
        raise _http(503, {})

    monkeypatch.setattr(gemini, "_post", fake)
    with pytest.raises(gemini.BudgetExhausted):
        gemini.generate("p", "k1")
    assert gemini.requests_used() == 3


def test_falls_back_to_next_model_on_404(monkeypatch):
    urls = []

    def fake(url, body, key, timeout):
        urls.append(url)
        if "retired" in url:
            raise _http(404, {"error": {"message": "no longer available"}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    gemini.FALLBACK_MODELS = ("retired-model", "good-model")
    try:
        gemini.generate("p", "k1")
    finally:
        gemini.FALLBACK_MODELS = ("gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.8-flash")
    assert "retired-model" in urls[0] and "good-model" in urls[1]


def test_all_keys_out_of_quota_raises(monkeypatch):
    def fake(url, body, key, timeout):
        raise _http(429, {"error": {"message": "quota", "details": [{"quotaId": "PerDay"}]}})

    monkeypatch.setattr(gemini, "_post", fake)
    with pytest.raises(gemini.QuotaExhausted):
        gemini.generate("p", "k1", "m")


def test_api_keys_dedupes(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "a")
    monkeypatch.setenv("GEMINI_API_KEY_2", "a")
    monkeypatch.setenv("GEMINI_API_KEY_3", "b")
    assert gemini.api_keys() == ["a", "b"]


def test_proxy_is_low_res(tmp_path):
    import json as _j
    import shutil
    import subprocess

    if not shutil.which("ffmpeg"):
        pytest.skip("no ffmpeg")
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1080x1920:rate=30:duration=3",
                    "-f", "lavfi", "-i", "sine=duration=3", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(src)], check=True)
    out = gemini.make_proxy(src)
    assert out != src
    info = _j.loads(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=height,r_frame_rate",
                                    "-of", "json", str(out)], capture_output=True, text=True).stdout)["streams"][0]
    assert info["height"] == 360 and info["r_frame_rate"] == "15/1"
