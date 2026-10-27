"""Local web app: several projects, document upload, issues, design-intent
findings, revision changes and a visual change tracker for drawings.

    calcbook serve --workspace ./projects        # then open http://127.0.0.1:8000
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file,
                   url_for)

from .. import drawings as dwg
from ..checks import counts, run_checks, write_findings
from ..compile import compile_issue
from ..config import load_project, parse_basis
from ..demo import build as build_demo
from ..diff import compare
from ..history import compress_ranges, register
from ..ingest import ingest
from ..projects import Home, delete_working_file, save_upload, working_files
from ..units import fmt

STATUS_ORDER = {"changed": 0, "new": 1, "removed": 2, "unchanged": 3}


def create_app(workspace: str | Path) -> Flask:
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "calcbook-local"
    app.config["MAX_CONTENT_LENGTH"] = 500 * 1024 * 1024
    home = Home(Path(workspace))
    app.config["HOME"] = home

    @app.context_processor
    def globals_():
        return {"all_projects": [(e.slug, _project_name(e)) for e in home.list()],
                "ranges": compress_ranges, "fmt": fmt,
                "region_label": dwg.region_label}

    def _project_name(e) -> str:
        try:
            return load_project(e.yaml).name
        except Exception:
            return e.slug

    # ------------------------------------------------------------ helpers
    def ws_for(slug: str):
        try:
            ws = home.open(slug)
        except (LookupError, FileNotFoundError):
            abort(404)
        g.slug = slug
        g.ws = ws
        return ws

    def pick_issue(ws, param="issue"):
        label = request.args.get(param)
        iss = ws.store.issue(label) if label else ws.store.latest()
        return iss

    def pair(ws):
        new = pick_issue(ws, "new")
        old = None
        if new:
            label = request.args.get("old")
            old = ws.store.issue(label) if label else ws.store.previous(new)
        return old, new

    # ------------------------------------------------------------ projects
    @app.get("/")
    def index():
        cards = []
        for e in home.list():
            try:
                ws = home.open(e.slug)
                latest = ws.store.latest()
                docs = ws.store.documents(latest) if latest else []
                c = counts(run_checks(ws, latest)[0]) if latest else None
                cards.append({"slug": e.slug, "p": ws.project, "latest": latest,
                              "issues": len(ws.store.issues()), "docs": len(docs), "c": c})
            except Exception as ex:  # a broken project shouldn't break the home page
                cards.append({"slug": e.slug, "error": str(ex)})
        return render_template("home.html", cards=cards)

    @app.post("/projects")
    def create_project():
        f = request.form
        if not f.get("name", "").strip():
            flash("Give the project a name.", "error")
            return redirect(url_for("index"))
        slug = home.create(f["name"].strip(), f.get("number", "").strip(), f.get("client", "").strip(),
                           f.get("eor", "").strip())
        flash("Project created. Upload documents to each package, then issue the first revision.", "ok")
        return redirect(url_for("documents", slug=slug))

    @app.post("/demo")
    def demo():
        target = home.root / "mission-bay-research-lab-demo"
        build_demo(target, quiet=True, outputs=False)
        flash("Demo project loaded with Issues A and B.", "ok")
        return redirect(url_for("overview", slug=target.name))

    @app.post("/p/<slug>/delete-project")
    def delete_project(slug):
        ws_for(slug)
        if request.form.get("confirm") != slug:
            flash("Type the project id to confirm deletion.", "error")
            return redirect(url_for("settings", slug=slug))
        home.delete(slug)
        flash("Project deleted.", "ok")
        return redirect(url_for("index"))

    # ------------------------------------------------------------ overview
    @app.get("/p/<slug>")
    def overview(slug):
        ws = ws_for(slug)
        latest = ws.store.latest()
        findings, c, diff = [], None, None
        if latest:
            findings, _ = run_checks(ws, latest)
            c = counts(findings)
            prev = ws.store.previous(latest)
            diff = compare(ws, prev, latest, with_text=False) if prev else None
        files, withdrawn = working_files(ws)
        pending = sum(1 for rows in files.values() for r in rows if r.status != "issued") + len(withdrawn)
        issues = []
        for iss in reversed(ws.store.issues()):
            docs = ws.store.documents(iss)
            issues.append((iss, len(docs), sum(d.n_pages for d in docs)))
        return render_template("overview.html", tab="overview", latest=latest, c=c, diff=diff,
                               top=[f for f in findings if f.severity == "error"][:5],
                               pending=pending, issues=issues)

    # ------------------------------------------------------------ documents / upload
    @app.get("/p/<slug>/documents")
    def documents(slug):
        ws = ws_for(slug)
        files, withdrawn = working_files(ws)
        latest = ws.store.latest()
        pending = {s: sum(1 for rows in files.values() for r in rows if r.status == s)
                   for s in ("new", "modified")}
        pending["withdrawn"] = len(withdrawn)
        next_label = _next_label(latest.label) if latest else "A"
        return render_template("documents.html", tab="documents", files=files, withdrawn=withdrawn,
                               latest=latest, pending=pending, next_label=next_label,
                               today=date.today().isoformat())

    @app.post("/p/<slug>/upload")
    def upload(slug):
        ws = ws_for(slug)
        pkg = request.form.get("package", "")
        ups = request.files.getlist("files")
        ok, errors = [], []
        for up in ups:
            if not up or not up.filename:
                continue
            try:
                name, replaced = save_upload(ws.project, pkg, up.filename, up.read())
                ok.append(name + (f" (replaced {', '.join(replaced)})" if replaced else ""))
            except ValueError as e:
                errors.append(str(e))
        if ok:
            flash(f"Uploaded to {pkg}: " + "; ".join(ok), "ok")
        for e in errors:
            flash(e, "error")
        if request.headers.get("X-Requested-With") == "fetch":
            return {"ok": ok, "errors": errors}
        return redirect(url_for("documents", slug=slug) + f"#pkg-{pkg}")

    @app.post("/p/<slug>/remove-file")
    def remove_file(slug):
        ws = ws_for(slug)
        try:
            delete_working_file(ws.project, request.form["package"], request.form["rel"])
            flash(f"Removed {request.form['rel']}. It will be withdrawn in the next issue.", "ok")
        except (KeyError, ValueError) as e:
            flash(str(e), "error")
        return redirect(url_for("documents", slug=slug))

    @app.post("/p/<slug>/issue")
    def make_issue(slug):
        ws = ws_for(slug)
        f = request.form
        try:
            res = ingest(ws.project, ws.store, f.get("label", ""), f.get("note", ""),
                         f.get("date") or None, replace=False)
        except ValueError as e:
            flash(str(e), "error")
            return redirect(url_for("documents", slug=slug))
        flash(f"Issue {res.issue.label} recorded: {res.documents} documents, {res.pages} sheets.", "ok")
        for w in res.warnings:
            flash(w, "warn")
        return redirect(url_for("overview", slug=slug))

    # ------------------------------------------------------------ findings
    @app.get("/p/<slug>/findings")
    def findings(slug):
        ws = ws_for(slug)
        issue = pick_issue(ws)
        if not issue:
            return render_template("empty.html", tab="findings")
        fs, basis = run_checks(ws, issue)
        return render_template("findings.html", tab="findings", issue=issue, findings=fs,
                               c=counts(fs), basis=basis)

    # ------------------------------------------------------------ changes
    @app.get("/p/<slug>/changes")
    def changes(slug):
        ws = ws_for(slug)
        old, new = pair(ws)
        if not new or not old:
            return render_template("empty.html", tab="changes",
                                   message="Changes appear once a project has two issues.")
        d = compare(ws, old, new)
        calc_changes = [c for c in d.changes if c.status != "unchanged"
                        and not ws.project.is_drawing_package(c.package)]
        return render_template("changes.html", tab="changes", d=d, changes=calc_changes, old=old, new=new)

    # ------------------------------------------------------------ drawings
    def _has_drawings(ws) -> bool:
        return any(p.is_drawings for p in ws.project.packages)

    @app.get("/p/<slug>/drawings")
    def drawings(slug):
        ws = ws_for(slug)
        if not _has_drawings(ws):
            return render_template("empty.html", tab="drawings",
                                   message="Add a package with kind: drawings in Settings to track drawing sets.")
        old, new = pair(ws)
        if not new:
            return render_template("empty.html", tab="drawings")
        if not old:
            sheets = sorted(dwg.sheet_index(ws, new).values(), key=lambda r: dwg._sheet_sort(r.sheet_id))
            return render_template("drawings.html", tab="drawings", old=None, new=new, diffs=[],
                                   first=sheets, c={})
        diffs = dwg.compare_sets(ws, old, new)
        diffs.sort(key=lambda d: (STATUS_ORDER[d.status], dwg._sheet_sort(d.sheet_id)))
        c = {s: sum(1 for d in diffs if d.status == s) for s in STATUS_ORDER}
        return render_template("drawings.html", tab="drawings", old=old, new=new, diffs=diffs, c=c, first=[])

    @app.get("/p/<slug>/drawings/sheet")
    def sheet(slug):
        ws = ws_for(slug)
        old, new = pair(ws)
        sid = request.args.get("id", "")
        if not new:
            abort(404)
        new_idx = dwg.sheet_index(ws, new)
        old_idx = dwg.sheet_index(ws, old) if old else {}
        o, n = old_idx.get(sid), new_idx.get(sid)
        if not o and not n:
            abort(404)
        if o and n:
            d = dwg.compare_sheets(ws, o, n) if (o.doc.sha256, o.page) != (n.doc.sha256, n.page) \
                else dwg.SheetDiff(sid, "unchanged", o, n)
        else:
            d = dwg.SheetDiff(sid, "new" if n else "removed", o, n)
        order = sorted(set(old_idx) | set(new_idx), key=dwg._sheet_sort)
        i = order.index(sid)
        nav = (order[i - 1] if i else None, order[i + 1] if i + 1 < len(order) else None)
        return render_template("sheet.html", tab="drawings", d=d, old=old, new=new, nav=nav)

    @app.get("/p/<slug>/img/page/<int:doc_id>/<int:page>.png")
    def page_image(slug, doc_id, page):
        ws = ws_for(slug)
        row = ws.store.db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not row:
            abort(404)
        doc = ws.store._doc(row)
        if page < 1 or page > doc.n_pages:
            abort(404)
        return send_file(dwg.render(ws, dwg.SheetRef("", doc, page)), max_age=86400)

    @app.get("/p/<slug>/img/diff/<key>/<kind>.png")
    def diff_image(slug, key, kind):
        ws = ws_for(slug)
        p = dwg.diff_image(ws, key, kind)
        if not p:
            abort(404)
        return send_file(p, max_age=86400)

    # ------------------------------------------------------------ register
    @app.get("/p/<slug>/register")
    def register_view(slug):
        ws = ws_for(slug)
        latest = ws.store.latest()
        if not latest:
            return render_template("empty.html", tab="register")
        iss, rows = register(ws.store, latest)
        order = {pid: i for i, pid in enumerate(ws.project.package_ids)}
        rows.sort(key=lambda r: (order.get(r.package, 999), r.doc_key))
        return render_template("register.html", tab="register", reg_issues=iss, rows=rows)

    # ------------------------------------------------------------ downloads
    @app.get("/p/<slug>/download/book")
    def download_book(slug):
        ws = ws_for(slug)
        issue = pick_issue(ws)
        if not issue:
            abort(404)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in ws.project.number or slug)
        out = ws.project.output_dir / f"{safe}_Calculations_Issue-{issue.label}.pdf"
        compile_issue(ws, issue, out)
        return send_file(out, as_attachment=True, download_name=out.name)

    @app.get("/p/<slug>/download/findings.csv")
    def download_findings(slug):
        ws = ws_for(slug)
        issue = pick_issue(ws)
        if not issue:
            abort(404)
        fs, _ = run_checks(ws, issue)
        out = ws.project.output_dir / f"findings_{issue.label}.csv"
        write_findings(fs, None, out)
        return send_file(out, as_attachment=True, download_name=out.name)

    @app.get("/p/<slug>/download/report.html")
    def download_report(slug):
        from ..report import render_report
        ws = ws_for(slug)
        issue = pick_issue(ws)
        if not issue:
            abort(404)
        fs, basis = run_checks(ws, issue)
        prev = ws.store.previous(issue)
        out = ws.project.output_dir / f"report_Issue-{issue.label}.html"
        render_report(ws, issue, fs, basis, compare(ws, prev, issue) if prev else None, out)
        return send_file(out, as_attachment=True, download_name=out.name)

    # ------------------------------------------------------------ settings
    @app.get("/p/<slug>/settings")
    def settings(slug):
        ws = ws_for(slug)
        basis_text = ws.project.basis_path.read_text() if ws.project.basis_path.exists() else ""
        return render_template("settings.html", tab="settings",
                               project_text=ws.project.path.read_text(), basis_text=basis_text)

    @app.post("/p/<slug>/settings")
    def save_settings(slug):
        ws = ws_for(slug)
        import yaml
        ptext = request.form.get("project_text", "")
        btext = request.form.get("basis_text", "")
        try:
            yaml.safe_load(ptext)
            braw = yaml.safe_load(btext) or {}
        except yaml.YAMLError as e:
            flash(f"YAML error: {e}", "error")
            return render_template("settings.html", tab="settings", project_text=ptext, basis_text=btext)
        backup = ws.project.path.read_text()
        ws.project.path.write_text(ptext)
        try:
            proj = load_project(ws.project.path)
        except Exception as e:
            ws.project.path.write_text(backup)
            flash(f"project.yaml not saved: {e}", "error")
            return render_template("settings.html", tab="settings", project_text=ptext, basis_text=btext)
        for p in proj.packages:
            p.path.mkdir(parents=True, exist_ok=True)
        proj.basis_path.write_text(btext)
        for err in parse_basis(braw, ws.rules).errors:
            flash(f"Design basis: {err}", "warn")
        flash("Settings saved. Design basis changes apply to the next issue.", "ok")
        return redirect(url_for("settings", slug=slug))

    return app


def _next_label(label: str) -> str:
    if label.isdigit():
        return str(int(label) + 1)
    if len(label) == 1 and label.isalpha() and label.upper() != "Z":
        return chr(ord(label.upper()) + 1)
    return ""
