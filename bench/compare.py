"""Compare two benchmark result files (micro or load) and fail on regressions.

uv run python -m bench.compare BASE.json HEAD.json [--threshold 0.10]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

KEYS = {"micro": ("case",), "load": ("config", "scenario")}
# metric -> which direction is better
METRICS = {
    "micro": {"p95_us": "lower", "ops_per_s": "higher"},
    "load": {"p95_ms": "lower", "rps": "higher", "db_conn_peak": "lower"},
}


def index(payload: dict[str, Any]) -> dict[tuple[str, ...], dict[str, Any]]:
    keys = KEYS[payload["kind"]]
    return {tuple(r[k] for k in keys): r for r in payload["results"] if r.get("status") == "ok"}


def compare(base: dict[str, Any], head: dict[str, Any], threshold: float) -> tuple[list[str], bool]:
    if base["kind"] != head["kind"]:
        raise SystemExit("cannot compare micro and load results")
    metrics = METRICS[base["kind"]]
    base_rows, head_rows = index(base), index(head)
    lines = ["| key | metric | base | head | delta | |", "|---|---|---|---|---|---|"]
    regressed = False
    for key in sorted(base_rows.keys() & head_rows.keys()):
        for metric, better in metrics.items():
            b, h = base_rows[key][metric], head_rows[key][metric]
            delta = (h - b) / b if b else 0.0
            worse = delta > threshold if better == "lower" else delta < -threshold
            regressed |= worse
            flag = "REGRESSION" if worse else ""
            lines.append(
                f"| {'/'.join(key)} | {metric} | {b:.2f} | {h:.2f} | {delta:+.1%} | {flag} |"
            )
    return lines, regressed


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("base", type=Path)
    parser.add_argument("head", type=Path)
    parser.add_argument("--threshold", type=float, default=0.10)
    args = parser.parse_args()
    lines, regressed = compare(
        json.loads(args.base.read_text()), json.loads(args.head.read_text()), args.threshold
    )
    print("\n".join(lines))
    if regressed:
        print(f"\nregression beyond {args.threshold:.0%}", file=sys.stderr)
    return 1 if regressed else 0


if __name__ == "__main__":
    raise SystemExit(main())
