#!/usr/bin/env python3
"""진입점: python studiohub.py <명령> … (python -m studiohub 와 같다)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from studiohub.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
