"""Convenience launcher: `python run.py` (optionally with --no-elevate)."""
from app.main import main

if __name__ == "__main__":
    raise SystemExit(main())
