"""python -m growth.instagram check|run"""

from __future__ import annotations

from growth.utils.cli import main
from growth.instagram.bot import HOOKS

if __name__ == "__main__":
    raise SystemExit(main(HOOKS))
