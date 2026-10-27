"""Pre-post guard for community-guideline and spam risks.

Romantic, movie, anime, and sports wording is allowed. Patterns name categories
without listing slurs.
"""

from __future__ import annotations

import re

DEFAULT_PATTERN = (
    r"(?i)\b(nude|nudes|naked|nsfw|porn|xxx|onlyfans|only\s*fans|"
    r"hentai|explicit|sexual|sex\s*tape)\b"
)

CATEGORY_PATTERNS = {
    "sexual": DEFAULT_PATTERN,
    "gore": r"(?i)\b(gore|gory|beheading|dismember(?:ment)?|snuff)\b",
    "hate": r"(?i)\b(hate crime|white power|kill all)\b",
    "dangerous": r"(?i)\b(blackout challenge|tide pod|choking challenge)\b",
    "misleading": r"(?i)\b(guaranteed profit|double your money|risk[- ]free crypto|get rich quick)\b",
}


def compile_pattern(pattern: str | None = None) -> re.Pattern[str]:
    return re.compile(pattern or DEFAULT_PATTERN)


def violation(text: str, pattern: re.Pattern[str] | None = None) -> str | None:
    if not text:
        return None
    found = (pattern or compile_pattern()).search(text)
    if not found:
        return None
    return found.group(0)


def classify(text: str) -> str | None:
    """Return the category name when text should not be posted."""
    if not text:
        return None
    for name, pattern in CATEGORY_PATTERNS.items():
        if re.search(pattern, text):
            return name
    return None
