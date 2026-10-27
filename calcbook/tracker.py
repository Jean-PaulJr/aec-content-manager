"""Project logs kept alongside the revision store: QA reviews, RFIs, submittals
and an activity timeline. Everything lives in the project's SQLite file, so it
travels with the project folder (e.g. on the company server)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from .store import Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS qa_log(
    id INTEGER PRIMARY KEY, doc_key TEXT NOT NULL, issue_label TEXT, action TEXT NOT NULL,
    person TEXT, comment TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS rfis(
    id INTEGER PRIMARY KEY, number TEXT, subject TEXT, question TEXT, status TEXT,
    raised_by TEXT, assigned_to TEXT, sent_on TEXT, due_on TEXT, answered_on TEXT,
    answer TEXT, docs TEXT, impact TEXT, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS submittals(
    id INTEGER PRIMARY KEY, number TEXT, spec_section TEXT, title TEXT, contractor TEXT,
    status TEXT, revision TEXT, received_on TEXT, due_on TEXT, returned_on TEXT,
    reviewer TEXT, notes TEXT, created TEXT, updated TEXT
);
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY, category TEXT, author TEXT, text TEXT, doc_key TEXT, tags TEXT, created TEXT
);
CREATE TABLE IF NOT EXISTS changes(
    id INTEGER PRIMARY KEY, number TEXT, title TEXT, description TEXT, source TEXT,
    opened_issue TEXT, auto_key TEXT UNIQUE, created TEXT
);
CREATE TABLE IF NOT EXISTS change_items(
    change_id INTEGER NOT NULL REFERENCES changes(id) ON DELETE CASCADE, doc_key TEXT NOT NULL,
    manual_resolved INTEGER DEFAULT 0, note TEXT, PRIMARY KEY(change_id, doc_key)
);
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, kind TEXT, ref TEXT, text TEXT, created TEXT
);
"""

QA_ACTIONS = {
    "submitted": "Submitted for review",
    "comment": "Comment",
    "returned": "Returned for revision",
    "checked": "Checked",
    "approved": "Approved",
}
QA_STATUS = {"submitted": "in review", "comment": "in review", "returned": "returned",
             "checked": "checked", "approved": "approved"}

NOTE_CATEGORIES = {"change": "Design change", "issue": "Issue", "lesson": "Lesson learned",
                   "decision": "Decision", "plancheck": "Plan check"}
CHANGE_SOURCES = ["Design criteria", "Code change", "RFI", "Plan check", "Owner / architect", "Field condition",
                  "Engineer", "Other"]
SUBMITTAL_KINDS = ["Shop drawings", "Product data", "Delegated design", "Material change request",
                   "Substitution request", "Mix design", "Test report"]
RFI_STATUSES = ["open", "answered", "closed"]
SUBMITTAL_STATUSES = ["received", "under review", "approved", "approved as noted",
                      "revise and resubmit", "rejected"]
SUBMITTAL_OPEN = {"received", "under review"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _overdue(due: str | None, is_open: bool) -> bool:
    if not (due and is_open):
        return False
    try:
        return date.fromisoformat(due) < date.today()
    except ValueError:
        return False


@dataclass
class QAEntry:
    id: int
    doc_key: str
    issue_label: str
    action: str
    person: str
    comment: str
    created: str

    @property
    def action_label(self) -> str:
        return QA_ACTIONS.get(self.action, self.action)


class Tracker:
    def __init__(self, store: Store):
        self.store = store
        self.db = store.db
        self.db.executescript(SCHEMA)
        cols = {r["name"] for r in self.db.execute("PRAGMA table_info(submittals)")}
        if "kind" not in cols:                       # older project files
            self.db.execute("ALTER TABLE submittals ADD COLUMN kind TEXT")
            self.db.commit()

    # ------------------------------------------------------------ engineering log
    def add_note(self, category: str, author: str, text: str, doc_key: str = "", tags: str = "") -> int:
        if category not in NOTE_CATEGORIES:
            raise ValueError(f"unknown category '{category}'")
        if not author.strip():
            raise ValueError("enter your name")
        if not text.strip():
            raise ValueError("write the note")
        cur = self.db.execute("INSERT INTO notes(category, author, text, doc_key, tags, created) VALUES(?,?,?,?,?,?)",
                              (category, author.strip(), text.strip(), doc_key.strip(), tags.strip(), _now()))
        self.db.commit()
        self.log("note", category, f"{NOTE_CATEGORIES[category]} logged by {author.strip()}: {text.strip()[:80]}")
        return cur.lastrowid  # type: ignore[return-value]

    def notes(self, category: str | None = None, query: str = "") -> list[dict]:
        sql, args = "SELECT * FROM notes", []
        where = []
        if category:
            where.append("category=?")
            args.append(category)
        if query:
            where.append("(text LIKE ? OR tags LIKE ? OR doc_key LIKE ? OR author LIKE ?)")
            args += [f"%{query}%"] * 4
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [dict(r) for r in self.db.execute(sql + " ORDER BY id DESC", args)]

    # ------------------------------------------------------------ change register
    def add_change(self, title: str, description: str, source: str, doc_keys: list[str],
                   opened_issue: str | None = None, auto_key: str | None = None) -> int:
        if not title.strip():
            raise ValueError("a change needs a title")
        if opened_issue is None:
            latest = self.store.latest()
            opened_issue = latest.label if latest else ""
        n = self.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] + 1
        cur = self.db.execute(
            "INSERT INTO changes(number, title, description, source, opened_issue, auto_key, created)"
            " VALUES(?,?,?,?,?,?,?)", (f"CHG-{n:03d}", title.strip(), description.strip(), source,
                                       opened_issue, auto_key, _now()))
        cid = cur.lastrowid
        self.db.executemany("INSERT OR IGNORE INTO change_items(change_id, doc_key) VALUES(?,?)",
                            [(cid, k) for k in doc_keys if k])
        self.db.commit()
        self.log("change", f"CHG-{n:03d}", f"Change CHG-{n:03d} opened — {title.strip()} "
                                           f"({len(doc_keys)} document(s) to update)")
        return cid  # type: ignore[return-value]

    def add_change_items(self, cid: int, doc_keys: list[str]) -> None:
        self.db.executemany("INSERT OR IGNORE INTO change_items(change_id, doc_key) VALUES(?,?)",
                            [(cid, k) for k in doc_keys if k])
        self.db.commit()

    def resolve_item(self, cid: int, doc_key: str, note: str = "", resolved: bool = True) -> None:
        self.db.execute("UPDATE change_items SET manual_resolved=?, note=? WHERE change_id=? AND doc_key=?",
                        (1 if resolved else 0, note.strip(), cid, doc_key))
        self.db.commit()
        self.log("change", str(cid), f"{doc_key} marked {'resolved' if resolved else 'open'}"
                                     + (f" — {note.strip()}" if note.strip() else ""))

    def delete_change(self, cid: int) -> None:
        self.db.execute("DELETE FROM change_items WHERE change_id=?", (cid,))
        self.db.execute("DELETE FROM changes WHERE id=?", (cid,))
        self.db.commit()

    def change_by_key(self, auto_key: str) -> dict | None:
        r = self.db.execute("SELECT * FROM changes WHERE auto_key=?", (auto_key,)).fetchone()
        return dict(r) if r else None

    def _revision_log(self) -> tuple[list[str], dict[str, list[tuple[str, str]]]]:
        """Issue labels in order, and per document the issues in which it changed."""
        from .history import UNCHANGED, doc_statuses
        labels, log, prev = [], {}, None
        for iss in self.store.issues():
            docs = self.store.documents_by_key(iss)
            for k, st in doc_statuses(prev, docs).items():
                if st != UNCHANGED:
                    log.setdefault(k, []).append((iss.label, st))
            labels.append(iss.label)
            prev = docs
        return labels, log

    def changes(self) -> list[dict]:
        labels, log = self._revision_log()
        out = []
        for r in self.db.execute("SELECT * FROM changes ORDER BY id DESC"):
            ch = dict(r)
            start = labels.index(ch["opened_issue"]) + 1 if ch["opened_issue"] in labels else 0
            later = set(labels[start:])
            items = []
            for it in self.db.execute("SELECT * FROM change_items WHERE change_id=? ORDER BY doc_key", (ch["id"],)):
                revs = [(lab, st) for lab, st in log.get(it["doc_key"], []) if lab in later]
                resolved_in = revs[0][0] if revs else ""
                items.append({"doc_key": it["doc_key"], "note": it["note"] or "",
                              "manual": bool(it["manual_resolved"]),
                              "resolved_in": resolved_in, "how": revs[0][1] if revs else "",
                              "resolved": bool(it["manual_resolved"]) or bool(resolved_in)})
            ch["items"] = items
            done = sum(i["resolved"] for i in items)
            ch["done"], ch["total"] = done, len(items)
            ch["status"] = "resolved" if items and done == len(items) else "open"
            out.append(ch)
        return out

    # ------------------------------------------------------------ activity
    def log(self, kind: str, ref: str, text: str) -> None:
        self.db.execute("INSERT INTO events(kind, ref, text, created) VALUES(?,?,?,?)",
                        (kind, ref, text, _now()))
        self.db.commit()

    def events(self, limit: int = 50) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]

    # ------------------------------------------------------------ QA
    def add_qa(self, doc_key: str, issue_label: str, action: str, person: str, comment: str) -> None:
        if action not in QA_ACTIONS:
            raise ValueError(f"unknown review action '{action}'")
        if not person.strip():
            raise ValueError("enter the reviewer's name")
        self.db.execute("INSERT INTO qa_log(doc_key, issue_label, action, person, comment, created)"
                        " VALUES(?,?,?,?,?,?)", (doc_key, issue_label, action, person.strip(),
                                                  comment.strip(), _now()))
        self.db.commit()
        self.log("qa", doc_key, f"{QA_ACTIONS[action]} by {person.strip()}"
                 + (f" (Issue {issue_label})" if issue_label else ""))

    def qa_entries(self, doc_key: str | None = None) -> list[QAEntry]:
        q = "SELECT * FROM qa_log" + (" WHERE doc_key=?" if doc_key else "") + " ORDER BY id DESC"
        rows = self.db.execute(q, (doc_key,) if doc_key else ())
        return [QAEntry(r["id"], r["doc_key"], r["issue_label"] or "", r["action"], r["person"] or "",
                        r["comment"] or "", r["created"]) for r in rows]

    def qa_status(self) -> dict[str, QAEntry]:
        """Latest QA entry per document."""
        out: dict[str, QAEntry] = {}
        for e in reversed(self.qa_entries()):
            out[e.doc_key] = e
        return out

    # ------------------------------------------------------------ RFIs
    def rfis(self) -> list[dict]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM rfis ORDER BY id DESC")]
        for r in rows:
            r["overdue"] = _overdue(r["due_on"], r["status"] == "open")
        return rows

    def next_number(self, table: str, prefix: str) -> str:
        n = self.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] + 1
        return f"{prefix}-{n:03d}"

    def save_rfi(self, data: dict, rfi_id: int | None = None) -> int:
        fields = ["number", "subject", "question", "status", "raised_by", "assigned_to", "sent_on",
                  "due_on", "answered_on", "answer", "docs", "impact"]
        vals = {f: (data.get(f) or "").strip() for f in fields}
        if not vals["subject"]:
            raise ValueError("an RFI needs a subject")
        vals["status"] = vals["status"] if vals["status"] in RFI_STATUSES else "open"
        if vals["status"] != "open" and not vals["answered_on"]:
            vals["answered_on"] = date.today().isoformat()
        if rfi_id:
            self.db.execute(f"UPDATE rfis SET {', '.join(f + '=?' for f in fields)}, updated=? WHERE id=?",
                            [vals[f] for f in fields] + [_now(), rfi_id])
            self.log("rfi", vals["number"], f"RFI {vals['number']} updated — {vals['status']}")
        else:
            vals["number"] = vals["number"] or self.next_number("rfis", "RFI")
            cur = self.db.execute(
                f"INSERT INTO rfis({', '.join(fields)}, created, updated) VALUES({', '.join('?' * (len(fields) + 2))})",
                [vals[f] for f in fields] + [_now(), _now()])
            rfi_id = cur.lastrowid
            self.log("rfi", vals["number"], f"RFI {vals['number']} opened — {vals['subject']}")
        self.db.commit()
        return rfi_id  # type: ignore[return-value]

    # ------------------------------------------------------------ submittals
    def submittals(self) -> list[dict]:
        rows = [dict(r) for r in self.db.execute("SELECT * FROM submittals ORDER BY id DESC")]
        for r in rows:
            r["overdue"] = _overdue(r["due_on"], r["status"] in SUBMITTAL_OPEN)
        return rows

    def save_submittal(self, data: dict, sub_id: int | None = None) -> int:
        fields = ["number", "kind", "spec_section", "title", "contractor", "status", "revision", "received_on",
                  "due_on", "returned_on", "reviewer", "notes"]
        vals = {f: (data.get(f) or "").strip() for f in fields}
        if not vals["title"]:
            raise ValueError("a submittal needs a title")
        vals["status"] = vals["status"] if vals["status"] in SUBMITTAL_STATUSES else "received"
        vals["revision"] = vals["revision"] or "0"
        vals["kind"] = vals["kind"] if vals["kind"] in SUBMITTAL_KINDS else "Shop drawings"
        if vals["status"] not in SUBMITTAL_OPEN and not vals["returned_on"]:
            vals["returned_on"] = date.today().isoformat()
        if sub_id:
            self.db.execute(f"UPDATE submittals SET {', '.join(f + '=?' for f in fields)}, updated=? WHERE id=?",
                            [vals[f] for f in fields] + [_now(), sub_id])
            self.log("submittal", vals["number"], f"Submittal {vals['number']} Rev {vals['revision']} — {vals['status']}")
        else:
            vals["number"] = vals["number"] or self.next_number("submittals", "SUB")
            cur = self.db.execute(
                f"INSERT INTO submittals({', '.join(fields)}, created, updated) "
                f"VALUES({', '.join('?' * (len(fields) + 2))})",
                [vals[f] for f in fields] + [_now(), _now()])
            sub_id = cur.lastrowid
            self.log("submittal", vals["number"], f"Submittal {vals['number']} Rev {vals['revision']} received — {vals['title']}")
        self.db.commit()
        return sub_id  # type: ignore[return-value]

    def get(self, table: str, row_id: int) -> dict | None:
        if table not in ("rfis", "submittals"):
            return None
        r = self.db.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
        return dict(r) if r else None


# --------------------------------------------------------------------------- #
# Changes driven by the design basis
# --------------------------------------------------------------------------- #
def _docs_using(ws, issue, param_key: str | None = None, code_family: str | None = None,
                also=None) -> list[str]:
    """Documents (ours, not external ones like plan check comments) that use a
    parameter or cite a code family, in `issue` and optionally `also`."""
    keys = []
    docs = list(ws.ordered_documents(issue)) + (list(ws.ordered_documents(also)) if also else [])
    for d in docs:
        pkg = ws.project.package(d.package)
        if (pkg and pkg.kind == "docs") or d.doc_key in keys:
            continue
        a = ws.analysis(d)
        if param_key and any(h.key == param_key for h in a.params):
            keys.append(d.doc_key)
        elif code_family and any(c.family == code_family for c in a.codes):
            keys.append(d.doc_key)
    return keys


def sync_basis_changes(ws, issue) -> list[int]:
    """Open a change for every design-criteria difference between `issue` and
    the issue before it, listing the documents that use that criterion. Safe
    to call repeatedly."""
    from .config import parse_basis
    from .rules import code_label
    prev = ws.store.previous(issue)
    if not prev:
        return []
    tr = Tracker(ws.store)
    old, new = parse_basis(prev.basis, ws.rules), parse_basis(issue.basis, ws.rules)
    created = []
    for k in sorted(set(old.params) | set(new.params)):
        a, b = old.params.get(k), new.params.get(k)
        if (a and a.display) == (b and b.display):
            continue
        key = f"basis:{prev.label}:{k}"
        if tr.change_by_key(key):
            continue
        label = ws.rules.label(k)
        created.append(tr.add_change(
            f"Design criteria: {label} {a.display if a else '—'} → {b.display if b else '—'}",
            f"Changed in the design basis for Issue {issue.label}. Every document that uses {label} "
            "must be revised or confirmed.", "Design criteria", _docs_using(ws, prev, param_key=k, also=issue),
            opened_issue=prev.label, auto_key=key))
    for fam in sorted(set(old.codes) | set(new.codes)):
        if old.codes.get(fam) == new.codes.get(fam):
            continue
        key = f"code:{prev.label}:{fam}"
        if tr.change_by_key(key):
            continue
        a = code_label(fam, old.codes[fam]) if fam in old.codes else "—"
        b = code_label(fam, new.codes[fam]) if fam in new.codes else "—"
        created.append(tr.add_change(
            f"Code change: {a} → {b}", f"Adopted edition changed for Issue {issue.label}.", "Code change",
            _docs_using(ws, prev, code_family=fam, also=issue), opened_issue=prev.label, auto_key=key))
    return created


def record_code_change(ws, family: str, old_edition: str | None, new_edition: str | None) -> dict:
    """Called when the adopted edition is switched in the app (before the next
    issue). Keeps one open change per code family since the latest issue, and
    drops it again if the edition is switched back."""
    from .config import parse_basis
    from .rules import code_label
    latest = ws.store.latest()
    tr = Tracker(ws.store)
    if not latest:
        return {"change": None, "affected": []}
    key = f"code:{latest.label}:{family}"
    issued = parse_basis(latest.basis, ws.rules).codes.get(family)
    existing = tr.change_by_key(key)
    if new_edition == issued:
        if existing:
            tr.delete_change(existing["id"])
            tr.log("change", family, f"{family} switched back to {code_label(family, issued)} — change withdrawn")
        return {"change": None, "affected": []}
    affected = _docs_using(ws, latest, code_family=family)
    a = code_label(family, issued) if issued else "—"
    b = code_label(family, new_edition) if new_edition else "—"
    title = f"Code change: {a} → {b}"
    if existing:
        tr.db.execute("UPDATE changes SET title=? WHERE id=?", (title, existing["id"]))
        tr.db.commit()
        tr.add_change_items(existing["id"], affected)
        cid = existing["id"]
        tr.log("change", family, title)
    else:
        cid = tr.add_change(title, "Adopted edition switched in BigDelta. Documents citing this code must be "
                                   "revised to the new edition or confirmed.", "Code change", affected,
                            opened_issue=latest.label, auto_key=key)
    return {"change": cid, "affected": affected}
