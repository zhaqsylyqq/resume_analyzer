"""SEC-04: turn the ZAP JSON report into a short Markdown table for the GitHub job summary.

    python ci/dast_summary.py zap-wrk/zap-report.json .zap/rules.tsv >> "$GITHUB_STEP_SUMMARY"

One row per finding: what ZAP calls it, its risk, what rules.tsv does with it and where it was
seen first. The full HTML report is kept as a build artifact.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

RISK_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Informational": 3}


def rules(path: Path) -> dict[str, str]:
    actions = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            rule_id, action, *_ = line.split("\t")
            actions[rule_id] = action
    return actions


def main(report: Path, rules_file: Path) -> int:
    actions = rules(rules_file)
    alerts = [a for site in json.loads(report.read_text(encoding="utf-8")).get("site", []) for a in site.get("alerts", [])]
    alerts.sort(key=lambda a: (RISK_ORDER.get(a.get("riskdesc", "").split(" ")[0], 9), a.get("pluginid", "")))
    print("### OWASP ZAP baseline (SEC-04)\n")
    if not alerts:
        print("No findings.")
        return 0
    print("| Rule | Finding | Risk | rules.tsv | Count | First seen |")
    print("|---|---|---|---|---|---|")
    for a in alerts:
        first = (a.get("instances") or [{}])[0]
        where = f"{first.get('method', '')} {first.get('uri', '')}".strip()
        print(f"| {a.get('pluginid')} | {a.get('alert')} | {a.get('riskdesc')} | "
              f"{actions.get(str(a.get('pluginid')), 'WARN')} | {a.get('count')} | `{where}` |")
    failing = [a for a in alerts if actions.get(str(a.get("pluginid"))) == "FAIL"]
    print(f"\n{len(alerts)} findings, {len(failing)} on FAIL rules.")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
