"""End-to-end tests on the demo project plus unit tests for extraction."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from calcbook.checks import run_checks  # noqa: E402
from calcbook.config import load_project  # noqa: E402
from calcbook.diff import compare  # noqa: E402
from calcbook.extract import analyze, page_hash, strip_rev_suffix  # noqa: E402
from calcbook.history import Workspace, compress_ranges  # noqa: E402
from calcbook.rules import load_rules  # noqa: E402
from calcbook.store import Store  # noqa: E402


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from calcbook.demo import build
    target = tmp_path_factory.mktemp("demo") / "proj"
    build(target, quiet=True)
    project = load_project(target / "project.yaml")
    return Workspace(project, Store(project.root), load_rules(project.rules_path))


# ---------------------------------------------------------------- extraction
RULES = load_rules()


def params(text):
    return {(h.key, h.value) for h in analyze([text], RULES, ["GRV", "LAT"], "LAT").params}


def test_units_normalised():
    assert ("fc", 4000.0) in params("f'c = 4 ksi")
    assert ("fc", 4000.0) in params("f’c = 4,000 psi")
    assert ("soil_bearing", 3000.0) in params("qa = 3 ksf")


def test_roof_live_load_not_mistaken_for_floor():
    p = params("Roof Live Load = 20 psf")
    assert ("roof_live_load", 20.0) in p
    assert not any(k == "floor_live_load" for k, _ in p)


def test_reaction_not_mistaken_for_r_factor():
    assert not any(k == "r_factor" for k, _ in params("R = 12.5 kips"))
    assert ("r_factor", 8.0) in params("R = 8")


def test_member_and_xref_extraction():
    a = analyze(["per GRV-02: B-12  W18X35  DCR = 0.86\nloads per GRV Rev A"], RULES, ["GRV", "LAT"], "LAT")
    assert [(e.mark, e.size) for e in a.elements] == [("B-12", "W18X35")]
    assert a.dcrs[0].mark == "B-12" and a.dcrs[0].value == 0.86
    assert [(x.package, x.rev) for x in a.xrefs] == [("GRV", "A")]


def test_rev_suffix_and_titleblock_noise():
    assert strip_rev_suffix("GRV-02_Floor_Beams_RevB") == "GRV-02_Floor_Beams"
    assert strip_rev_suffix("Rebar2") == "Rebar2"
    assert page_hash("calc text\nRev A\nSheet 1 of 3") == page_hash("calc text\nRev B\nSheet 1 of 4")
    assert page_hash("loads per GRV Rev A") != page_hash("loads per GRV Rev B")


def test_compress_ranges():
    assert compress_ranges([1, 2, 3, 5, 7, 8]) == "1–3, 5, 7–8"


# ---------------------------------------------------------------- workflow
def test_revision_diff(demo):
    a, b = demo.store.resolve("A"), demo.store.resolve("B")
    d = compare(demo, a, b)
    calcs = [c for c in d.changes if demo.project.is_calc_package(c.package)]
    assert {s: sum(c.status == s for c in calcs) for s in ("new", "revised", "removed", "unchanged")} == \
        {"new": 2, "revised": 3, "removed": 1, "unchanged": 5}     # FDN-02 + scanned FDN-04
    beams = next(c for c in d.changes if c.doc_key == "GRV/GRV-02_Floor_Beams")
    assert beams.changed_pages == [1, 3]           # page 2 only had title-block changes
    assert ("B-12", "W16X31", "W18X35") in beams.member_changes
    assert ("Basic wind speed V", "92 mph", "95 mph") in d.basis_changes


def test_design_intent_checks(demo):
    findings, _ = run_checks(demo, demo.store.resolve("B"))
    got = {(f.check, f.doc_key) for f in findings}
    assert ("basis-mismatch", "FDN/FDN-01_Spread_Footings") in got
    assert ("basis-mismatch", "FDN/FDN-02_Slab_on_Grade") in got
    assert ("basis-mismatch", "LAT/LAT-01_Seismic_Base_Shear") in got
    assert ("code-edition", "LAT/LAT-02_Wind") in got
    assert ("basis-changed", "LAT/LAT-02_Wind") in got
    assert ("stale-reference", "CONN/CONN-01_Beam_Connections") in got
    assert ("dcr-exceeded", "GRV/GRV-03_Columns") in got
    assert any(f.check == "member-size-mismatch" and "B-12" in f.message for f in findings)
    # drawing vs calc: S-201 shows B-14 W14X26, GRV designs W14X22
    assert any(f.check == "member-size-mismatch" and "B-14" in f.message for f in findings)
    # FDN-01 cites "GRV Rev B", which is current — must not be flagged
    assert ("stale-reference", "FDN/FDN-01_Spread_Footings") not in got


def test_compiled_book(demo):
    from calcbook.compile import compile_issue
    out = demo.project.output_dir / "book_B.pdf"
    info = compile_issue(demo, demo.store.resolve("B"), out)
    r = PdfReader(str(out))
    assert len(r.pages) == info["sheets"]
    top = [o.title for o in r.outline if not isinstance(o, list)]
    assert top[:4] == ["Cover", "Contents", "Codes & assumptions", "Revision summary"]
    assert top[4:] == ["1  Gravity Framing", "2  Lateral System", "3  Foundations", "4  Steel Connections"]
    assert f"Sheet {info['sheets']} of {info['sheets']}" in r.pages[-1].extract_text()
    beams = r.pages[6].extract_text()                    # 1.2 GRV-02 sheet 1, after section title page
    assert "GRV-02" in beams and "REVISED" in beams
    assert "Gravity Framing" in r.pages[4].extract_text()  # section title page


def test_report_package_builder(tmp_path):
    from calcbook.compile import compile_draft
    from calcbook.demo import calc_pdf
    from calcbook.projects import (Home, add_section, number_working, remove_section, save_upload,
                                   set_order, update_section, working_files)
    home = Home(tmp_path)
    slug = home.create("Builder Test", template="wood-multifamily")
    ws = home.open(slug)
    assert [p.id for p in ws.project.packages][:3] == ["CRIT", "FRM", "SW"]
    for name in ("A-01_Loads.pdf", "A-03_Joists.pdf"):
        f = tmp_path / name
        calc_pdf(f, name[:4], name, "me", "A", [["LL = 40 psf"]])
        save_upload(ws.project, "FRM", name, f.read_bytes())
    f = tmp_path / "A-02_Beams.pdf"
    calc_pdf(f, "A-02", "Beams", "me", "A", [["p1"], ["p2"]])
    ws = home.open(slug)
    save_upload(ws.project, "FRM", f.name, f.read_bytes(), position="after:A-01_Loads")
    ws = home.open(slug)
    files, _ = working_files(ws)
    assert [x.name for x in files["FRM"]] == ["A-01_Loads", "A-02_Beams", "A-03_Joists"]
    n = number_working(ws.project, files, False)
    assert [x.start for x in files["FRM"]] == [5, 6, 8]   # cover, contents, codes, section title page
    set_order(ws.project, "FRM", ["A-03_Joists", "A-01_Loads", "A-02_Beams"])
    update_section(ws.project, "SW", include=False)
    sid = add_section(ws.project, "Elevator Pit")
    ws = home.open(slug)
    assert ws.project.package(sid).path.exists()
    remove_section(ws.project, "HD")
    ws = home.open(slug)
    assert ws.project.package("HD") is None and not ws.project.package("SW").include
    info = compile_draft(ws, tmp_path / "draft.pdf")
    r = PdfReader(str(tmp_path / "draft.pdf"))
    assert len(r.pages) == info["sheets"] == n["total"]
    sec = [o for o in r.outline if isinstance(o, list)][0]
    titles = [o.title for o in sec]
    assert [t[:3] for t in titles] == ["1.1", "1.2", "1.3"]
    assert "Joists" in titles[0] and "Loads" in titles[1] and "Beams" in titles[2]
    # images of hand calcs become PDF pages
    from PIL import Image
    import io
    buf = io.BytesIO()
    Image.new("RGB", (400, 500), "white").save(buf, "PNG")
    name, _ = save_upload(ws.project, "FDN", "hand calc.png", buf.getvalue())
    assert name.endswith(".pdf")


def test_tracker(tmp_path):
    from calcbook.tracker import Tracker
    from calcbook.store import Store
    tr = Tracker(Store(tmp_path))
    rid = tr.save_rfi({"subject": "Beam penetration", "due_on": "2020-01-01"})
    assert tr.rfis()[0]["number"] == "RFI-001" and tr.rfis()[0]["overdue"]
    tr.save_rfi({"subject": "Beam penetration", "status": "answered", "number": "RFI-001"}, rid)
    assert not tr.rfis()[0]["overdue"] and tr.rfis()[0]["answered_on"]
    tr.save_submittal({"title": "Steel shop drawings", "status": "approved"})
    assert tr.submittals()[0]["returned_on"]
    tr.add_qa("GRV/X", "A", "approved", "M. Okafor", "")
    assert tr.qa_status()["GRV/X"].action == "approved"
    with pytest.raises(ValueError):
        tr.add_qa("GRV/X", "A", "approved", "", "")
    assert len(tr.events()) == 4


def test_drawing_changes(demo):
    from calcbook import drawings as dwg
    diffs = {d.sheet_id: d for d in dwg.compare_sets(demo, demo.store.resolve("A"), demo.store.resolve("B"))}
    assert set(diffs) == {"S-001", "S-101", "S-201"}          # matched across re-organised files
    s201 = diffs["S-201"]
    assert s201.status == "changed" and s201.new.title == "LEVEL 2 FRAMING"
    assert "W16X31" in s201.words_removed and "W18X35" in s201.words_added
    assert len(s201.regions) >= 3                               # label, new beam, deleted column (+ title block)
    assert dwg.diff_image(demo, s201.key, "overlay") is not None
    assert "92" in diffs["S-001"].words_removed and "95" in diffs["S-001"].words_added


def test_web_app(tmp_path):
    from calcbook.demo import calc_pdf
    from calcbook.web import create_app
    app = create_app(tmp_path / "ws")
    c = app.test_client()
    assert c.get("/").status_code == 200
    r = c.post("/projects", data={"name": "Pier 70 Garage", "number": "2026-021"})
    assert r.status_code == 302
    slug = "pier-70-garage"
    pdf = tmp_path / "GRV-01_Criteria_RevA.pdf"
    calc_pdf(pdf, "GRV-01", "Criteria", "me", "A", [["LL = 40 psf", "f'c = 5000 psi"]])
    r = c.post(f"/p/{slug}/upload", data={"package": "GRV", "files": (open(pdf, "rb"), pdf.name)},
               content_type="multipart/form-data")
    assert r.status_code == 302
    r = c.post(f"/p/{slug}/upload", data={"package": "GRV", "files": (b"not a pdf", "x.pdf")},
               content_type="multipart/form-data")
    assert r.status_code == 302                                  # rejected with a flash, no crash
    assert c.post(f"/p/{slug}/issue", data={"label": "A", "note": "first"}).status_code == 302
    for page in ("", "/documents", "/findings", "/changes", "/drawings", "/register", "/settings",
                 "/qa", "/rfis", "/submittals", "/info"):
        assert c.get(f"/p/{slug}{page}").status_code == 200, page
    assert c.get(f"/p/{slug}/download/book").status_code == 200
    assert c.get(f"/p/{slug}/download/draft").status_code == 200
    assert c.post(f"/p/{slug}/rfis", data={"subject": "Opening in B-12"}).status_code == 302
    assert b"Opening in B-12" in c.get(f"/p/{slug}/rfis").data
    assert c.post(f"/p/{slug}/order", json={"sections": ["LAT", "CRIT"]}).get_json()["ok"]
    assert c.post(f"/p/{slug}/info", data={"name": "Pier 70 Garage", "phase": "Construction",
                                           "ms_name": ["Pour"], "ms_due": ["2027-01-01"]}).status_code == 302
    assert b"Construction" in c.get(f"/p/{slug}").data
    assert c.get("/p/nope/findings").status_code == 404
    # two projects side by side
    c.post("/projects", data={"name": "Second Job"})
    assert b"Second Job" in c.get("/").data and b"Pier 70 Garage" in c.get("/").data
