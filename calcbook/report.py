"""Self-contained HTML report: design-intent findings, revision changes,
document register and the design basis in force."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment

from .checks import Finding, counts
from .config import Basis
from .diff import IssueDiff
from .history import Workspace, compress_ranges, register
from .store import Issue
from .units import fmt

TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ project.name }} · Issue {{ issue.label }}</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1d2430;--muted:#6b7280;--rule:#e2e5ea;--accent:#1f4e79;
--err:#b42318;--err-bg:#fdecea;--warn:#a15c07;--warn-bg:#fdf3e1;--info:#1f5f99;--info-bg:#e8f1fa;
--new:#1e7a46;--new-bg:#e6f4ea;--code:#f1f3f6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#12161c;--card:#1a2029;--ink:#e6e9ee;
--muted:#9aa3af;--rule:#2b333f;--accent:#7fb2e5;--err:#f97066;--err-bg:#3a1d1b;--warn:#f5b44a;--warn-bg:#3a2c14;
--info:#7fb2e5;--info-bg:#18293c;--new:#5fcf8c;--new-bg:#16301f;--code:#222a35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 16px 60px}
header{border-top:6px solid var(--accent);background:var(--card);padding:22px 24px;border-radius:0 0 10px 10px;
box-shadow:0 1px 2px rgba(0,0,0,.05)}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);font-weight:600}
h1{margin:4px 0 2px;font-size:24px}h2{font-size:17px;margin:34px 0 12px}
.meta{color:var(--muted);font-size:13px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:10px;margin-top:18px}
.stat{background:var(--card);border:1px solid var(--rule);border-radius:8px;padding:12px 14px}
.stat b{display:block;font-size:22px;font-variant-numeric:tabular-nums}.stat span{color:var(--muted);font-size:12px}
.stat.err b{color:var(--err)}.stat.warn b{color:var(--warn)}.stat.info b{color:var(--info)}
.card{background:var(--card);border:1px solid var(--rule);border-radius:10px;overflow:hidden}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13px}
th{text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);
font-weight:600;padding:9px 12px;border-bottom:1px solid var(--rule);background:var(--card);white-space:nowrap}
td{padding:9px 12px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
.pill{display:inline-block;font-size:11px;font-weight:600;padding:1px 8px;border-radius:999px;white-space:nowrap}
.error{color:var(--err);background:var(--err-bg)}.warning{color:var(--warn);background:var(--warn-bg)}
.info{color:var(--info);background:var(--info-bg)}.new{color:var(--new);background:var(--new-bg)}
.revised{color:var(--err);background:var(--err-bg)}.removed{color:var(--muted);background:var(--code)}
.unchanged{color:var(--muted)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
.ev{color:var(--muted);font-size:12px;margin-top:4px}
.loc{color:var(--muted);font-size:12px}
.toolbar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin-bottom:10px}
.chip{border:1px solid var(--rule);background:var(--card);color:var(--ink);border-radius:999px;padding:4px 12px;
font-size:12px;cursor:pointer}.chip[aria-pressed=true]{background:var(--accent);color:var(--card);border-color:var(--accent)}
input[type=search]{flex:1;min-width:180px;border:1px solid var(--rule);background:var(--card);color:var(--ink);
border-radius:8px;padding:6px 10px;font-size:13px}
details{border-top:1px solid var(--rule)}details:first-child{border-top:0}
summary{cursor:pointer;padding:10px 14px;font-weight:600}
pre{margin:0;padding:12px 14px;background:var(--code);overflow:auto;max-height:420px;font-size:12px;line-height:1.45}
.add{color:var(--new)}.del{color:var(--err)}.hunk{color:var(--info)}
.reg td.c{text-align:center}.empty{padding:16px;color:var(--muted)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}@media(max-width:760px){.grid2{grid-template-columns:1fr}}
footer{margin-top:40px;color:var(--muted);font-size:12px}
</style></head><body><div class="wrap">
<header>
  <div class="eyebrow">Calculation package review</div>
  <h1>{{ project.name }}</h1>
  <div class="meta">Project {{ project.number }}{% if project.client %} · {{ project.client }}{% endif %}
   · <b>Issue {{ issue.label }}</b> ({{ issue.issued_on }}){% if issue.note %} — {{ issue.note }}{% endif %}</div>
  <div class="stats">
    <div class="stat err"><b>{{ c.error }}</b><span>errors</span></div>
    <div class="stat warn"><b>{{ c.warning }}</b><span>warnings</span></div>
    <div class="stat info"><b>{{ c.info }}</b><span>notes</span></div>
    <div class="stat"><b>{{ n_docs }}</b><span>documents</span></div>
    <div class="stat"><b>{{ n_pages }}</b><span>sheets</span></div>
    {% if diff %}<div class="stat"><b>{{ diff.summary.revised + diff.summary.new }}</b><span>changed since {{ diff.old.label }}</span></div>{% endif %}
  </div>
</header>

<h2>Design intent findings</h2>
{% if findings %}
<div class="toolbar">
  <button class="chip" data-f="all" aria-pressed="true">All</button>
  <button class="chip" data-f="error" aria-pressed="false">Errors</button>
  <button class="chip" data-f="warning" aria-pressed="false">Warnings</button>
  <button class="chip" data-f="info" aria-pressed="false">Notes</button>
  <input type="search" id="q" placeholder="Filter by document, member, value…">
</div>
<div class="card scroll"><table id="findings">
<thead><tr><th>ID</th><th>Severity</th><th>Check</th><th>Finding</th><th>Where</th><th>Expected</th><th>Found</th></tr></thead>
<tbody>
{% for f in findings %}
<tr data-sev="{{ f.severity }}">
  <td class="mono" style="white-space:nowrap">{{ f.id }}</td>
  <td><span class="pill {{ f.severity }}">{{ f.severity }}</span></td>
  <td>{{ f.title }}</td>
  <td>{{ f.message }}{% if f.evidence %}<div class="ev mono">“{{ f.evidence }}”</div>{% endif %}</td>
  <td class="loc">{% if f.locations %}{% for l in f.locations %}{{ l }}<br>{% endfor %}{% else %}{{ f.where }}{% endif %}</td>
  <td class="mono">{{ f.expected }}</td><td class="mono">{{ f.found }}</td>
</tr>
{% endfor %}
</tbody></table></div>
{% else %}<div class="card empty">No findings — every document agrees with the design basis.</div>{% endif %}

{% if diff %}
<h2>Changes from Issue {{ diff.old.label }} to Issue {{ diff.new.label }}</h2>
{% if diff.basis_changes %}
<div class="card scroll" style="margin-bottom:14px"><table>
<thead><tr><th>Design basis item</th><th>Issue {{ diff.old.label }}</th><th>Issue {{ diff.new.label }}</th></tr></thead><tbody>
{% for label, a, b in diff.basis_changes %}<tr><td>{{ label }}</td><td class="mono">{{ a }}</td><td class="mono">{{ b }}</td></tr>{% endfor %}
</tbody></table></div>
{% endif %}
<div class="card scroll"><table>
<thead><tr><th>Status</th><th>Document</th><th>Changed pages</th><th>Design values changed</th><th>Members changed</th><th>Max D/C</th></tr></thead><tbody>
{% for ch in changes %}
<tr><td><span class="pill {{ ch.status }}">{{ ch.status }}</span></td>
<td><b>{{ ch.title }}</b><div class="loc mono">{{ ch.doc_key }}</div></td>
<td class="mono">{{ ranges(ch.changed_pages) or ('—' if ch.status != 'revised' else 'layout only') }}{% if ch.pages_dropped %}<div class="loc">{{ ch.pages_dropped }} fewer page(s)</div>{% endif %}</td>
<td>{% for l, a, b in ch.param_changes %}<div>{{ l }}: <span class="mono">{{ a }} → {{ b }}</span></div>{% else %}<span class="loc">—</span>{% endfor %}</td>
<td>{% for m, a, b in ch.member_changes %}<div class="mono">{{ m }}: {{ a }} → {{ b }}</div>{% else %}<span class="loc">—</span>{% endfor %}</td>
<td class="mono">{{ dcr(ch.max_dcr[0]) }} → {{ dcr(ch.max_dcr[1]) }}</td></tr>
{% else %}<tr><td colspan="6" class="empty">No documents changed.</td></tr>{% endfor %}
</tbody></table></div>
{% set diffs = changes | selectattr('text_diff') | list %}
{% if diffs %}
<h2>Text differences</h2>
<div class="card">
{% for ch in diffs %}<details><summary>{{ ch.title }} <span class="loc mono">{{ ch.doc_key }}</span></summary>
<pre class="mono">{{ colorize(ch.text_diff) }}</pre></details>{% endfor %}
</div>
{% endif %}
{% endif %}

<h2>Document register</h2>
<div class="card scroll"><table class="reg">
<thead><tr><th>Package</th><th>Document</th>{% for i in reg_issues %}<th style="text-align:center">{{ i.label }}</th>{% endfor %}<th>Current rev</th></tr></thead><tbody>
{% for r in reg_rows %}<tr><td class="mono">{{ r.package }}</td><td>{{ r.title }}<div class="loc mono">{{ r.doc_key }}</div></td>
{% for cell in r.cells %}<td class="c">{% if cell %}<span class="pill {{ cell }}" title="{{ cell }}">{{ symbols[cell] }}</span>{% endif %}</td>{% endfor %}
<td class="mono">{{ 'withdrawn' if r.cells[-1] in ('', 'removed') else r.current_rev }}</td></tr>{% endfor %}
</tbody></table></div>
<p class="loc">● new · ▲ revised · ○ unchanged · ✕ removed</p>

<h2>Design basis in force for Issue {{ issue.label }}</h2>
<div class="grid2">
<div class="card scroll"><table><thead><tr><th>Parameter</th><th>Requirement</th><th>Scope</th></tr></thead><tbody>
{% for p in basis.params.values() %}<tr><td>{{ p.label }}{% if p.note %}<div class="loc">{{ p.note }}</div>{% endif %}</td>
<td class="mono">{{ p.display }}</td><td class="loc">{{ p.packages|join(', ') or 'all packages' }}</td></tr>
{% else %}<tr><td colspan="3" class="empty">No parameters defined.</td></tr>{% endfor %}
</tbody></table></div>
<div class="card scroll"><table><thead><tr><th>Adopted code / standard</th><th>Edition</th></tr></thead><tbody>
{% for fam, ed in basis.codes.items() %}<tr><td>{{ fam }}</td><td class="mono">{{ ed }}</td></tr>{% endfor %}
<tr><td>Demand / capacity limit</td><td class="mono">{{ '%g' % basis.dcr_limit }}</td></tr>
</tbody></table></div>
</div>

<footer>Generated by calcbook on {{ now }}. Findings are produced by pattern matching on document text;
they point a reviewer to places to look and do not replace engineering review.</footer>
</div>
<script>
(function(){
  var rows=[].slice.call(document.querySelectorAll('#findings tbody tr'));
  var chips=[].slice.call(document.querySelectorAll('.chip'));var q=document.getElementById('q');var sev='all';
  function apply(){var t=(q&&q.value||'').toLowerCase();rows.forEach(function(r){
    var ok=(sev==='all'||r.dataset.sev===sev)&&(!t||r.textContent.toLowerCase().indexOf(t)>-1);
    r.style.display=ok?'':'none';});}
  chips.forEach(function(c){c.addEventListener('click',function(){sev=c.dataset.f;
    chips.forEach(function(x){x.setAttribute('aria-pressed',x===c?'true':'false')});apply();});});
  if(q)q.addEventListener('input',apply);
})();
</script>
</body></html>
"""


def _colorize(text: str):
    from markupsafe import Markup, escape
    out = []
    for line in text.splitlines():
        e = escape(line)
        if line.startswith("+") and not line.startswith("+++"):
            out.append(Markup('<span class="add">') + e + Markup("</span>"))
        elif line.startswith("-") and not line.startswith("---"):
            out.append(Markup('<span class="del">') + e + Markup("</span>"))
        elif line.startswith("@@"):
            out.append(Markup('<span class="hunk">') + e + Markup("</span>"))
        else:
            out.append(e)
    return Markup("\n").join(out)


def render_report(ws: Workspace, issue: Issue, findings: list[Finding], basis: Basis,
                  diff: IssueDiff | None, out_path: Path, include_unchanged: bool = False) -> Path:
    env = Environment(autoescape=True)
    tmpl = env.from_string(TEMPLATE)
    docs = ws.store.documents(issue)
    reg_issues, reg_rows = register(ws.store, issue)
    order = {pid: i for i, pid in enumerate(ws.project.package_ids)}
    reg_rows.sort(key=lambda r: (order.get(r.package, 999), r.doc_key))
    changes = []
    if diff:
        changes = [c for c in diff.changes if include_unchanged or c.status != "unchanged"]
    html = tmpl.render(
        project=ws.project, issue=issue, findings=findings, c=counts(findings), basis=basis,
        n_docs=len(docs), n_pages=sum(d.n_pages for d in docs), diff=diff, changes=changes,
        reg_issues=reg_issues, reg_rows=reg_rows,
        symbols={"new": "●", "revised": "▲", "unchanged": "○", "removed": "✕"},
        ranges=compress_ranges, colorize=_colorize,
        dcr=lambda v: "—" if v is None else f"{v:g}", fmt=fmt,
        now=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
