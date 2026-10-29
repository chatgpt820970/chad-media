"""Retry a flaky step a few times, then let the caller skip it."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class MinorFailure(RuntimeError):
    """A candidate-level problem. The run continues with the next video."""


def call_with_retry(action: Callable[[], T], attempts: int = 2) -> T:
    """Run `action` up to `attempts` times. The last error is raised."""
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    last: Exception | None = None
    for _ in range(attempts):
        try:
            return action()
        except Exception as exc:
            last = exc
    if last is None:
        raise MinorFailure("retry produced no result")
    raise last
