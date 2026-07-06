"""Verify desktop/payload/env.lock still agrees with the four apps' requirement pins.

The apps pin exact versions (pkg==x.y.z). env.lock additionally freezes transitive deps at
whatever resolved when it was generated - that part legitimately drifts and is only refreshed
when a developer re-locks. What must NEVER drift silently is a direct pin: if any
apps/*/requirements.txt pin differs from env.lock, the desktop payload would ship different
versions than the web apps run. This check is deterministic (no network, no resolution).

Exit 0 = consistent; exit 1 with a report otherwise.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REQUIREMENT_FILES = [
    "apps/easi/requirements.txt",
    "apps/sfari/requirements.txt",
    "apps/deep/requirements.txt",
    "apps/stream-curves/requirements.txt",
]

PIN_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;#]+)")


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def parse_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = PIN_RE.match(line)
        if match:
            pins[canonical(match.group(1))] = match.group(2)
    return pins


def main() -> int:
    repo = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    lock = parse_pins(repo / "desktop/payload/env.lock")
    if not lock:
        print("FAIL: desktop/payload/env.lock has no pins?")
        return 1

    problems: list[str] = []
    for rel in REQUIREMENT_FILES:
        for name, version in parse_pins(repo / rel).items():
            locked = lock.get(name)
            if locked is None:
                problems.append(f"{rel}: {name}=={version} is missing from env.lock")
            elif locked != version:
                problems.append(f"{rel}: {name}=={version} but env.lock has {locked}")

    if problems:
        print("env.lock is OUT OF SYNC with the app requirement pins:")
        for problem in problems:
            print("  -", problem)
        print("\nRegenerate and commit it:")
        print("  uv pip compile apps/easi/requirements.txt apps/sfari/requirements.txt "
              "apps/deep/requirements.txt apps/stream-curves/requirements.txt "
              "--python-version 3.12 --python-platform windows --no-header -o desktop/payload/env.lock")
        return 1

    print(f"env.lock consistent with all direct pins ({len(lock)} locked packages)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
