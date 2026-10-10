"""The pages chap-analytics writes from this project's store.

    python3 analytics.py                  write analytics/ once
    python3 analytics.py --watch 300      write it again every five minutes
    python3 analytics.py --store data/chap.db --workspace wsp_x --out analytics

Reads the SQLite store the server writes (read-only, so a running server
is fine), and writes beside this file, under analytics/:

    report.html   the interactive report: every decision the chain makes
                  decidable, with filters, charts and briefs, in one file
    cases.jsonl   the evaluation cases: each corrected task with the agent's
                  output and the reviewer's, and each approved one
    refine.md     what to refine: the correction clusters ranked for
                  attention with example rationales, every rejection note,
                  and the briefs' headlines

The server serves them under /analytics/, and the desk's Insights view
links them. Needs Python 3.10 or later and chap-analytics 0.2.2 or later,
which reads the stores of both coordinators:

    pip install "chap-analytics>=0.2.2"
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

if sys.version_info < (3, 10):
    raise SystemExit("chap-analytics needs Python 3.10 or newer.")

try:
    import chap_analytics
    from chap_analytics import briefs, export, frames, from_json, report
except ModuleNotFoundError as exc:  # pragma: no cover - import guard
    if exc.name and exc.name.split(".")[0] in ("chap_analytics", "pandas", "numpy"):
        raise SystemExit('chap-analytics is not installed. Run: pip install "chap-analytics>=0.2.2"') from exc
    raise

if tuple(int(p) for p in re.findall(r"\d+", chap_analytics.__version__)[:3]) < (0, 2, 2):
    raise SystemExit(f'chap-analytics {chap_analytics.__version__} cannot read every store this project writes. Run: pip install --upgrade "chap-analytics>=0.2.2"')


def load_config() -> dict:
    path = HERE / "chap.config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def patch_sections(patch) -> dict[str, str]:
    """A unified diff split into its files: path -> that file's section."""
    out: dict[str, str] = {}
    if not isinstance(patch, str):
        return out
    current = None
    for line in patch.splitlines(keepends=True):
        if line.startswith("diff --git "):
            # With renames off both halves name the same path, so the line is
            # split in the middle, which holds for a path with " b/" in it.
            rest = line.rstrip("\n")[len("diff --git "):]
            n = (len(rest) - 5) // 2
            path = rest[2:2 + n]
            current = path if rest.startswith("a/") and rest[2 + n:] == f" b/{path}" else rest
            out[current] = ""
        if current is not None:
            out[current] += line
    return out


def code_sections(f) -> list[str]:
    """For code changes: the files reviewers edited, with their rationales."""
    o = f.overrides
    if o.empty:
        return []
    files: dict[str, list[str]] = {}
    for r in o.itertuples(index=False):
        before = (r.based_on or {}).get("patch") if isinstance(r.based_on, dict) else None
        after = (r.result or {}).get("patch") if isinstance(r.result, dict) else None
        if before is None or after is None:
            continue
        a, b = patch_sections(before), patch_sections(after)
        for path in sorted(set(a) | set(b)):
            if a.get(path) != b.get(path):
                files.setdefault(path, []).append(r.rationale if isinstance(r.rationale, str) and r.rationale else "no rationale")
    if not files:
        return []
    lines = ["## Files reviewers edited", "", "| File | Edits |", "|---|---|"]
    for path, notes in sorted(files.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"| `{path}` | {len(notes)} |")
    lines.append("")
    for path, notes in sorted(files.items(), key=lambda kv: -len(kv[1])):
        lines.append(f"**`{path}`:**")
        lines.append("")
        lines += [f"- {n}" for n in notes[:5]]
        lines.append("")
    return lines


def refine_markdown(f, workspace: str, cases_count: int) -> str:
    """The refinement page: what reviewers corrected most, in their words."""
    lines = [f"# {workspace}: what to refine", "",
             f"Written {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC from the store by chap-analytics. "
             f"{len(f.tasks)} tasks, {len(f.decisions)} decisions, {len(f.overrides)} overrides, {cases_count} evaluation cases.", ""]
    clusters = export.prompt_revision_candidates(f, top=10, examples=3)
    lines += ["## Correction clusters, ranked", ""]
    if clusters.empty:
        lines += ["No override yet. A reviewer's edit at the desk, with its rationale and a tag, is what builds this table.", ""]
    else:
        lines += ["| Task kind | Where in the artefact | Tag | Corrections | Reversing | Priority |", "|---|---|---|---|---|---|"]
        for r in clusters.itertuples(index=False):
            share = "n/a" if r.reversing_share != r.reversing_share else f"{r.reversing_share:.0%}"
            lines.append(f"| {r.task_kind} | `{r.top_path}` | {r.tag} | {r.n} | {share} | {r.priority:.1f} |")
        lines.append("")
        for r in clusters.itertuples(index=False):
            if not r.examples:
                continue
            lines.append(f"**{r.task_kind}, `{r.top_path}`, {r.tag}:**")
            lines.append("")
            for e in r.examples:
                lines.append(f"- {e}")
            lines.append("")
    lines += code_sections(f)
    rejections = f.decisions[f.decisions["kind"] == "reject"] if not f.decisions.empty else f.decisions
    lines += ["## What reviewers sent back", ""]
    if rejections is None or rejections.empty:
        lines += ["No rejection yet.", ""]
    else:
        for r in rejections.sort_values("ts", ascending=False).head(30).itertuples(index=False):
            how = "revision requested" if getattr(r, "request_revision", False) else "rejected"
            note = r.comment if isinstance(r.comment, str) and r.comment else "no note"
            tags = f" [{', '.join(r.tags)}]" if isinstance(r.tags, list) and r.tags else ""
            lines.append(f"- {r.task_id} ({r.task_kind}), {r.reviewer}, {how}: {note}{tags}")
        lines.append("")
    lines += ["## The briefs", ""]
    for b in briefs.everything(f):
        lines += [f"**{b.title}.** {b.headline}", "", f"{b.decision}", ""]
    lines += ["## Using this", "",
              "A correction that recurs is a rule for the agent: put it in the agent's instructions "
              "(AGENT_INSTRUCTIONS.md, or the prompt in agent.py or agent.mjs), and watch the cluster shrink "
              "on the next run. `cases.jsonl` holds each corrected task with the agent's output and the "
              "reviewer's, for an evaluation harness.", ""]
    return "\n".join(lines)


# The collections a workspace snapshot holds, and the field each is keyed by.
# The Python coordinator stores them as objects keyed by that field and the
# TypeScript coordinator as lists; chap-analytics 0.2.1 reads the first, so
# a list is keyed here before the snapshot is handed over.
KEYED = {"tasks": "id", "overrides": "id", "whispers": "id", "deliberations": "id",
         "handoffs": "id", "snapshots": "id", "route_decisions": "id", "members": "uri"}


def load_chain(store: str, workspace: str | None):
    """The workspace's chain and state from the store, whichever coordinator wrote it."""
    con = sqlite3.connect(f"file:{store}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT id, data FROM chap_workspaces").fetchall()
    finally:
        con.close()
    available = [r[0] for r in rows]
    if not rows:
        raise SystemExit(f"{store} holds no workspace yet.")
    if workspace is None:
        if len(rows) > 1:
            raise SystemExit(f"{store} holds {', '.join(available)}; name one with --workspace.")
        workspace = available[0]
    data = next((d for w, d in rows if w == workspace), None)
    if data is None:
        raise SystemExit(f"{workspace} is not in {store}, which holds {', '.join(available)}.")
    snapshot = json.loads(data)
    for name, key in KEYED.items():
        if isinstance(snapshot.get(name), list):
            snapshot[name] = {x[key]: x for x in snapshot[name] if isinstance(x, dict) and isinstance(x.get(key), str)}
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "snapshot.json"
        path.write_text(json.dumps(snapshot), encoding="utf-8")
        chain = from_json(str(path), workspace=workspace)
    chain.source = f"sqlite:{store}"
    return chain


def write_pages(store: str, workspace: str | None, out: Path, rationales: bool = True) -> dict:
    chain = load_chain(store, workspace)
    f = frames(chain)
    out.mkdir(parents=True, exist_ok=True)
    report.write(f, str(out / "report.html"), rationales=rationales)
    cases = export.evaluation_cases(f, include_approved=True)
    export.to_jsonl(cases, str(out / "cases.jsonl"))
    (out / "refine.md").write_text(refine_markdown(f, chain.workspace, len(cases)), encoding="utf-8")
    summary = {"written": datetime.now(timezone.utc).isoformat(), "workspace": chain.workspace, "tasks": int(len(f.tasks)),
               "decisions": int(len(f.decisions)), "overrides": int(len(f.overrides)), "cases": int(len(cases))}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return summary


def main(argv: list[str] | None = None) -> int:
    config = load_config()
    parser = argparse.ArgumentParser(description="Write the chap-analytics pages for this project's store.")
    parser.add_argument("--store", default=os.environ.get("CHAP_DB_PATH") or config.get("store") or "./data/chap.db")
    parser.add_argument("--workspace", default=config.get("workspace"))
    parser.add_argument("--out", default=str(HERE / "analytics"))
    parser.add_argument("--watch", type=int, metavar="SECONDS", help="write the pages again every so many seconds")
    parser.add_argument("--no-rationales", action="store_true", help="leave the reviewers' rationales out of the report")
    args = parser.parse_args(argv)
    store = args.store
    if store == ":memory:":
        raise SystemExit("The server runs with an in-memory store, so there is nothing to read. Start it with CHAP_DB_PATH set to a file.")
    if not Path(store).is_absolute():
        store = str((HERE / store).resolve())
    if not Path(store).exists():
        raise SystemExit(f"No store at {store}. Start the server once, or pass --store.")
    while True:
        s = write_pages(store, args.workspace, Path(args.out), rationales=not args.no_rationales)
        print(f"{s['written'][:19]} wrote {args.out}/report.html, cases.jsonl and refine.md: "
              f"{s['tasks']} tasks, {s['decisions']} decisions, {s['overrides']} overrides, {s['cases']} cases")
        sys.stdout.flush()
        if not args.watch:
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
