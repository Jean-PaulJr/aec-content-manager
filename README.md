# aec-content-manager — calcbook

A Python tool for structural engineers that

1. **compiles** calculations from multiple sub-packages (gravity, lateral, foundations, connections, delegated design…) into one bookmarked calculation book,
2. **tracks revisions** of every document across issues and shows exactly what changed — documents, pages, design values, member sizes, D/C ratios, and the design basis itself,
3. **cross-checks** every document against a single design basis, and against each other, to confirm design intent is being followed.

## Install

```bash
pip install -e .            # Python 3.10+; installs pypdf, pdfplumber, reportlab, PyYAML, Jinja2
python examples/build_demo.py demo_project   # builds a 4-package demo with Issues A and B
```

## Workflow

```bash
calcbook init my_job && cd my_job       # project.yaml + design_basis.yaml + package folders
# drop each sub-package's PDF calcs into its folder, then:
calcbook ingest A --note "50% CD"       # snapshot every package as Issue A (PDFs archived)
# ... packages get revised ...
calcbook ingest B --note "Permit"       # Issue B
calcbook diff A B                       # what changed
calcbook check                          # design-intent findings (exit code 1 on errors → CI friendly)
calcbook compile                        # output/<job>_Calculations_Issue-B.pdf
calcbook report                         # output/report_Issue-B.html
calcbook history GRV/GRV-02_Floor_Beams
```

`-p path/to/project.yaml` works with every command.

## Files

**project.yaml** — project info and the ordered list of sub-packages (`id`, `title`, `path`, `responsible`).
Package order is the order in the compiled book.

**design_basis.yaml** — the statement of design intent:

```yaml
codes: [CBC 2025, ASCE 7-22, ACI 318-19, AISC 360-22]
parameters:
  sds: {value: 1.20, tolerance: 0.005}
  wind_speed: 95 mph
  fc: {value: 4000 psi, check: at_least}
  soil_bearing: {value: 4000 psf, check: at_most, packages: [FDN], note: "Geotech GR-114"}
dcr_limit: 1.0
dcr_warning: 0.95
```

`check` is `equals` (default), `at_least` or `at_most`; `packages` limits scope. Units are normalised (4 ksi = 4,000 psi, 3 ksf = 3,000 psf, m/s → mph). The basis is captured with each issue, so basis changes are tracked too.

## What gets checked

| Check | Severity | Catches |
|---|---|---|
| basis-mismatch | error | a document's value violates the design basis (SDS, V, f'c, bearing…) |
| code-edition | error | a calc cites ASCE 7-16 when the basis adopts ASCE 7-22 |
| dcr-exceeded / dcr-near-limit | error / note | D/C, DCR, unity or interaction ratios over the limit |
| member-size-mismatch | error | the same mark (B-12) sized differently in two documents |
| stale-reference | warning | "per GRV Rev A" when GRV has since been revised |
| basis-changed | warning | the basis changed but a document using that value wasn't revised |
| inconsistent-value | warning | values that should match everywhere but don't (for items not in the basis) |
| not-addressed, code-not-in-basis, no-text | note / warning | coverage gaps, scanned PDFs needing OCR |

Findings go to the console, `output/findings_<issue>.csv/.json`, and the HTML report.

## Revision tracking

* Each `ingest` stores page text and a SHA-256 of every PDF; the exact PDFs are archived in `.calcbook/archive/` so any past issue can be recompiled.
* A document keeps its identity across issues even when re-saved as `..._RevB.pdf`.
* Changed pages are found by text, ignoring title-block noise ("Rev B", "Sheet 2 of 5"), so a re-issued 40-page calc with one edited page shows one changed page.
* The compiled book has a cover with issue history and package revisions, contents, a revision summary listing changed sheets, a footer on every sheet (`Issue B · Sheet n of N`), and a red **REVISED** / green **NEW** tag on changed sheets.

## Custom rules

Add `rules: rules.yaml` to project.yaml to add or override extraction patterns (`{num}` = a number):

```yaml
parameters:
  ground_snow:
    label: Ground snow load
    dimension: pressure
    default_unit: psf
    patterns: ['\bp\s*_?\s*g\s*[=:]\s*{num}\s*(?P<unit>psf)']
  r_factor: null                    # disable a default rule
codes:
  ACI 350: ['\bACI\s*350\s*-\s*(?P<ed>\d{2})\b']
mark_pattern: '\b[A-Z]{1,3}-?\d{1,3}[A-Z]?\b'
```

Built-in parameters: `fc, rebar_fy, steel_fy, sds, sd1, risk_category, site_class, sdc, r_factor, ie, wind_speed, exposure, floor_live_load, roof_live_load, soil_bearing`.

## Limits

Checks work on the PDF text layer — scanned calcs need OCR first (they are flagged). Pattern matching points a reviewer to places to look; it does not replace engineering review. Software output with unusual formatting may need custom patterns.

## Layout

```
calcbook/  config.py  store.py  ingest.py  extract.py  rules.py  units.py
           history.py diff.py   checks.py  compile.py  report.py cli.py
examples/build_demo.py      tests/test_calcbook.py
```
