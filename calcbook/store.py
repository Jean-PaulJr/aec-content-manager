"""Revision store: an SQLite register of every issue plus a content-addressed
archive of the exact PDFs that were issued."""
from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS issues(
    id INTEGER PRIMARY KEY,
    label TEXT UNIQUE NOT NULL,
    issued_on TEXT,
    note TEXT,
    basis_json TEXT,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS documents(
    id INTEGER PRIMARY KEY,
    issue_id INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    package TEXT NOT NULL,
    doc_key TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    title TEXT,
    sha256 TEXT NOT NULL,
    n_pages INTEGER,
    archive_path TEXT,
    has_text INTEGER,
    UNIQUE(issue_id, doc_key)
);
CREATE TABLE IF NOT EXISTS pages(
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    text TEXT,
    norm_hash TEXT,
    PRIMARY KEY(document_id, page_no)
);
"""


@dataclass
class Issue:
    id: int
    label: str
    issued_on: str
    note: str
    basis: dict
    created_at: str


@dataclass
class DocRecord:
    id: int
    issue_id: int
    package: str
    doc_key: str
    rel_path: str
    title: str
    sha256: str
    n_pages: int
    archive_path: str
    has_text: bool

    @property
    def name(self) -> str:
        return self.doc_key.split("/", 1)[-1]


class Store:
    def __init__(self, root: Path):
        self.dir = Path(root) / ".calcbook"
        (self.dir / "archive").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.dir / "calcbook.db")
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.executescript(SCHEMA)

    # ---------------- issues ----------------
    @staticmethod
    def _issue(row) -> Issue:
        return Issue(row["id"], row["label"], row["issued_on"], row["note"] or "",
                     json.loads(row["basis_json"] or "{}"), row["created_at"])

    def issues(self) -> list[Issue]:
        return [self._issue(r) for r in self.db.execute("SELECT * FROM issues ORDER BY id")]

    def issue(self, label: str) -> Issue | None:
        r = self.db.execute("SELECT * FROM issues WHERE label=?", (label,)).fetchone()
        return self._issue(r) if r else None

    def latest(self) -> Issue | None:
        r = self.db.execute("SELECT * FROM issues ORDER BY id DESC LIMIT 1").fetchone()
        return self._issue(r) if r else None

    def previous(self, issue: Issue) -> Issue | None:
        r = self.db.execute("SELECT * FROM issues WHERE id<? ORDER BY id DESC LIMIT 1",
                            (issue.id,)).fetchone()
        return self._issue(r) if r else None

    def resolve(self, label: str | None) -> Issue:
        """Issue by label, or the latest one when label is None."""
        iss = self.issue(label) if label else self.latest()
        if iss is None:
            raise LookupError(f"issue '{label}' not found" if label else "no issues ingested yet")
        return iss

    def create_issue(self, label: str, issued_on: str, note: str, basis: dict) -> Issue:
        cur = self.db.execute(
            "INSERT INTO issues(label, issued_on, note, basis_json, created_at) VALUES(?,?,?,?,?)",
            (label, issued_on, note, json.dumps(basis), datetime.now().isoformat(timespec="seconds")))
        return self.issue(label)  # type: ignore[return-value]

    def delete_issue(self, label: str) -> None:
        self.db.execute("DELETE FROM issues WHERE label=?", (label,))
        self.db.commit()

    # ---------------- documents ----------------
    @staticmethod
    def _doc(row) -> DocRecord:
        return DocRecord(row["id"], row["issue_id"], row["package"], row["doc_key"], row["rel_path"],
                         row["title"], row["sha256"], row["n_pages"], row["archive_path"],
                         bool(row["has_text"]))

    def archive(self, src: Path, sha: str) -> str:
        rel = Path("archive") / sha[:2] / f"{sha}.pdf"
        dest = self.dir / rel
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
        return str(rel)

    def archived_file(self, doc: DocRecord) -> Path:
        return self.dir / doc.archive_path

    def add_document(self, issue: Issue, package: str, doc_key: str, rel_path: str, title: str,
                     sha: str, archive_path: str, pages: list[str], hashes: list[str]) -> None:
        has_text = any(t.strip() for t in pages)
        cur = self.db.execute(
            "INSERT INTO documents(issue_id, package, doc_key, rel_path, title, sha256, n_pages,"
            " archive_path, has_text) VALUES(?,?,?,?,?,?,?,?,?)",
            (issue.id, package, doc_key, rel_path, title, sha, len(pages), archive_path, int(has_text)))
        self.db.executemany(
            "INSERT INTO pages(document_id, page_no, text, norm_hash) VALUES(?,?,?,?)",
            [(cur.lastrowid, i + 1, t, h) for i, (t, h) in enumerate(zip(pages, hashes))])

    def commit(self) -> None:
        self.db.commit()

    def documents(self, issue: Issue) -> list[DocRecord]:
        return [self._doc(r) for r in self.db.execute(
            "SELECT * FROM documents WHERE issue_id=? ORDER BY package, rel_path", (issue.id,))]

    def documents_by_key(self, issue: Issue) -> dict[str, DocRecord]:
        return {d.doc_key: d for d in self.documents(issue)}

    def pages(self, doc: DocRecord) -> list[str]:
        return [r["text"] or "" for r in self.db.execute(
            "SELECT text FROM pages WHERE document_id=? ORDER BY page_no", (doc.id,))]

    def page_hashes(self, doc: DocRecord) -> list[str]:
        return [r["norm_hash"] for r in self.db.execute(
            "SELECT norm_hash FROM pages WHERE document_id=? ORDER BY page_no", (doc.id,))]

    def history(self, doc_key: str) -> list[tuple[Issue, DocRecord | None]]:
        out = []
        for iss in self.issues():
            r = self.db.execute("SELECT * FROM documents WHERE issue_id=? AND doc_key=?",
                                (iss.id, doc_key)).fetchone()
            out.append((iss, self._doc(r) if r else None))
        return out
