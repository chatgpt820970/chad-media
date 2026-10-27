"""Download commands for public Google Drive and Dropbox share links.

Dry-run records the command and does not run it. No cookies are embedded.
"""

from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def dropbox_direct(url: str) -> str:
    """Force a Dropbox share link to download (dl=1)."""
    parts = urlsplit(url.strip())
    query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key.lower() != "dl"]
    query.append(("dl", "1"))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def download_command(source: dict, dest: str) -> list[str]:
    """Public-folder download. Drive uses gdown. Dropbox uses dl=1."""
    kind = source.get("kind") or ""
    url = str(source.get("url") or "")
    if kind == "google_drive_folder":
        return ["gdown", "--folder", "--remaining-ok", "-O", dest, url]
    if kind == "dropbox_folder":
        return ["curl", "-L", "--fail", "-o", dest, dropbox_direct(url)]
    raise ValueError(f"unsupported source kind {kind!r}")
