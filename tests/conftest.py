import pytest


@pytest.fixture(autouse=True)
def _no_real_gemini(monkeypatch):
    """Tests must never call the real Gemini API or sleep on backoff."""
    for name in ("GEMINI_API_KEY", "GEMINI_API_KEY_2", "GEMINI_API_KEY_3"):
        monkeypatch.delenv(name, raising=False)
    from growth.utils import gemini

    monkeypatch.setattr(gemini, "_sleep", lambda seconds: None)
