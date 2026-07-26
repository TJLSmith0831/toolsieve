#!/usr/bin/env python3
"""Print the CHANGELOG.md section for a given version (the text between its
`## [x.y.z]` heading and the next one). Used by .github/workflows/release.yml
as the GitHub Release body.
"""
import argparse
import re
import sys
from pathlib import Path

CHANGELOG = Path("CHANGELOG.md")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    text = CHANGELOG.read_text()
    pattern = rf"## \[{re.escape(args.version)}\][^\n]*\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, text, re.S)
    if not match:
        print(f"No CHANGELOG.md section for {args.version}", file=sys.stderr)
        return 1

    print(match.group(1).strip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
