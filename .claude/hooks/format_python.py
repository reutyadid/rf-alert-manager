"""PostToolUse hook: format and lint-fix a Python file right after Claude edits it.

Reads the hook payload from stdin, and if the edited file is a .py file, runs
`ruff format` and `ruff check --fix` on it. Never blocks Claude: any problem
(ruff not installed, non-Python file) exits 0 silently. Cross-platform (Windows/Linux).
"""

import json
import shutil
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0

    file_path = (payload.get("tool_input") or {}).get("file_path", "")
    if not file_path.endswith(".py"):
        return 0

    ruff = shutil.which("ruff")
    cmd = [ruff] if ruff else [sys.executable, "-m", "ruff"]

    for args in (["format", file_path], ["check", "--fix", "--quiet", file_path]):
        try:
            subprocess.run(cmd + args, capture_output=True, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
