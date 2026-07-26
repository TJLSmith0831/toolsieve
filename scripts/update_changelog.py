#!/usr/bin/env python3
"""Append a merged PR to CHANGELOG.md's [Unreleased] section, grouped by
conventional-commit prefix (feat -> Added, fix -> Fixed, docs -> Documentation,
everything else -> Changed). Run by .github/workflows/changelog.yml.
"""
import argparse
import re
import sys
from pathlib import Path

CATEGORY_ORDER = ["Added", "Changed", "Fixed", "Documentation"]
CATEGORY_BY_PREFIX = {"feat": "Added", "fix": "Fixed", "docs": "Documentation"}
CHANGELOG = Path("CHANGELOG.md")


def categorize(title: str) -> str:
    prefix = title.split(":", 1)[0].split("(")[0].strip().lower()
    return CATEGORY_BY_PREFIX.get(prefix, "Changed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--title", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()

    text = CHANGELOG.read_text()
    match = re.search(r"## \[Unreleased\]\n(.*?)(?=\n## \[|\Z)", text, re.S)
    if not match:
        print("No [Unreleased] section found in CHANGELOG.md", file=sys.stderr)
        return 1

    sections = dict(re.findall(r"### (\w+)\n((?:- .*\n)*)", match.group(1)))
    bullet = f"- {args.title} ([#{args.pr}]({args.url}))\n"
    sections[categorize(args.title)] = sections.get(categorize(args.title), "") + bullet

    new_body = "\n" + "".join(
        f"### {c}\n{sections[c]}\n" for c in CATEGORY_ORDER if c in sections
    )
    CHANGELOG.write_text(text[: match.start(1)] + new_body + text[match.end(1) :])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
