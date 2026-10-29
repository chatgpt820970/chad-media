"""python -m growth.youtube check|run"""

from __future__ import annotations

from growth.utils.cli import main
from growth.youtube.bot import HOOKS

if __name__ == "__main__":
    raise SystemExit(main(HOOKS))
