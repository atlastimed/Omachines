#!/usr/bin/env python3
"""Heuristic checks of tracked publication files; never print matched values."""
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
HOME_PATH = re.compile(r"(?:/home/|/Users/)([A-Za-z0-9_.-]+)/|[A-Za-z]:\\+Users\\+([A-Za-z0-9_.-]+)\\+", re.I)
EMAIL = re.compile(r"[A-Za-z0-9_.+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PLACEHOLDERS = {"example", "user", "username"}
PRIVATE_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".qcow2", ".vhd", ".vhdx", ".iso", ".tar", ".zip"}


def findings(path, text):
    problems = []
    env_file = path.name == ".env" or (path.name.startswith(".env.") and path.name != ".env.example")
    if path.suffix.lower() in PRIVATE_SUFFIXES or env_file or path.name in {"id_rsa", "id_ed25519"}:
        problems.append((0, "private artifact type"))
    for line, value in enumerate(text.splitlines(), 1):
        for match in HOME_PATH.finditer(value):
            name = next(group for group in match.groups() if group)
            if name.lower() not in PLACEHOLDERS:
                problems.append((line, "personal home path"))
        for match in EMAIL.finditer(value):
            domain = match.group(1).lower()
            if domain not in {"example.com", "example.org", "users.noreply.github.com"}:
                problems.append((line, "email address needs review"))
    return problems


def main():
    files = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).split(b"\0")
    count = 0
    failed = False
    for name in filter(None, files):
        relative = Path(name.decode())
        path = ROOT/relative
        count += 1
        if path.is_symlink() or not path.is_file():
            print(f"{relative}: symlink or missing publication file")
            failed = True
            continue
        try:
            text = path.read_text()
        except UnicodeError:
            print(f"{relative}: binary file requires manual review")
            failed = True
            continue
        for line, kind in findings(relative, text):
            print(f"{relative}:{line}: {kind}")
            failed = True
    print(f"Privacy heuristics checked {count} tracked files; {'review required' if failed else 'no findings'}.")
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
