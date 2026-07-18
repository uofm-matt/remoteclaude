#!/usr/bin/env python3
"""Per-file coverage floor: fail if any measured module sits below the threshold, so a weak
module can't hide behind the aggregate `coverage report --fail-under`. Reads coverage.json
(written by `coverage json`). Usage: coverage_floor.py [floor], default 90."""

import json
import sys
from pathlib import Path


def main() -> None:
    floor = float(sys.argv[1]) if len(sys.argv) > 1 else 90.0
    files = json.loads(Path("coverage.json").read_text())["files"]
    low = {
        f: v["summary"]["percent_covered"]
        for f, v in files.items()
        if v["summary"]["percent_covered"] < floor
    }
    for f, pct in sorted(low.items()):
        print(f"::error::{f} at {pct:.0f}% is below the {floor:.0f}% per-file floor")
    raise SystemExit(1 if low else 0)


if __name__ == "__main__":
    main()
