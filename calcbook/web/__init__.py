"""AEC Content Manager — local web app.

Several projects; report package builder (sections from templates, upload,
ordering, signatures, draft and issued calc books); revision tracking; design
intent checks; visual drawing comparison; QA reviews; RFIs; submittals;
project phase and milestones.

    calcbook serve --workspace ./projects        # then open http://127.0.0.1:8000

The workspace can be a folder on the company server (a mapped drive or UNC path).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from flask import (Flask, abort, flash, g, redirect, render_template, request, send_file,
                   url_for)

from .. import drawings as dwg
from ..checks import counts, run_checks, write_findings
from ..compile import compile_draft, compile_issue
from ..config import PHASES, SIGN_ROLES, edit_project, load_project, parse_basis
from ..demo import build as build_demo
from ..diff import compare
from ..history import compress_ranges, register
from ..ingest import ingest
from ..projects import (Home, add_section, delete_working_file, doc_name, move_document, number_working,
                        place_document, remove_section, reorder_sections, save_upload, set_order,
                        update_section, working_files)
from ..tracker import (QA_ACTIONS, QA_STATUS, RFI_STATUSES, SUBMITTAL_OPEN, SUBMITTAL_STATUSES,
                       Tracker)
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
                "region_label": dwg.region_label, "PHASES": PHASES}

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
        return render_template("home.html", cards=cards, templates=home.templates())

    @app.post("/projects")
    def create_project():
        f = request.form
        if not f.get("name", "").strip():
            flash("Give the project a name.", "error")
            return redirect(url_for("index"))
        slug = home.create(f["name"].strip(), f.get("number", "").strip(), f.get("client", "").strip(),
                           f.get("eor", "").strip(), template=f.get("template") or None,
                           address=f.get("address", "").strip(), phase=f.get("phase") or "Design Development")
        Tracker(home.open(slug).store).log("project", slug, "Project created")
        flash("Project created with its section folders. Upload PDFs to each section, then issue the first revision.", "ok")
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
        tr = Tracker(ws.store)
        rfis, subs = tr.rfis(), tr.submittals()
        qa = tr.qa_status()
        calc_docs = [d for d in (ws.store.documents(latest) if latest else [])
                     if not ws.project.is_drawing_package(d.package)]
        approved = sum(1 for d in calc_docs if qa.get(d.doc_key) and qa[d.doc_key].action == "approved")
        stats = {
            "rfi_open": sum(r["status"] == "open" for r in rfis), "rfi_overdue": sum(r["overdue"] for r in rfis),
            "sub_open": sum(r["status"] in SUBMITTAL_OPEN for r in subs),
            "sub_overdue": sum(r["overdue"] for r in subs),
            "qa_approved": approved, "qa_total": len(calc_docs),
        }
        ms = ws.project.milestones
        return render_template("overview.html", tab="overview", latest=latest, c=c, diff=diff,
                               top=[f for f in findings if f.severity == "error"][:5],
                               pending=pending, issues=issues, stats=stats, events=tr.events(12),
                               ms_done=sum(m.done for m in ms), ms_total=len(ms))

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
        numbering = number_working(ws.project, files, latest is not None)
        return render_template("documents.html", tab="documents", files=files, withdrawn=withdrawn,
                               latest=latest, pending=pending, next_label=next_label,
                               today=date.today().isoformat(), numbering=numbering,
                               templates=home.templates(), roles=SIGN_ROLES)

    @app.post("/p/<slug>/upload")
    def upload(slug):
        ws = ws_for(slug)
        pkg = request.form.get("package", "")
        position = request.form.get("position", "end")
        ups = request.files.getlist("files")
        ok, errors = [], []
        for up in ups:
            if not up or not up.filename:
                continue
            try:
                name, replaced = save_upload(ws.project, pkg, up.filename, up.read(), position)
                ok.append(name + (f" (replaced {', '.join(replaced)})" if replaced else ""))
                if position != "end" and not replaced:      # keep a multi-file drop in order
                    position = f"after:{doc_name(name)}"
                ws.project = load_project(ws.project.path)
            except ValueError as e:
                errors.append(str(e))
        if ok:
            Tracker(ws.store).log("upload", pkg, f"Uploaded to {pkg}: " + "; ".join(ok))
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
        Tracker(ws.store).log("issue", res.issue.label,
                              f"Issue {res.issue.label} recorded — {res.issue.note or 'no description'} "
                              f"({res.documents} documents, {res.pages} sheets)")
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

    # ------------------------------------------------------------ report package builder
    @app.post("/p/<slug>/order")
    def order(slug):
        ws = ws_for(slug)
        data = request.get_json(silent=True) or {}
        try:
            if data.get("sections"):
                reorder_sections(ws.project, [str(x).upper() for x in data["sections"]])
            files, _ = working_files(ws)
            where = {f.name.lower(): pid for pid, rows in files.items() for f in rows}
            for pid, names in (data.get("docs") or {}).items():
                for n in names:
                    src = where.get(n.lower())
                    if src and src != pid:
                        move_document(ws.project, n, src, pid)
                        Tracker(ws.store).log("upload", pid, f"Moved {n} from {src} to {pid}")
                set_order(ws.project, pid, names)
        except ValueError as e:
            return {"ok": False, "error": str(e)}, 400
        return {"ok": True}

    @app.post("/p/<slug>/section")
    def section(slug):
        ws = ws_for(slug)
        f = request.form
        action, sid = f.get("action"), f.get("id", "").upper()
        try:
            if action == "add":
                sid = add_section(ws.project, f.get("title", ""), f.get("kind", "calcs"), f.get("after") or None)
                flash(f"Section '{f.get('title')}' added with its own folder.", "ok")
            elif action == "update":
                update_section(ws.project, sid, title=f.get("title", "").strip() or sid,
                               responsible=f.get("responsible", "").strip(),
                               include=f.get("include") == "on", title_page=f.get("title_page") == "on")
                flash("Section saved.", "ok")
            elif action == "remove":
                moved = remove_section(ws.project, sid)
                flash(f"Section {sid} removed." + (f" Its files were kept in {moved}." if moved else ""), "ok")
            Tracker(ws.store).log("section", sid, f"Section {sid}: {action}")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("documents", slug=slug) + (f"#pkg-{sid}" if action != "remove" else ""))

    @app.post("/p/<slug>/signatures")
    def signatures(slug):
        ws = ws_for(slug)

        def fn(data):
            sig = data["project"].setdefault("signatures", {})
            for role, _ in SIGN_ROLES:
                sig[role] = {"name": request.form.get(f"{role}_name", "").strip(),
                             "date": request.form.get(f"{role}_date", "").strip()}
        edit_project(ws.project.path, fn)
        flash("Signature block saved. It prints on the cover of every book.", "ok")
        return redirect(url_for("documents", slug=slug) + "#signatures")

    @app.post("/p/<slug>/template")
    def save_template(slug):
        ws = ws_for(slug)
        name = request.form.get("name", "").strip()
        if not name:
            flash("Name the template.", "error")
        else:
            home.save_template(name, ws.project)
            flash(f"Template '{name}' saved. Pick it when you create the next project.", "ok")
        return redirect(url_for("documents", slug=slug))

    @app.get("/p/<slug>/download/draft")
    def download_draft(slug):
        ws = ws_for(slug)
        safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in ws.project.number or slug)
        out = ws.project.output_dir / f"{safe}_Calculations_DRAFT.pdf"
        compile_draft(ws, out)
        return send_file(out, as_attachment=True, download_name=out.name)

    # ------------------------------------------------------------ QA reviews
    @app.get("/p/<slug>/qa")
    def qa(slug):
        ws = ws_for(slug)
        tr = Tracker(ws.store)
        latest = ws.store.latest()
        docs = [d for d in (ws.ordered_documents(latest) if latest else [])]
        status = tr.qa_status()
        entries = tr.qa_entries()
        counts_ = {"approved": 0, "checked": 0, "in review": 0, "returned": 0, "not started": 0}
        for d in docs:
            e = status.get(d.doc_key)
            counts_[QA_STATUS[e.action] if e else "not started"] += 1
        return render_template("qa.html", tab="qa", latest=latest, docs=docs, status=status,
                               entries=entries, actions=QA_ACTIONS, qa_status=QA_STATUS, counts=counts_,
                               focus=request.args.get("doc", ""))

    @app.post("/p/<slug>/qa")
    def qa_add(slug):
        ws = ws_for(slug)
        f = request.form
        latest = ws.store.latest()
        try:
            for key in f.getlist("doc_key"):
                Tracker(ws.store).add_qa(key, latest.label if latest else "", f.get("action", ""),
                                         f.get("person", ""), f.get("comment", ""))
            flash("Review recorded.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("qa", slug=slug))

    # ------------------------------------------------------------ RFIs / submittals
    @app.get("/p/<slug>/rfis")
    def rfis(slug):
        ws = ws_for(slug)
        tr = Tracker(ws.store)
        edit = tr.get("rfis", request.args.get("edit", type=int) or 0) if request.args.get("edit") else None
        return render_template("rfis.html", tab="rfis", rows=tr.rfis(), edit=edit, statuses=RFI_STATUSES,
                               next_no=tr.next_number("rfis", "RFI"), today=date.today().isoformat())

    @app.post("/p/<slug>/rfis")
    def rfis_save(slug):
        ws = ws_for(slug)
        try:
            Tracker(ws.store).save_rfi(request.form, request.form.get("id", type=int))
            flash("RFI saved.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("rfis", slug=slug))

    @app.get("/p/<slug>/submittals")
    def submittals(slug):
        ws = ws_for(slug)
        tr = Tracker(ws.store)
        edit = tr.get("submittals", request.args.get("edit", type=int) or 0) if request.args.get("edit") else None
        return render_template("submittals.html", tab="submittals", rows=tr.submittals(), edit=edit,
                               statuses=SUBMITTAL_STATUSES, next_no=tr.next_number("submittals", "SUB"),
                               today=date.today().isoformat())

    @app.post("/p/<slug>/submittals")
    def submittals_save(slug):
        ws = ws_for(slug)
        try:
            Tracker(ws.store).save_submittal(request.form, request.form.get("id", type=int))
            flash("Submittal saved.", "ok")
        except ValueError as e:
            flash(str(e), "error")
        return redirect(url_for("submittals", slug=slug))

    # ------------------------------------------------------------ project info & progress
    @app.get("/p/<slug>/info")
    def info(slug):
        ws_for(slug)
        return render_template("info.html", tab="info")

    @app.post("/p/<slug>/info")
    def info_save(slug):
        ws = ws_for(slug)
        f = request.form
        if not f.get("name", "").strip():
            flash("The project needs a name.", "error")
            return redirect(url_for("info", slug=slug))
        old_phase = ws.project.phase

        def fn(data):
            pr = data["project"]
            for k in ("name", "number", "client", "address", "engineer_of_record"):
                pr[k] = f.get(k, "").strip()
            pr["phase"] = f.get("phase") if f.get("phase") in PHASES else pr.get("phase", PHASES[0])
            ms = []
            for i, name in enumerate(f.getlist("ms_name")):
                if name.strip():
                    ms.append({"name": name.strip(), "due": f.getlist("ms_due")[i].strip(),
                               "done": str(i) in f.getlist("ms_done")})
            pr["milestones"] = ms
        edit_project(ws.project.path, fn)
        if f.get("phase") != old_phase:
            Tracker(ws.store).log("phase", f.get("phase", ""), f"Phase changed: {old_phase} → {f.get('phase')}")
        flash("Project info saved.", "ok")
        return redirect(url_for("info", slug=slug))

    return app


def _next_label(label: str) -> str:
    if label.isdigit():
        return str(int(label) + 1)
    if len(label) == 1 and label.isalpha() and label.upper() != "Z":
        return chr(ord(label.upper()) + 1)
    return ""
