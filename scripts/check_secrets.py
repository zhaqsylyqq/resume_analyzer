"""INF-07: refuse a secrets file that is not encrypted by SOPS.

    python scripts/check_secrets.py deploy/envs/*/secrets*.yaml

A file passes when it carries SOPS metadata (`sops:` with an age recipient) and every value
under `data:` / `stringData:` is an `ENC[...]` blob. Plain `secrets.yaml` working copies must
never reach Git (they are in .gitignore; this check catches a forced add).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_VALUE = re.compile(r"^(\s+)([A-Za-z0-9_.-]+):\s*(.*)$")


def problems(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    found: list[str] = []
    if not path.name.endswith(".enc.yaml"):
        return [f"{path}: plain secrets file in the repository (encrypt it: scripts/secrets.sh encrypt <env>)"]
    if not re.search(r"^sops:\s*$", text, re.MULTILINE) or "age1" not in text:
        found.append(f"{path}: no SOPS metadata with an age recipient")
    section = None
    for number, line in enumerate(text.splitlines(), 1):
        if re.match(r"^(data|stringData):\s*$", line):
            section = line.split(":")[0]
            continue
        if section and line and not line.startswith(" "):
            section = None
        if section:
            match = _VALUE.match(line)
            if match and not match.group(3).strip().strip('"').startswith("ENC["):
                found.append(f"{path}:{number}: {section}.{match.group(2)} is not encrypted")
    return found


def main(paths: list[str]) -> int:
    issues = [p for name in paths if Path(name).is_file() for p in problems(Path(name))]
    for issue in issues:
        print(issue)
    checked = sum(1 for name in paths if Path(name).is_file())
    print(f"secrets check: {checked} file(s), {len(issues)} problem(s)")
    return 1 if issues else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
