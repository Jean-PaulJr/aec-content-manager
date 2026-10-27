"""Demo project: four calculation sub-packages plus a structural drawing set,
issued twice (Issue A, Issue B), with deliberate coordination problems so every
check fires:

  * LAT seismic calc uses SDS = 1.15 (basis: 1.20)
  * LAT wind calc still uses V = 92 mph after the basis moved to 95 mph,
    and cites ASCE 7-16
  * FDN footing uses 4,500 psf bearing (geotech allows 4,000 psf)
  * New slab-on-grade calc uses f'c = 3,000 psi (basis minimum 4,000 psi)
  * Beam B-12 was upsized to W18X35 in GRV and on S-201, but CONN still
    designs a W16X31 connection and cites "GRV Rev A"
  * S-201 shows B-14 as W14X26 while the calc designs W14X22
  * Column C-3 interaction ratio 1.04

The drawing set is re-organised between issues (one file in A, two in B) to
show sheets being matched by sheet number.

    python -m calcbook.demo [target_dir]
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

from .cli import main as calcbook

PROJECT = """\
project:
  name: "Mission Bay Research Lab"
  number: "2026-014"
  client: "Bayfront Science Partners"
  engineer_of_record: "J. Rivera, S.E."

packages:
  - id: GRV
    title: Gravity Framing
    path: packages/gravity
    responsible: "A. Chen"
  - id: LAT
    title: Lateral System
    path: packages/lateral
    responsible: "M. Okafor"
  - id: FDN
    title: Foundations
    path: packages/foundations
    responsible: "P. Shah"
  - id: CONN
    title: Steel Connections
    path: packages/connections
    responsible: "Fabricator's engineer"
  - id: DWG
    title: Structural Drawings
    path: drawings/structural
    kind: drawings
    responsible: "J. Rivera"

basis: design_basis.yaml
output: output
"""

BASIS = """\
codes:
  - CBC 2025
  - ASCE 7-22
  - ACI 318-19
  - AISC 360-22
  - AISC 341-22

parameters:
  risk_category: II
  site_class: D
  sdc: D
  sds: {{value: 1.20, tolerance: 0.005}}
  sd1: {{value: 0.75, tolerance: 0.005}}
  ie: 1.0
  r_factor: {{value: 6, note: "SCBF"}}
  wind_speed: {wind} mph
  exposure: C
  floor_live_load: {{value: 100 psf, check: at_least, note: "laboratory occupancy"}}
  fc: {{value: 4000 psi, check: at_least, note: "minimum for all structural concrete"}}
  rebar_fy: 60 ksi
  steel_fy: 50 ksi
  soil_bearing: {{value: 4000 psf, check: at_most, packages: [FDN], note: "Geotech report GR-114"}}

dcr_limit: 1.0
dcr_warning: 0.95
"""


def calc_pdf(path: Path, doc_no: str, title: str, by: str, rev: str, pages: list[list[str]]) -> None:
    """Draw a simple calc sheet: title block + monospaced body."""
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = letter
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(f"{doc_no} {title}")
    for i, lines in enumerate(pages, 1):
        c.setLineWidth(0.8)
        c.rect(36, h - 96, w - 72, 60)
        c.setFont("Helvetica-Bold", 11)
        c.drawString(46, h - 56, "Mission Bay Research Lab   Job 2026-014")
        c.setFont("Helvetica", 9)
        c.drawString(46, h - 72, f"{doc_no}  {title}")
        c.drawString(46, h - 86, f"By: {by}")
        c.drawRightString(w - 46, h - 56, f"Rev {rev}")
        c.drawRightString(w - 46, h - 72, f"Sheet {i} of {len(pages)}")
        c.setFont("Courier", 9.5)
        y = h - 124
        for line in lines:
            c.drawString(54, y, line)
            y -= 14
        c.showPage()
    c.save()


def issue_a(pk: Path) -> None:
    calc_pdf(pk / "gravity/GRV-01_Design_Criteria.pdf", "GRV-01", "Design Criteria", "A. Chen", "A", [[
        "DESIGN CRITERIA - GRAVITY",
        "Codes: 2025 CBC, ASCE 7-22, ACI 318-19, AISC 360-22",
        "",
        "Floor Live Load = 100 psf (laboratory)",
        "Roof Live Load = 20 psf",
        "Superimposed dead load = 25 psf",
        "",
        "Concrete on metal deck: f'c = 4000 psi",
        "Reinforcing ASTM A615 Grade 60, fy = 60 ksi",
        "Wide flange ASTM A992, Fy = 50 ksi",
    ]])
    calc_pdf(pk / "gravity/GRV-02_Floor_Beams_RevA.pdf", "GRV-02", "Floor Beams", "A. Chen", "A", [[
        "TYPICAL FLOOR BEAMS - LEVEL 2",
        "LL = 100 psf,  span = 30 ft,  trib = 10 ft",
        "",
        "B-12  W16X31  Mu = 162 k-ft   phiMn = 203 k-ft",
        "      DCR = 0.80   deflection L/412  OK",
        "B-14  W14X22  Mu = 88 k-ft    phiMn = 125 k-ft",
        "      DCR = 0.70   OK",
    ], [
        "GIRDER G-4  W24X55",
        "Mu = 410 k-ft   phiMn = 503 k-ft",
        "D/C = 0.82  OK",
    ]])
    calc_pdf(pk / "gravity/GRV-03_Columns.pdf", "GRV-03", "Columns", "A. Chen", "A", [[
        "GRAVITY COLUMNS  per AISC 360-22 Ch. H",
        "C-3  W12X65  Pu = 610 k   phiPn = 690 k",
        "     Interaction = 0.93   OK",
        "C-5  W12X53  Interaction = 0.81   OK",
    ]])
    calc_pdf(pk / "lateral/LAT-01_Seismic_Base_Shear.pdf", "LAT-01", "Seismic Base Shear", "M. Okafor", "A", [[
        "SEISMIC DESIGN PARAMETERS   ASCE 7-22 Ch. 11-12",
        "Risk Category II, Ie = 1.0",
        "Site Class D   (geotech report GR-114)",
        "SDS = 1.15 g     SD1 = 0.75 g",
        "Seismic Design Category D",
        "SCBF:  R = 6   Omega0 = 2   Cd = 5",
        "",
        "Cs = SDS/(R/Ie) = 0.192",
        "V = Cs W = 0.192 x 4,850 k = 931 k",
    ]])
    calc_pdf(pk / "lateral/LAT-02_Wind.pdf", "LAT-02", "Wind Loads", "M. Okafor", "A", [[
        "WIND LOADS - MWFRS directional procedure (ASCE 7-16 Ch. 27)",
        "Risk Category II",
        "Basic wind speed V = 92 mph,  Exposure C",
        "Kzt = 1.0,  Kd = 0.85,  Ke = 1.0",
        "qh = 18.9 psf",
    ]])
    calc_pdf(pk / "lateral/LAT-03_Braced_Frames.pdf", "LAT-03", "Braced Frames", "M. Okafor", "A", [[
        "SPECIAL CONCENTRICALLY BRACED FRAMES   AISC 341-22",
        "BR-1  HSS6X6X3/8   Pu = 182 k   phiPn = 207 k   DCR = 0.88",
        "BR-2  HSS7X7X1/2   DCR = 0.76",
        "Columns per GRV Rev A gravity loads",
    ]])
    calc_pdf(pk / "foundations/FDN-01_Spread_Footings.pdf", "FDN-01", "Spread Footings", "P. Shah", "A", [[
        "SPREAD FOOTINGS   ACI 318-19",
        "Allowable bearing pressure = 4000 psf (GR-114)",
        "f'c = 4000 psi,  fy = 60 ksi",
        "F-1  8'-0\" x 8'-0\" x 24\"   q = 3,650 psf   OK",
        "Column loads per GRV Rev A",
    ]])
    calc_pdf(pk / "foundations/FDN-03_Retaining_Wall.pdf", "FDN-03", "Retaining Wall", "P. Shah", "A", [[
        "CANTILEVER RETAINING WALL - LOADING DOCK",
        "f'c = 4000 psi",
        "qa = 4000 psf",
    ]])
    calc_pdf(pk / "connections/CONN-01_Beam_Connections.pdf", "CONN-01", "Beam Shear Connections",
             "Fabricator's engineer", "A", [[
        "SINGLE-PLATE SHEAR CONNECTIONS   AISC 360-22 Part 10",
        "Beam reactions per GRV Rev A",
        "",
        "B-12  W16X31   Ru = 21.6 k   4 bolts 3/4\" A325-N   DCR = 0.71",
        "B-14  W14X22   Ru = 11.7 k   3 bolts               DCR = 0.48",
    ]])


def issue_b(pk: Path) -> None:
    # GRV-02 re-issued with a new file name; B-12 upsized; extra page added.
    (pk / "gravity/GRV-02_Floor_Beams_RevA.pdf").unlink()
    calc_pdf(pk / "gravity/GRV-02_Floor_Beams_RevB.pdf", "GRV-02", "Floor Beams", "A. Chen", "B", [[
        "TYPICAL FLOOR BEAMS - LEVEL 2   (revised for added lab equipment)",
        "LL = 100 psf,  span = 30 ft,  trib = 10 ft,  equipment 1.5 k pt load",
        "",
        "B-12  W18X35  Mu = 214 k-ft   phiMn = 249 k-ft",
        "      DCR = 0.86   deflection L/455  OK",
        "B-14  W14X22  Mu = 88 k-ft    phiMn = 125 k-ft",
        "      DCR = 0.70   OK",
    ], [
        "GIRDER G-4  W24X55",
        "Mu = 410 k-ft   phiMn = 503 k-ft",
        "D/C = 0.82  OK",
    ], [
        "EQUIPMENT SUPPORT BEAM B-20",
        "B-20  W12X26   Mu = 61 k-ft   phiMn = 112 k-ft",
        "      DCR = 0.97   OK",
    ]])
    calc_pdf(pk / "gravity/GRV-03_Columns.pdf", "GRV-03", "Columns", "A. Chen", "B", [[
        "GRAVITY COLUMNS  per AISC 360-22 Ch. H",
        "C-3  W12X65  Pu = 668 k   phiPn = 690 k   (B-12 upsized)",
        "     Interaction = 1.04   SEE NOTE",
        "C-5  W12X53  Interaction = 0.81   OK",
    ]])
    calc_pdf(pk / "foundations/FDN-01_Spread_Footings.pdf", "FDN-01", "Spread Footings", "P. Shah", "B", [[
        "SPREAD FOOTINGS   ACI 318-19",
        "Allowable bearing pressure = 4500 psf (1/3 increase)",
        "f'c = 4000 psi,  fy = 60 ksi",
        "F-1  8'-0\" x 8'-0\" x 24\"   q = 4,210 psf   OK",
        "Column loads per GRV Rev B",
    ]])
    (pk / "foundations/FDN-03_Retaining_Wall.pdf").unlink()
    calc_pdf(pk / "foundations/FDN-02_Slab_on_Grade.pdf", "FDN-02", "Slab on Grade", "P. Shah", "B", [[
        "SLAB ON GRADE - LAB FLOOR   ACI 360R",
        "6\" slab, f'c = 3000 psi, #4 @ 16\" o.c. each way",
        "Floor Live Load = 125 psf (forklift aisle)",
        "Loads per GRV Rev B",
    ]])


# --------------------------------------------------------------------------- #
# Drawings (11x17 sheets)
# --------------------------------------------------------------------------- #
SHEET = (1224, 792)
GRIDS_X = [90, 300, 510, 720]      # grid lines 1-4
GRIDS_Y = [640, 470, 300, 130]     # grid lines A-D
INK = HexColor("#111111")


def _title_block(c, sheet_no: str, title: str, revs: list[tuple[str, str, str]]) -> None:
    w, h = SHEET
    x = w - 230
    c.setStrokeColor(INK)
    c.setLineWidth(1.2)
    c.rect(24, 24, w - 48, h - 48)
    c.line(x, 24, x, h - 24)
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(x + 14, h - 60, "RIVERA STRUCTURAL")
    c.setFont("Helvetica", 8)
    c.drawString(x + 14, h - 74, "Structural Engineers  ·  San Francisco")
    c.line(x, h - 92, w - 24, h - 92)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 14, h - 118, "Mission Bay Research Lab")
    c.setFont("Helvetica", 8)
    c.drawString(x + 14, h - 132, "Job 2026-014")
    c.line(x, h - 150, w - 24, h - 150)
    c.setFont("Helvetica-Bold", 7)
    c.drawString(x + 14, h - 166, "REV   DATE         DESCRIPTION")
    c.setFont("Helvetica", 7)
    for i, (r, d, desc) in enumerate(revs):
        c.drawString(x + 14, h - 180 - 12 * i, f"{r}      {d}   {desc}")
    c.line(x, 150, w - 24, 150)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + 14, 122, title)
    c.setFont("Helvetica-Bold", 30)
    c.drawString(x + 14, 48, sheet_no)


def _framing_plan(c, rev: str) -> None:
    c.setStrokeColor(INK)
    c.setFillColor(INK)
    c.setLineWidth(0.5)
    c.setDash(6, 3)
    for i, x in enumerate(GRIDS_X):
        c.line(x, 90, x, 690)
        c.circle(x, 706, 11)
        c.setFont("Helvetica-Bold", 10)
        c.drawCentredString(x, 702, str(i + 1))
    for j, y in enumerate(GRIDS_Y):
        c.line(50, y, 770, y)
        c.circle(36 + 2, y, 11)
        c.drawCentredString(38, y - 4, "ABCD"[j])
    c.setDash()
    # columns
    cols = [(x, y) for x in GRIDS_X for y in GRIDS_Y]
    if rev == "B":
        cols.remove((720, 130))          # column at D/4 deleted in B
    for x, y in cols:
        c.rect(x - 6, y - 6, 12, 12, fill=1)
    # girders and beams
    c.setLineWidth(2.2)
    for y in GRIDS_Y:
        c.line(GRIDS_X[0], y, GRIDS_X[-1], y)
    c.setLineWidth(1.2)
    for x0, x1 in zip(GRIDS_X, GRIDS_X[1:]):
        for y0, y1 in zip(GRIDS_Y, GRIDS_Y[1:]):
            for k in (1, 2):
                ym = y0 + (y1 - y0) * k / 3
                c.line(x0 + 6, ym, x1 - 6, ym)
    c.setFont("Helvetica", 8)
    c.drawString(GRIDS_X[0] + 40, 585, "B-12 W18X35" if rev == "B" else "B-12 W16X31")
    c.drawString(GRIDS_X[1] + 40, 585, "B-14 W14X26")
    c.drawString(GRIDS_X[2] + 40, 585, "B-16 W14X22")
    c.drawString(GRIDS_X[0] + 40, 476, "G-4 W24X55")
    c.drawString(GRIDS_X[0] + 8, 650, "C-3 W12X65")
    if rev == "B":
        # new equipment support beam with cloud
        c.setLineWidth(1.6)
        c.line(GRIDS_X[1] + 6, 380, GRIDS_X[2] - 6, 380)
        c.setFont("Helvetica-Bold", 8)
        c.drawString(GRIDS_X[1] + 40, 386, "B-20 W12X26  (EQUIP. SUPPORT)")
        c.setLineWidth(0.8)
        p = c.beginPath()
        p.roundRect(GRIDS_X[1] + 20, 362, 200, 40, 14)
        c.drawPath(p, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(60, 60, "LEVEL 2 FRAMING PLAN   1/8\" = 1'-0\"")
    c.setFont("Helvetica", 7.5)
    c.drawString(60, 46, "TOP OF STEEL EL. +16'-0\" U.N.O.   3 1/4\" LW CONC ON 3\" METAL DECK, f'c = 4000 psi")


def _general_notes(c, wind: int) -> None:
    c.setFillColor(INK)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(60, 720, "GENERAL STRUCTURAL NOTES")
    lines = [
        "1. DESIGN CODES: 2025 CBC, ASCE 7-22, ACI 318-19, AISC 360-22, AISC 341-22.",
        "2. FLOOR LIVE LOAD = 100 psf (LABORATORY).   ROOF LIVE LOAD = 20 psf.",
        f"3. WIND: BASIC WIND SPEED V = {wind} mph, EXPOSURE C, RISK CATEGORY II.",
        "4. SEISMIC: SITE CLASS D, SDS = 1.20, SD1 = 0.75, SEISMIC DESIGN CATEGORY D.",
        "   SCBF, R = 6, Ie = 1.0.",
        "5. CONCRETE: f'c = 4000 psi U.N.O.   REINFORCING ASTM A615 GRADE 60.",
        "6. STRUCTURAL STEEL: WIDE FLANGE ASTM A992, Fy = 50 ksi.  HSS ASTM A500 GR. C.",
        "7. FOUNDATIONS: ALLOWABLE BEARING PRESSURE = 4000 psf PER GEOTECH REPORT GR-114.",
    ]
    c.setFont("Helvetica", 9)
    for i, ln in enumerate(lines):
        c.drawString(60, 690 - 18 * i, ln)


def _foundation_plan(c, rev: str) -> None:
    c.setStrokeColor(INK)
    c.setFillColor(INK)
    c.setLineWidth(0.5)
    c.setDash(6, 3)
    for x in GRIDS_X:
        c.line(x, 90, x, 690)
    for y in GRIDS_Y:
        c.line(50, y, 770, y)
    c.setDash()
    c.setLineWidth(1.2)
    size = 48 if rev == "A" else 54           # footings grow 8'-0" -> 9'-0"
    for x in GRIDS_X:
        for y in GRIDS_Y:
            c.rect(x - size / 2, y - size / 2, size, size)
    c.setFont("Helvetica", 8)
    c.drawString(GRIDS_X[1] + 34, 482, "F-1 8'-0\" SQ x 24\"" if rev == "A" else "F-1 9'-0\" SQ x 24\"")
    c.setFont("Helvetica-Bold", 9)
    c.drawString(60, 60, "FOUNDATION PLAN   1/8\" = 1'-0\"")


def drawing_set(path: Path, sheets: list[str], rev: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    revs = [("A", "2026-08-14", "50% CD")]
    if rev == "B":
        revs.append(("B", "2026-10-23", "PERMIT"))
    c = canvas.Canvas(str(path), pagesize=SHEET)
    c.setTitle(path.stem.replace("_", " "))
    for sid in sheets:
        if sid == "S-001":
            _general_notes(c, 92 if rev == "A" else 95)
            _title_block(c, "S-001", "GENERAL NOTES", revs)
        elif sid == "S-101":
            _foundation_plan(c, rev)
            _title_block(c, "S-101", "FOUNDATION PLAN", revs)
        elif sid == "S-201":
            _framing_plan(c, rev)
            _title_block(c, "S-201", "LEVEL 2 FRAMING", revs)
        c.showPage()
    c.save()


PACKAGES = ["gravity", "lateral", "foundations", "connections"]


def build(target: Path, quiet: bool = False, outputs: bool = True) -> Path:
    """Create the demo project at `target` (replacing it) and ingest Issues A and B."""
    target = Path(target).resolve()
    if target.exists():
        shutil.rmtree(target)
    pk = target / "packages"
    for p in PACKAGES:
        (pk / p).mkdir(parents=True)
    dw = target / "drawings" / "structural"
    (target / "project.yaml").write_text(PROJECT)
    proj = ["-p", str(target / "project.yaml")]

    def run(*argv):
        if quiet:
            import contextlib
            import io
            with contextlib.redirect_stdout(io.StringIO()):
                calcbook([*argv, *proj])
        else:
            calcbook([*argv, *proj])

    (target / "design_basis.yaml").write_text(BASIS.format(wind=92))
    issue_a(pk)
    drawing_set(dw / "Structural_Set_RevA.pdf", ["S-001", "S-101", "S-201"], "A")
    run("ingest", "A", "--note", "50% Construction Documents", "--date", "2026-08-14")

    (target / "design_basis.yaml").write_text(BASIS.format(wind=95))
    issue_b(pk)
    (dw / "Structural_Set_RevA.pdf").unlink()
    drawing_set(dw / "S-001_General_Notes_RevB.pdf", ["S-001"], "B")
    drawing_set(dw / "S-100_Plans_RevB.pdf", ["S-101", "S-201"], "B")
    run("ingest", "B", "--note", "Permit submittal - revised per lab equipment", "--date", "2026-10-23")

    if outputs:
        run("check", "--fail-on", "never")
        run("compile")
        run("report")
    return target


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project"))
