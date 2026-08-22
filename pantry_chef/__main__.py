"""Entry point so `python -m pantry_chef` works."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
