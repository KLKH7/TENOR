"""Fail if any relative Markdown link in a tracked .md file points at a path that doesn't exist."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r"\]\(([^)\s]+)\)")


def broken_links() -> list[str]:
    files = subprocess.run(
        ["git", "ls-files", "*.md"], capture_output=True, text=True
    ).stdout.split()
    bad = []
    for f in files:
        if f.startswith(("data/", "evals/reports/")):
            continue
        text = re.sub(r"```.*?```", "", Path(f).read_text(), flags=re.S)  # skip code blocks
        for target in LINK.findall(text):
            path = target.split("#", 1)[0]
            if not path or re.match(r"^[a-z]+:", path) or path.startswith(("/", "<")):
                continue
            if not (Path(f).parent / path).exists():
                bad.append(f"{f}: {target}")
    return bad


if __name__ == "__main__":
    problems = broken_links()
    print("\n".join(problems) or "links: all relative links resolve")
    sys.exit(1 if problems else 0)
