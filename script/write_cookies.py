"""Write Netscape cookie secrets to temp files for yt-dlp.

The secret text is masked in the Actions log and is never printed back.
A missing secret is skipped so the fetch can use a cookie-free source.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

PAIRS = (
    ("YT_COOKIES", "yt_cookies.txt", "YT_COOKIES_FILE"),
    ("X_COOKIES", "x_cookies.txt", "X_COOKIES_FILE"),
    ("INSTAGRAM_COOKIES", "ig_cookies.txt", "INSTAGRAM_COOKIES_FILE"),
    ("TIKTOK_COOKIES", "tt_cookies.txt", "TIKTOK_COOKIES_FILE"),
)


def write_cookies(env: dict[str, str], directory: Path, env_file: Path | None) -> list[str]:
    """Return the env names that were written. Mask each secret line."""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    lines: list[str] = []
    for secret, filename, env_name in PAIRS:
        value = env.get(secret) or ""
        if not value.strip():
            print(f"{secret} missing")
            continue
        for line in value.splitlines():
            if line:
                sys.stdout.write(f"::add-mask::{line}\n")
        dest = directory / filename
        dest.write_text(value if value.endswith("\n") else value + "\n", encoding="utf-8")
        dest.chmod(0o600)
        lines.append(f"{env_name}={dest}")
        written.append(env_name)
        print(f"{secret} written")
    if env_file is not None and lines:
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    return written


def main() -> int:
    directory = Path(os.environ.get("RUNNER_TEMP") or tempfile.gettempdir())
    env_file = Path(os.environ["GITHUB_ENV"]) if os.environ.get("GITHUB_ENV") else None
    write_cookies(dict(os.environ), directory, env_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
