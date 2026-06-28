#!/usr/bin/env python3
"""Validate the local allowlist config using the app's loader."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server import load_catalog  # noqa: E402


def main() -> int:
    try:
        catalog = load_catalog()
    except ValueError as error:
        print(f"Invalid config: {error}", file=sys.stderr)
        return 1

    print(
        f"OK: {catalog['videoCount']} videos across "
        f"{len(catalog['channels'])} channels from {catalog['configPath']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
