"""Build a demo project with four sub-packages issued twice (Issue A, Issue B)
and run the full calcbook workflow on it.

    python examples/build_demo.py [target_dir]

Issue B contains deliberate coordination problems so every check fires:
  * LAT seismic calc uses SDS = 1.15 (basis: 1.20)
  * LAT wind calc still uses V = 92 mph after the basis moved to 95 mph,
    and cites ASCE 7-16
  * FDN footing uses 4,500 psf bearing (geotech allows 4,000 psf)
  * New slab-on-grade calc uses f'c = 3,000 psi (basis minimum 4,000 psi)
  * Beam B-12 was upsized to W18X35 in GRV, but CONN still designs a W16X31
    connection and cites "GRV Rev A"
  * Column C-3 interaction ratio 1.04
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from calcbook.cli import main as calcbook  # noqa: E402

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


PACKAGES = ["gravity", "lateral", "foundations", "connections"]


def build(target: Path) -> None:
    if target.exists():
        shutil.rmtree(target)
    pk = target / "packages"
    for p in PACKAGES:
        (pk / p).mkdir(parents=True)
    (target / "project.yaml").write_text(PROJECT)
    proj = ["-p", str(target / "project.yaml")]

    print("== Issue A: 50% construction documents ==")
    (target / "design_basis.yaml").write_text(BASIS.format(wind=92))
    issue_a(pk)
    calcbook(["ingest", "A", "--note", "50% Construction Documents", "--date", "2026-08-14", *proj])

    print("\n== Issue B: permit submittal ==")
    (target / "design_basis.yaml").write_text(BASIS.format(wind=95))
    issue_b(pk)
    calcbook(["ingest", "B", "--note", "Permit submittal - revised per lab equipment",
              "--date", "2026-10-23", *proj])

    print("\n== diff A B ==")
    calcbook(["diff", "A", "B", *proj])
    print("\n== check ==")
    calcbook(["check", "--fail-on", "never", *proj])
    print("== compile / report ==")
    calcbook(["compile", *proj])
    calcbook(["compile", "--issue", "A", *proj])
    calcbook(["report", *proj])


if __name__ == "__main__":
    build(Path(sys.argv[1] if len(sys.argv) > 1 else "demo_project").resolve())
