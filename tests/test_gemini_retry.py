import io
import json
import urllib.error

import pytest

from growth.utils import gemini


def _http(code, body):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(json.dumps(body).encode()))


def _ok(text="hi"):
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}


def test_retries_503_then_succeeds(monkeypatch):
    calls = []

    def fake(url, body, key, timeout):
        calls.append(key)
        if len(calls) < 3:
            raise _http(503, {"error": {"status": "UNAVAILABLE"}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    assert gemini.generate("p", "k1", "m") == "hi"
    assert len(calls) == 3


def test_retry_delay_is_honoured(monkeypatch):
    waits = []
    monkeypatch.setattr(gemini, "_sleep", waits.append)
    state = {"n": 0}

    def fake(url, body, key, timeout):
        state["n"] += 1
        if state["n"] == 1:
            raise _http(429, {"error": {"message": "slow down", "details": [{"retryDelay": "17s"}]}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    gemini.generate("p", "k1", "m")
    assert waits == [17.0]


def test_rotates_key_on_quota(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY_2", "k2")
    used = []

    def fake(url, body, key, timeout):
        used.append(key)
        if key == "k1":
            raise _http(429, {"error": {"message": "You exceeded your current quota", "details": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}})
        return _ok("from k2")

    monkeypatch.setattr(gemini, "_post", fake)
    assert gemini.generate("p", "k1", "m") == "from k2"
    assert used == ["k1", "k2"]


def test_falls_back_to_next_model_on_404(monkeypatch):
    urls = []

    def fake(url, body, key, timeout):
        urls.append(url)
        if "retired" in url:
            raise _http(404, {"error": {"message": "no longer available"}})
        return _ok()

    monkeypatch.setattr(gemini, "_post", fake)
    gemini.generate("p", "k1", "retired-model")
    assert "retired-model" in urls[0] and gemini.FALLBACK_MODELS[0] in urls[1]


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
