"""Pre-publication audit: fail if anything private would ship with the public repo.

Scans every tracked file at HEAD **and every blob reachable from the history** (so a secret
committed once and deleted later is still caught). It fails on:

- API-key patterns (``sk-ant-…``, ``grn_…``), except the named fake keys the tests use;
- ``.env`` content: a committed ``.env`` file, or a non-empty ``*_API_KEY=`` assignment;
- paths under ``data/real`` (the author's real Granola sync; never published);
- email addresses other than the author's git email and the synthetic Northwind domains;
- tracked files over 5 MB.

Free and offline: it only reads the local git object store.

    uv run python scripts/prepublish_check.py          # history of HEAD
    uv run python scripts/prepublish_check.py --all    # every local branch and ref

Exit code 0 = clean, 1 = findings (each printed with path and, for blobs, the commit it
first appears in), 2 = not a git repo.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

MAX_BYTES = 5 * 1024 * 1024

KEY_PATTERNS = {
    "anthropic-key": re.compile(rb"sk-ant-[A-Za-z0-9_\-]{8,}"),
    "granola-key": re.compile(rb"grn_[A-Za-z0-9_\-]{8,}"),
}
# Deliberately fake keys used by the test-suite (they assert the key never leaks into logs).
ALLOWED_KEY_LITERALS = {b"grn_test_SECRET_do_not_log_1234"}

ENV_ASSIGNMENT = re.compile(
    rb"(?m)^[ \t]*(?:export[ \t]+)?[A-Z_]*API_KEY[ \t]*=[ \t]*['\"]?([^\s'\"#]+)"
)
# Values that are documentation, not secrets (e.g. `GRANOLA_API_KEY=grn_…` in prose).
ENV_PLACEHOLDERS = re.compile(rb"^(grn_\xe2\x80\xa6|sk-ant-\xe2\x80\xa6|\.\.\.|<.*>|\$\{?\w+\}?)$")

# The host must start with a letter: `system@30.batch`-style cache keys are not addresses.
EMAIL = re.compile(
    rb"[A-Za-z0-9._%+\-]+@([A-Za-z][A-Za-z0-9\-]*(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,})"
)
# Fictional organisations in the synthetic Northwind corpus (packages/tenor/synth/roster.py)
# and the test fixtures. They are made-up people at made-up or reserved domains.
SYNTHETIC_DOMAINS = {
    "northwind.dev",
    "acmeway.com",
    "acme.com",
    "beaconfreight.com",
    "corvidbank.com",
    "driftwoodretail.com",
    "harbor.vc",
    "lumenhealth.com",
    "orchardlabs.io",
    "pinecrest.co",
    "quarrybank.com",
    "redwoodmedia.com",
    "summitschools.org",
    "tidewaterfoods.com",
    "vantagelegal.com",
}
RESERVED_SUFFIXES = (".example", ".test", ".invalid", ".localhost")
RESERVED_DOMAINS = {"example.com", "example.org", "example.net", "example.edu"}
# Tool/bot addresses that appear in lockfiles or attribution lines, not personal data.
ALLOWED_EMAILS = {b"noreply@anthropic.com", b"noreply@github.com"}


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str
    detail: str


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout


def email_allowed(addr: bytes, domain: bytes, author: bytes) -> bool:
    d = domain.decode("ascii", "ignore").lower().rstrip(".")
    if addr.lower() == author.lower() or addr.lower() in ALLOWED_EMAILS:
        return True
    synthetic = d in SYNTHETIC_DOMAINS or any(d.endswith("." + s) for s in SYNTHETIC_DOMAINS)
    if synthetic or d in RESERVED_DOMAINS or d.endswith(RESERVED_SUFFIXES):
        return True
    # Anything ending in an image/file extension is a false positive like `logo@2x.png`.
    return d.rsplit(".", 1)[-1] in {"png", "jpg", "jpeg", "svg", "gif", "webp", "js", "css"}


def scan_content(data: bytes, where: str, author: bytes) -> list[Finding]:
    """Content checks shared by HEAD files and history blobs."""
    out: list[Finding] = []
    for kind, pat in KEY_PATTERNS.items():
        for m in pat.finditer(data):
            if m.group(0) not in ALLOWED_KEY_LITERALS:
                out.append(Finding(kind, where, m.group(0)[:12].decode() + "…"))
    for m in ENV_ASSIGNMENT.finditer(data):
        value = m.group(1)
        if not ENV_PLACEHOLDERS.match(value) and value not in ALLOWED_KEY_LITERALS:
            name = m.group(0).split(b"=")[0].strip().decode()
            out.append(Finding("env-assignment", where, f"{name}=<{len(value)} chars>"))
    for m in EMAIL.finditer(data):
        if not email_allowed(m.group(0), m.group(1), author):
            out.append(Finding("email", where, m.group(0).decode("utf-8", "replace")))
    return out


def scan_path(path: str) -> list[Finding]:
    out: list[Finding] = []
    if path == "data/real" or path.startswith(("data/real/", "data/real.")):
        out.append(Finding("real-data-path", path, "the author's real Granola data"))
    name = path.rsplit("/", 1)[-1]
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        out.append(Finding("env-file", path, "committed .env file"))
    return out


def head_files() -> list[tuple[str, int]]:
    """(path, size) for every file in the HEAD tree."""
    rows = git("ls-tree", "-r", "-l", "-z", "HEAD").split(b"\0")
    out = []
    for row in filter(None, rows):
        meta, path = row.split(b"\t", 1)
        size = meta.split()[3]
        out.append((path.decode(), int(size) if size.isdigit() else 0))
    return out


def history_blobs(revs: list[str]) -> dict[str, str]:
    """blob sha -> first path it was seen at, for every blob reachable from ``revs``."""
    seen: dict[str, str] = {}
    for line in git("rev-list", "--objects", *revs).decode().splitlines():
        sha, _, path = line.partition(" ")
        if path and sha not in seen:
            seen[sha] = path
    return seen


def read_blobs(shas: list[str]) -> dict[str, bytes]:
    """Read many objects in one `git cat-file --batch` call; non-blobs are dropped."""
    proc = subprocess.run(
        ["git", "cat-file", "--batch"],
        input="\n".join(shas).encode() + b"\n",
        capture_output=True,
        check=True,
    )
    buf, pos, out = proc.stdout, 0, {}
    while pos < len(buf):
        nl = buf.index(b"\n", pos)
        header = buf[pos:nl].split()
        pos = nl + 1
        if len(header) < 3:  # "<sha> missing"
            continue
        sha, typ, size = header[0].decode(), header[1], int(header[2])
        if typ == b"blob":
            out[sha] = buf[pos : pos + size]
        pos += size + 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--all", action="store_true", help="scan every local ref, not just HEAD")
    args = ap.parse_args()
    try:
        author = git("config", "user.email").strip()
    except subprocess.CalledProcessError:
        print("prepublish: not a git repository (or no user.email set)", file=sys.stderr)
        return 2

    findings: list[Finding] = []

    files = head_files()
    for path, size in files:
        findings += scan_path(path)
        if size > MAX_BYTES:
            findings.append(Finding("large-file", path, f"{size / 1e6:.1f} MB > 5 MB"))

    blobs = history_blobs(["--all"] if args.all else ["HEAD"])
    for path in set(blobs.values()):
        findings += [f for f in scan_path(path) if f not in findings]
    contents = read_blobs(list(blobs))
    for sha, data in contents.items():
        findings += scan_content(data, f"{blobs[sha]} (blob {sha[:8]})", author)

    # One line per distinct (kind, detail, path) so a value repeated across versions of the
    # same file is reported once.
    unique: dict[tuple[str, str, str], Finding] = {}
    for f in findings:
        key = (f.kind, f.detail, f.where.split(" (blob")[0])
        unique.setdefault(key, f)

    scope = "all refs" if args.all else "HEAD"
    print(
        f"prepublish: {len(files)} tracked files, {len(contents)} blobs in history of {scope}, "
        f"author email {author.decode()}"
    )
    if not unique:
        print("prepublish: OK, nothing to fix")
        return 0
    for f in sorted(unique.values(), key=lambda f: (f.kind, f.where)):
        print(f"  [{f.kind}] {f.where}: {f.detail}")
    print(f"prepublish: FAIL, {len(unique)} finding(s)")
    return 1


if __name__ == "__main__":
    sys.exit(main())
