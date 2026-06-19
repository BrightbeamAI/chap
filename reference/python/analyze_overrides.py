"""
analyze-overrides (Python): the override-as-learning-data dividend.

Reads a workspace's audit log via audit.read and produces a tag
histogram so prompt-revision targets are concrete, not guessed.

Mirrors the output of reference/core-plus-review/analyze-overrides.ts.

Usage:

    python analyze_overrides.py wsp_support_triage
    python analyze_overrides.py --url http://my-coord/chap wsp_pr_reviews
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from urllib.request import Request, urlopen


def call(url: str, method: str, **params) -> dict:
    env = {
        "jsonrpc": "2.0",
        "id": f"analyse-{method}",
        "method": method,
        "params": params,
    }
    req = Request(url, data=json.dumps(env).encode("utf-8"),
                  headers={"Content-Type": "application/json"})
    with urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))


def bar(n: int, max_n: int, width: int = 20) -> str:
    if max_n == 0:
        return ""
    return "█" * max(1, int(width * n / max_n))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Aggregate override patterns from a CHAP audit log",
    )
    p.add_argument("workspace", help="Workspace id, e.g. wsp_support_triage")
    p.add_argument("--url", default="http://127.0.0.1:8080/chap")
    args = p.parse_args(argv)

    r = call(args.url, "audit.read", workspace=args.workspace)
    if "error" in r:
        print(f"audit.read failed: {r['error']}", file=sys.stderr)
        return 1
    entries = r["result"]["entries"]

    overrides = [
        e for e in entries
        if e["envelope"].get("method") == "decide.override"
    ]

    print(f"Override Learning Report")
    print("=" * 40)
    print(f"Workspace:       {args.workspace}")
    print(f"Total overrides: {len(overrides)}")
    print()
    if not overrides:
        print("No overrides recorded yet.")
        return 0

    # Tag histogram (an override may have multiple tags)
    tag_counter: Counter[str] = Counter()
    for o in overrides:
        for tag in o["envelope"].get("params", {}).get("tags") or []:
            tag_counter[tag] += 1

    max_count = max(tag_counter.values()) if tag_counter else 0
    print("By tag:")
    for tag, count in tag_counter.most_common():
        pct = round(100 * count / len(overrides))
        print(f"  {tag:35s}  {bar(count, max_count):20s}  {count:3d}  ({pct}%)")

    # Intent-preserved breakdown if any overrides carry that field
    refined = sum(
        1 for o in overrides
        if o["envelope"].get("params", {}).get("intent_preserved") is True
    )
    substituted = sum(
        1 for o in overrides
        if o["envelope"].get("params", {}).get("intent_preserved") is False
    )
    unknown = len(overrides) - refined - substituted
    if refined + substituted > 0:
        print()
        print("Intent breakdown:")
        print(f"  refining (same decision, better wording)   {refined}")
        print(f"  substituting (different decision)          {substituted}")
        if unknown:
            print(f"  unknown (intent_preserved not supplied)    {unknown}")

    # Top reviewers
    reviewers: Counter[str] = Counter()
    for o in overrides:
        rev = o["envelope"].get("params", {}).get("from")
        if rev:
            reviewers[rev] += 1
    if reviewers:
        print()
        print("Top reviewers:")
        for r_, n in reviewers.most_common(5):
            print(f"  {r_:40s}  {n}")

    print()
    print("Hint: the most common tags are your next prompt revision targets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
