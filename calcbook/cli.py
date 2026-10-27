"""Command line interface.

    calcbook init                     create project.yaml + design_basis.yaml
    calcbook ingest B --note "..."    snapshot all sub-packages as Issue B
    calcbook issues                   list issues
    calcbook compile [--issue B]      build the bookmarked calculation book
    calcbook check   [--issue B]      design-intent cross-check
    calcbook diff A B                 what changed between two issues
    calcbook report  [--issue B]      HTML report (findings + changes + register)
    calcbook history GRV/GRV-02...    revision history of one document
    calcbook serve --workspace DIR    web app for many projects (upload, drawings tracker)
"""
from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

from .checks import SEV_ORDER, counts, run_checks, write_findings
from .compile import compile_issue
from .config import load_project
from .diff import compare
from .history import Workspace, compress_ranges
from .ingest import ingest
from .report import render_report
from .rules import load_rules
from .store import Store

PROJECT_TEMPLATE = """\
# calcbook project manifest
project:
  name: "New Building"
  number: "2026-000"
  client: ""
  engineer_of_record: ""

# Sub-packages, in the order they appear in the compiled book.
# Each `path` is a folder of PDF calcs; sub-folders are allowed.
packages:
  - id: GRV
    title: Gravity Framing
    path: packages/gravity
    responsible: ""
  - id: LAT
    title: Lateral System
    path: packages/lateral
  - id: FDN
    title: Foundations
    path: packages/foundations

basis: design_basis.yaml
# rules: rules.yaml        # optional: extra / overridden extraction rules
output: output
"""

BASIS_TEMPLATE = """\
# Design basis — the single statement of design intent every calc is checked against.
# check: equals (default) | at_least | at_most      packages: [IDs] limits scope
codes:
  - CBC 2025
  - ASCE 7-22
  - ACI 318-19
  - AISC 360-22

parameters:
  risk_category: II
  site_class: D
  sdc: D
  sds: {value: 1.20, tolerance: 0.005}
  sd1: {value: 0.75, tolerance: 0.005}
  ie: 1.0
  wind_speed: 95 mph
  exposure: C
  floor_live_load: {value: 100 psf, check: at_least}
  fc: {value: 4000 psi, check: at_least, note: "minimum for all structural concrete"}
  rebar_fy: 60 ksi
  steel_fy: 50 ksi
  soil_bearing: {value: 4000 psf, check: at_most, packages: [FDN], note: "geotech report"}

dcr_limit: 1.0
dcr_warning: 0.95
"""


def _ws(args) -> Workspace:
    project = load_project(args.project)
    return Workspace(project, Store(project.root), load_rules(project.rules_path))


def cmd_init(args) -> int:
    root = Path(args.dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for name, body in (("project.yaml", PROJECT_TEMPLATE), ("design_basis.yaml", BASIS_TEMPLATE)):
        p = root / name
        if p.exists():
            print(f"  exists  {p}")
        else:
            p.write_text(body)
            print(f"  created {p}")
    for sub in ("gravity", "lateral", "foundations"):
        (root / "packages" / sub).mkdir(parents=True, exist_ok=True)
    print("Edit project.yaml and design_basis.yaml, drop PDFs into packages/*, then run "
          "`calcbook ingest A`.")
    return 0


def cmd_ingest(args) -> int:
    ws = _ws(args)
    res = ingest(ws.project, ws.store, args.label, args.note or "", args.date, args.replace)
    print(f"Issue {res.issue.label}: {res.documents} documents, {res.pages} pages archived.")
    for w in res.warnings:
        print(f"  ! {w}")
    prev = ws.store.previous(res.issue)
    if prev:
        s = compare(ws, prev, res.issue, with_text=False).summary
        print(f"  vs Issue {prev.label}: {s['new']} new, {s['revised']} revised, "
              f"{s['removed']} removed, {s['unchanged']} unchanged")
    return 0


def cmd_issues(args) -> int:
    ws = _ws(args)
    issues = ws.store.issues()
    if not issues:
        print("No issues yet. Run `calcbook ingest <label>`.")
    for i in issues:
        docs = ws.store.documents(i)
        print(f"{i.label:>6}  {i.issued_on}  {len(docs):3d} docs  {sum(d.n_pages for d in docs):4d} pp  {i.note}")
    return 0


def cmd_compile(args) -> int:
    ws = _ws(args)
    issue = ws.store.resolve(args.issue)
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in ws.project.number or "calcs")
    out = Path(args.out) if args.out else ws.project.output_dir / f"{safe}_Calculations_Issue-{issue.label}.pdf"
    info = compile_issue(ws, issue, out, mark_changes=not args.no_deltas)
    print(f"Compiled {info['documents']} documents into {info['sheets']} sheets "
          f"({info['front_matter']} front matter, {info['revised_sheets']} sheets tagged as changed).")
    print(f"  -> {info['path']}")
    return 0


def _print_findings(findings) -> None:
    width = 100
    for f in findings:
        head = f"{f.id}  {f.severity.upper():7}  {f.title}"
        print(head)
        print(textwrap.fill(f.message, width, initial_indent="        ", subsequent_indent="        "))
        print(textwrap.fill(f"at: {f.where}", width, initial_indent="        ", subsequent_indent="            "))
        if f.evidence:
            print(textwrap.shorten(f'        "{f.evidence}"', width + 8))
        print()


def cmd_check(args) -> int:
    ws = _ws(args)
    issue = ws.store.resolve(args.issue)
    findings, _ = run_checks(ws, issue)
    shown = [f for f in findings if SEV_ORDER[f.severity] <= SEV_ORDER[args.min_severity]]
    _print_findings(shown)
    c = counts(findings)
    print(f"Issue {issue.label}: {c['error']} errors, {c['warning']} warnings, {c['info']} notes")
    out = ws.project.output_dir
    write_findings(findings, Path(args.json) if args.json else out / f"findings_{issue.label}.json",
                   Path(args.csv) if args.csv else out / f"findings_{issue.label}.csv")
    print(f"  -> {out / f'findings_{issue.label}.csv'}" if not args.csv else f"  -> {args.csv}")
    if args.fail_on == "never":
        return 0
    limit = SEV_ORDER[args.fail_on]
    return 1 if any(SEV_ORDER[f.severity] <= limit for f in findings) else 0


def cmd_diff(args) -> int:
    ws = _ws(args)
    old, new = ws.store.resolve(args.old), ws.store.resolve(args.new)
    d = compare(ws, old, new)
    s = d.summary
    print(f"Issue {old.label} -> {new.label}: {s['new']} new, {s['revised']} revised, "
          f"{s['removed']} removed, {s['unchanged']} unchanged\n")
    for label, a, b in d.basis_changes:
        print(f"  BASIS    {label}: {a} -> {b}")
    if d.basis_changes:
        print()
    for c in d.changes:
        if c.status == "unchanged":
            continue
        pages = compress_ranges(c.changed_pages)
        print(f"  {c.status.upper():8} {c.doc_key}" + (f"  (pages {pages})" if pages else ""))
        for label, a, b in c.param_changes:
            print(f"           {label}: {a} -> {b}")
        for m, a, b in c.member_changes:
            print(f"           {m}: {a} -> {b}")
        a, b = c.max_dcr
        if a != b and c.status == "revised":
            print(f"           max D/C: {'—' if a is None else f'{a:.2f}'} -> {'—' if b is None else f'{b:.2f}'}")
    if args.html:
        findings, basis = run_checks(ws, new)
        p = render_report(ws, new, findings, basis, d, Path(args.html))
        print(f"\n  -> {p}")
    return 0


def cmd_report(args) -> int:
    ws = _ws(args)
    issue = ws.store.resolve(args.issue)
    against = ws.store.resolve(args.against) if args.against else ws.store.previous(issue)
    findings, basis = run_checks(ws, issue)
    d = compare(ws, against, issue) if against else None
    out = Path(args.out) if args.out else ws.project.output_dir / f"report_Issue-{issue.label}.html"
    render_report(ws, issue, findings, basis, d, out)
    c = counts(findings)
    print(f"Issue {issue.label}: {c['error']} errors, {c['warning']} warnings, {c['info']} notes")
    print(f"  -> {out}")
    return 0


def cmd_history(args) -> int:
    ws = _ws(args)
    prev_sha = None
    found = False
    for iss, doc in ws.store.history(args.doc_key):
        if doc is None:
            state = "removed" if prev_sha else "—"
            prev_sha = None
        else:
            found = True
            state = "new" if prev_sha is None else ("revised" if doc.sha256 != prev_sha else "unchanged")
            prev_sha = doc.sha256
        print(f"{iss.label:>6}  {iss.issued_on}  {state:9}  "
              + (f"{doc.n_pages} pp  {doc.rel_path}  sha {doc.sha256[:10]}" if doc else ""))
    if not found:
        keys = sorted({d.doc_key for i in ws.store.issues() for d in ws.store.documents(i)})
        print(f"No document '{args.doc_key}'. Known keys:\n  " + "\n  ".join(keys))
        return 1
    return 0


def cmd_serve(args) -> int:
    from .web import create_app
    app = create_app(args.workspace)
    print(f"calcbook running at http://{args.host}:{args.port}  (workspace: {Path(args.workspace).resolve()})")
    app.run(host=args.host, port=args.port, debug=False, threaded=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("-p", "--project", default="project.yaml", help="path to project.yaml")
    ap = argparse.ArgumentParser(prog="calcbook", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project skeleton")
    s.add_argument("dir", nargs="?", default=".")
    s.set_defaults(fn=cmd_init)

    s = sub.add_parser("ingest", parents=[common], help="snapshot all sub-packages as a new issue")
    s.add_argument("label", help="issue / revision label, e.g. A, B, 1, 50DD")
    s.add_argument("--note", help="issue description, e.g. 'Permit submittal'")
    s.add_argument("--date", help="issue date (YYYY-MM-DD), default today")
    s.add_argument("--replace", action="store_true", help="overwrite an existing issue with this label")
    s.set_defaults(fn=cmd_ingest)

    s = sub.add_parser("issues", parents=[common], help="list issues")
    s.set_defaults(fn=cmd_issues)

    s = sub.add_parser("compile", parents=[common], help="compile the calculation book PDF")
    s.add_argument("--issue", help="issue label (default: latest)")
    s.add_argument("--out", help="output PDF path")
    s.add_argument("--no-deltas", action="store_true", help="don't tag changed sheets")
    s.set_defaults(fn=cmd_compile)

    s = sub.add_parser("check", parents=[common], help="cross-check documents against design intent")
    s.add_argument("--issue", help="issue label (default: latest)")
    s.add_argument("--min-severity", choices=["error", "warning", "info"], default="info",
                   help="lowest severity to print")
    s.add_argument("--fail-on", choices=["error", "warning", "info", "never"], default="error",
                   help="exit code 1 if findings at or above this severity (for CI)")
    s.add_argument("--json")
    s.add_argument("--csv")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("diff", parents=[common], help="compare two issues")
    s.add_argument("old")
    s.add_argument("new")
    s.add_argument("--html", help="also write an HTML report to this path")
    s.set_defaults(fn=cmd_diff)

    s = sub.add_parser("report", parents=[common], help="write the HTML review report")
    s.add_argument("--issue", help="issue label (default: latest)")
    s.add_argument("--against", help="issue to compare with (default: previous)")
    s.add_argument("--out")
    s.set_defaults(fn=cmd_report)

    s = sub.add_parser("history", parents=[common], help="revision history of one document")
    s.add_argument("doc_key", help="e.g. GRV/GRV-02_Floor_Beams")
    s.set_defaults(fn=cmd_history)
    s = sub.add_parser("serve", help="run the web app for all projects in a workspace folder")
    s.add_argument("--workspace", default="projects", help="folder holding one sub-folder per project")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(fn=cmd_serve)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (FileNotFoundError, LookupError, ValueError) as e:
        print(f"calcbook: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
