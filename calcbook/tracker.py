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
        self.db = store.db
        self.db.executescript(SCHEMA)

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
        fields = ["number", "spec_section", "title", "contractor", "status", "revision", "received_on",
                  "due_on", "returned_on", "reviewer", "notes"]
        vals = {f: (data.get(f) or "").strip() for f in fields}
        if not vals["title"]:
            raise ValueError("a submittal needs a title")
        vals["status"] = vals["status"] if vals["status"] in SUBMITTAL_STATUSES else "received"
        vals["revision"] = vals["revision"] or "0"
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
