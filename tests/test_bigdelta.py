"""Behaviour specs for the BigDelta brief (written before the implementation).

Each test names the brief item it covers.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    from calcbook.demo import build
    from calcbook.projects import open_workspace
    target = tmp_path_factory.mktemp("bd") / "proj"
    build(target, quiet=True, outputs=False)
    return open_workspace(target / "project.yaml")


# --------------------------------------------------------------- name + logo
def test_logo_is_generated_for_every_platform(tmp_path):
    """Brief: create a logo, embedded automatically, works on Mac, Windows, etc."""
    from calcbook import brand
    assert brand.APP_NAME == "BigDelta"
    svg = brand.logo_svg()
    assert svg.startswith("<svg") and "BigDelta" in svg          # accessible title
    out = brand.write_icons(tmp_path)
    for name in ("favicon.ico", "favicon.svg", "icon-512.png", "apple-touch-icon.png"):
        assert (tmp_path / name).stat().st_size > 0, name
    from PIL import Image
    ico = Image.open(tmp_path / "favicon.ico")
    assert ico.format == "ICO"
    assert set(out) >= {"favicon.ico", "icon-512.png"}


def test_web_pages_carry_name_and_icons(tmp_path):
    from calcbook.web import create_app
    c = create_app(tmp_path / "ws").test_client()
    html = c.get("/").get_data(as_text=True)
    assert "BigDelta" in html and 'rel="icon"' in html and "apple-touch-icon" in html
    assert c.get("/static/favicon.ico").status_code == 200


# --------------------------------------------------------------- assumptions
def test_assumptions_are_extracted_from_calcs():
    """Brief 9: list out assumptions and make them visibly clear."""
    from calcbook.extract import analyze
    from calcbook.rules import load_rules
    text = ("BEAM DESIGN\nASSUMPTIONS:\n1. Top flange braced by deck\n2. Simple spans\n"
            "Mu = 162 k-ft\nAssume L/360 deflection limit for live load.\n")
    a = analyze([text], load_rules(), [], "GRV")
    got = [x.text for x in a.assumptions]
    assert "Top flange braced by deck" in got and "Simple spans" in got
    assert any("L/360" in t for t in got)
    assert all(x.page == 1 for x in a.assumptions)


def test_project_assumptions_and_calc_assumptions_listed(demo):
    from calcbook.citations import assumptions
    items = assumptions(demo, demo.store.latest())
    sources = {i.source for i in items}
    assert "Design basis" in sources                           # project-level list
    assert any(i.doc_key.startswith("GRV/") for i in items)     # found in a calc


# --------------------------------------------------------------- codes cited
def test_every_code_citation_is_listed(demo):
    """Brief 8: cite all code use."""
    from calcbook.citations import codes_cited
    cites = {(c.family, c.edition): c for c in codes_cited(demo, demo.store.latest())}
    asce22 = cites[("ASCE 7", "22")]
    assert asce22.status == "adopted" and len(asce22.locations) >= 2
    assert cites[("ASCE 7", "16")].status == "different edition"
    assert any(loc[0] == "LAT/LAT-02_Wind" for loc in cites[("ASCE 7", "16")].locations)


# --------------------------------------------------------------- linked book
def _links(page):
    out = []
    for a in page.get("/Annots", []) or []:
        a = a.get_object()
        if a.get("/Subtype") == "/Link":
            out.append(a)
    return out


def test_contents_entries_link_to_their_pages(demo, tmp_path):
    """Brief: table of contents with linked pages."""
    from calcbook.compile import compile_issue
    info = compile_issue(demo, demo.store.latest(), tmp_path / "b.pdf")
    r = PdfReader(str(tmp_path / "b.pdf"))
    toc_links = _links(r.pages[1])
    assert len(toc_links) >= info["documents"]


def test_book_lists_codes_and_assumptions(demo, tmp_path):
    from calcbook.compile import compile_issue
    compile_issue(demo, demo.store.latest(), tmp_path / "b.pdf")
    r = PdfReader(str(tmp_path / "b.pdf"))
    titles = [o.title for o in r.outline if not isinstance(o, list)]
    assert "Codes & assumptions" in titles
    i = titles.index("Codes & assumptions")
    page = r.pages[r.get_destination_page_number(r.outline[i])].extract_text()
    assert "ASCE 7" in page and "Assumption" in page


def test_references_between_calcs_get_current_sheet_numbers(demo, tmp_path):
    """Brief: updated page number references. 'per LAT-03' in FDN-01 is
    stamped with LAT-03's sheet in this book and linked to it."""
    from calcbook.compile import compile_issue, spec_from_issue
    compile_issue(demo, demo.store.latest(), tmp_path / "b.pdf")
    spec = spec_from_issue(demo, demo.store.latest())
    target = next(d for d in spec.docs if d.key.endswith("LAT-03_Braced_Frames"))
    # rebuild numbering exactly as the book did
    from calcbook.compile import number_spec
    number_spec(spec)
    source = next(d for d in spec.docs if d.key.endswith("FDN-01_Spread_Footings"))
    r = PdfReader(str(tmp_path / "b.pdf"))
    page = r.pages[source.start - 1]
    assert f"sh. {target.start}" in page.extract_text()
    assert len(_links(page)) >= 1


# --------------------------------------------------------------- engineering log
def test_engineering_log(tmp_path):
    """Brief 3: comment section for engineers to log changes or issues."""
    from calcbook.store import Store
    from calcbook.tracker import Tracker
    tr = Tracker(Store(tmp_path))
    tr.add_note("lesson", "A. Chen", "Check equipment loads early with the lab planner.", "GRV/GRV-02", "loads")
    tr.add_note("issue", "P. Shah", "Storm drain conflicts with F-1 at D/4.", "", "")
    assert [n["category"] for n in tr.notes()] == ["issue", "lesson"]
    assert len(tr.notes(category="lesson")) == 1
    with pytest.raises(ValueError):
        tr.add_note("lesson", "", "no author", "", "")


# --------------------------------------------------------------- change register
def test_change_resolves_when_affected_documents_are_revised(demo):
    """Brief: design changes (e.g. adding an opening) must be resolved down to
    the calcs and drawings."""
    from calcbook.tracker import Tracker
    tr = Tracker(demo.store)
    cid = tr.add_change("Add 10 in. opening in B-12 web", "Per RFI-001", "RFI",
                        ["GRV/GRV-02_Floor_Beams", "CONN/CONN-01_Beam_Connections"], opened_issue="A")
    ch = next(c for c in tr.changes() if c["id"] == cid)
    items = {i["doc_key"]: i for i in ch["items"]}
    assert items["GRV/GRV-02_Floor_Beams"]["resolved"]                 # revised in Issue B
    assert items["GRV/GRV-02_Floor_Beams"]["resolved_in"] == "B"
    assert not items["CONN/CONN-01_Beam_Connections"]["resolved"]      # never revised
    assert ch["status"] == "open"
    tr.resolve_item(cid, "CONN/CONN-01_Beam_Connections", "Checked — no change needed")
    ch = next(c for c in tr.changes() if c["id"] == cid)
    assert ch["status"] == "resolved"


def test_design_criteria_changes_open_change_items(demo):
    """Brief: changes to design criteria must reach the calcs and drawings."""
    from calcbook.tracker import Tracker, sync_basis_changes
    sync_basis_changes(demo, demo.store.resolve("B"))          # (the demo already ran it once)
    tr = Tracker(demo.store)
    wind = next(c for c in tr.changes() if "wind speed" in c["title"].lower())
    keys = {i["doc_key"]: i for i in wind["items"]}
    assert "LAT/LAT-02_Wind" in keys and not keys["LAT/LAT-02_Wind"]["resolved"]
    assert sync_basis_changes(demo, demo.store.resolve("B")) == []       # idempotent


# --------------------------------------------------------------- congruency
def test_drawings_must_reference_current_specifications(demo):
    """Brief: V3 drawings must reference V3 specifications."""
    from calcbook.checks import run_checks
    f, _ = run_checks(demo, demo.store.resolve("B"))
    stale = [x for x in f if x.check == "stale-reference" and x.package == "DWG"]
    assert stale and "SPEC" in stale[0].message


def test_book_contains_only_calculation_sections(demo, tmp_path):
    from calcbook.compile import spec_from_issue
    spec = spec_from_issue(demo, demo.store.latest())
    assert {s.pkg.kind for s in spec.sections} == {"calcs"}


# --------------------------------------------------------------- submittals / material changes
def test_material_change_requests_are_tracked(tmp_path):
    from calcbook.store import Store
    from calcbook.tracker import SUBMITTAL_KINDS, Tracker
    assert "Material change request" in SUBMITTAL_KINDS
    tr = Tracker(Store(tmp_path))
    tr.save_submittal({"title": "Substitute A325 bolts for A490", "kind": "Material change request"})
    assert tr.submittals()[0]["kind"] == "Material change request"


# --------------------------------------------------------------- code edition toggle
def test_switching_code_edition_updates_checks_and_highlights(tmp_path):
    """Follow-up request: toggle adopted codes; everything affected updates and is
    made visually clear."""
    from calcbook.demo import build
    from calcbook.web import create_app
    ws_root = tmp_path / "ws"
    build(ws_root / "demo", quiet=True, outputs=False)
    c = create_app(ws_root).test_client()
    before = c.get("/p/demo/findings").get_data(as_text=True)
    assert "ASCE 7-16; design basis adopts ASCE 7-22" in before

    r = c.post("/p/demo/codes", data={"family": "ASCE 7", "edition": "16"})
    assert r.status_code == 302 and "changed=ASCE" in r.headers["Location"]
    page = c.get(r.headers["Location"]).get_data(as_text=True)
    assert "flash-row" in page and "is now ASCE 7-16" in page
    assert "update to ASCE 7-16" in page                       # calcs citing 7-22 are now flagged

    findings = c.get("/p/demo/findings").get_data(as_text=True)
    assert "Checking against the current design basis" in findings
    assert "design basis adopts ASCE 7-16" in findings          # LAT-01 etc. now mismatch
    reg = c.get("/p/demo/changes-register").get_data(as_text=True)
    assert "Code change: ASCE 7-22 → ASCE 7-16" in reg

    c.post("/p/demo/codes", data={"family": "ASCE 7", "edition": "22"})    # switch back
    reg = c.get("/p/demo/changes-register").get_data(as_text=True)
    assert "Code change: ASCE 7-22 → ASCE 7-16" not in reg      # withdrawn
